"""D2：编译图按「模型 + 工具集 + 提示词」跨会话复用。

覆盖：
- 同 model/tools/prompt 的两个 session 只调用一次 create_deep_agent；
- 不同 model / 不同 tools / skills 变化不命中；
- backend callable 通过 SessionContext 指向正确 workspace；
- 会话绑定工具在调用时按 context 注入（工具指纹不含 session 字段）；
- 缓存 LRU 驱逐、显式关闭、legacy checkpointer/会话头降级。
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

import easy_agent.agent.core as agent_mod
from easy_agent.agent import EasyAgent
from easy_agent.agent.graph_cache import (
    GraphCache,
    SessionContext,
    contextualize_tools,
    get_agent_graph_cache,
    reset_agent_graph_cache,
    tool_fingerprint,
)
from easy_agent.bootstrap.config import Config


@pytest.fixture(autouse=True)
def _clean_graph_cache(monkeypatch):
    reset_agent_graph_cache()
    monkeypatch.setenv("EASY_AGENT_GRAPH_CACHE", "1")
    yield
    reset_agent_graph_cache()


@pytest.fixture
def config(tmp_path):
    cfg = Config.load()
    cfg.agent.workspace_dir = str(tmp_path / "workspace")
    cfg.agent.memories_dir = str(tmp_path / "memories")
    return cfg


@pytest.fixture
def shared_saver(monkeypatch):
    saver = MemorySaver()
    monkeypatch.setattr(agent_mod, "_shared_graph_checkpointer", lambda: saver)
    return saver


@pytest.fixture
def compile_spy(monkeypatch):
    calls: list[dict] = []

    def fake_create_deep_agent(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(kind="compiled-graph", kwargs=kwargs)

    monkeypatch.setattr(agent_mod, "create_deep_agent", fake_create_deep_agent)
    return calls


def _make_agent(
    cfg: Config,
    *,
    session_id: str,
    workspace_name: str,
    username: str = "u1",
    model_name: str | None = None,
    mcp_tools: list | None = None,
    enable_hitl: bool = False,
    system_prompt: str = "BASE PROMPT",
) -> EasyAgent:
    return EasyAgent(
        config=cfg,
        system_prompt=system_prompt,
        username=username,
        session_id=session_id,
        workspace_name=workspace_name,
        mcp_tools=mcp_tools or [],
        enable_hitl=enable_hitl,
        model_name=model_name,
    )


class _ProbeArgs(BaseModel):
    x: str = ""


class _ProbeTool(BaseTool):
    name: str = "probe"
    description: str = "probe"
    args_schema: type = _ProbeArgs
    username: str = ""
    session_id: str = ""
    workspace_dir: str = ""

    def _run(self, **kwargs):  # noqa: ANN003
        raise NotImplementedError("async only")

    async def _arun(self, x: str = "") -> str:
        return f"{self.username}|{self.session_id}|{self.workspace_dir}|{x}"


def _ctx(tmp_path: Path, *, session_id: str, workspace_name: str) -> SessionContext:
    workspace = tmp_path / "workspace" / "u1" / "session" / workspace_name
    skills = tmp_path / "workspace" / "u1" / "skills"
    return SessionContext(
        session_id=session_id,
        username="u1",
        safe_username="u1",
        workspace_dir=str(workspace),
        workspace_virtual_path="/workspace",
        user_skills_dir=str(skills),
        memory_file=str(workspace / "memory.md"),
        long_term_memory_file=str(tmp_path / "memories" / "u1" / "AGENTS.md"),
        workspace_name=workspace_name,
    )


def test_same_model_tools_prompt_two_sessions_compile_once(
    config, shared_saver, compile_spy
):
    first = _make_agent(config, session_id="s1", workspace_name="w1")
    second = _make_agent(config, session_id="s2", workspace_name="w2")

    assert len(compile_spy) == 1, "同 model/tools/prompt 的第二个 session 应复用编译图"
    assert first.agent is second.agent
    assert first.agent_graph_key == second.agent_graph_key
    assert first.workspace_dir != second.workspace_dir

    kwargs = compile_spy[0]
    assert kwargs["context_schema"] is SessionContext
    from easy_agent.backends.context import ContextBackend

    assert isinstance(kwargs["backend"], ContextBackend), "共享图必须用 context-aware backend 实例"
    assert kwargs["memory"] == [
        "/workspace/memory.md",
        "/memories/AGENTS.md",
        "/workspace/uploads/index.md",
    ]

    stats = get_agent_graph_cache().stats()
    assert stats["builds"] == 1
    assert stats["hits"] == 1


def test_different_model_does_not_hit(config, shared_saver, compile_spy):
    config.models["alt"] = config.models["deepseek"].model_copy(
        update={"model": "alt-model", "api_base": "https://alt.example.com"}
    )
    first = _make_agent(config, session_id="s1", workspace_name="w1")
    second = _make_agent(
        config, session_id="s2", workspace_name="w2", model_name="alt"
    )

    assert len(compile_spy) == 2
    assert first.agent is not second.agent
    assert first.agent_graph_key != second.agent_graph_key


def test_different_tools_do_not_hit(config, shared_saver, compile_spy):
    extra = _ProbeTool()
    first = _make_agent(config, session_id="s1", workspace_name="w1")
    second = _make_agent(
        config, session_id="s2", workspace_name="w2", mcp_tools=[extra]
    )

    assert len(compile_spy) == 2
    assert first.agent is not second.agent


def test_skill_added_changes_key(config, shared_saver, compile_spy):
    first = _make_agent(config, session_id="s1", workspace_name="w1")

    skill_dir = Path(config.agent.workspace_dir) / "u1" / "skills" / "demo"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")

    second = _make_agent(config, session_id="s2", workspace_name="w2")
    assert len(compile_spy) == 2
    assert first.agent is not second.agent


def test_backend_factory_resolves_workspace_from_context(
    config, shared_saver, compile_spy, tmp_path
):
    agent = _make_agent(config, session_id="s1", workspace_name="w1")
    factory = agent._make_backend_factory([])

    ctx = _ctx(tmp_path, session_id="s-other", workspace_name="w-other")
    backend = factory.resolve(ctx)

    assert Path(backend.default.cwd) == Path(ctx.workspace_dir).absolute()
    assert Path(backend.routes["/workspace/"].cwd) == Path(ctx.workspace_dir).absolute()
    assert Path(backend.routes["/user-skills/"].cwd) == Path(
        ctx.user_skills_dir
    ).absolute()
    assert Path(backend.routes["/memories/"].cwd) == Path(
        ctx.long_term_memory_file
    ).parent.absolute()


def test_different_users_do_not_hit(config, shared_saver, compile_spy):
    first = _make_agent(config, session_id="s1", workspace_name="w1", username="u1")
    second = _make_agent(config, session_id="s2", workspace_name="w2", username="u2")
    assert len(compile_spy) == 2
    assert first.agent is not second.agent


def test_backend_factory_rejects_other_user_context(
    config, shared_saver, compile_spy, tmp_path
):
    agent = _make_agent(config, session_id="s1", workspace_name="w1", username="u1")
    factory = agent._make_backend_factory([])
    other = _ctx(tmp_path, session_id="s-other", workspace_name="w-other")
    other = replace(other, username="u2", safe_username="u2")
    with pytest.raises(RuntimeError, match="用户"):
        factory.resolve(other)


def test_shared_graph_without_context_refuses_to_pick_a_session(
    config, shared_saver, compile_spy
):
    agent = _make_agent(config, session_id="s1", workspace_name="w1")
    factory = agent._make_backend_factory([])
    with pytest.raises(RuntimeError, match="SessionContext"):
        factory.resolve(None)


def test_context_bound_tool_is_rewritten_per_runtime(tmp_path):
    original = _ProbeTool(username="old", session_id="old", workspace_dir="/old")
    wrapped = contextualize_tools([original])[0]

    ctx = _ctx(tmp_path, session_id="s-new", workspace_name="w-new")
    result = asyncio.run(
        wrapped.coroutine(runtime=SimpleNamespace(context=ctx), x="hi")
    )
    assert result == f"u1|s-new|{ctx.workspace_dir}|hi"

    # 工具指纹不包含会话字段：两个 session 的工具实例哈希一致
    left = _ProbeTool(username="u1", session_id="s1", workspace_dir="/w1")
    right = _ProbeTool(username="u1", session_id="s2", workspace_dir="/w2")
    assert tool_fingerprint(left) == tool_fingerprint(right)


def test_graph_cache_lru_eviction():
    cache = GraphCache(max_entries=2)
    cache.put("k0", object(), username="u1", label="g0")
    cache.put("k1", object(), username="u1", label="g1")
    cache.put("k2", object(), username="u1", label="g2")

    stats = cache.stats()
    assert stats["entries"] == 2
    assert stats["evictions"] == 1
    assert stats["keys"] == ["k1", "k2"]


def test_cache_disabled_when_header_binds_session(config, shared_saver, compile_spy):
    config.models["deepseek"].headers = {"x-opencode-session": "{session_id}"}
    first = _make_agent(config, session_id="s1", workspace_name="w1")
    second = _make_agent(config, session_id="s2", workspace_name="w2")

    assert len(compile_spy) == 2
    assert first.agent_graph_key is None
    assert second.agent_graph_key is None
    assert get_agent_graph_cache().stats()["disabled"] >= 1


def test_cache_disabled_with_legacy_checkpointer(monkeypatch, config, compile_spy):
    monkeypatch.setattr(agent_mod, "_shared_graph_checkpointer", lambda: None)
    first = _make_agent(config, session_id="s1", workspace_name="w1")
    second = _make_agent(config, session_id="s2", workspace_name="w2")

    assert len(compile_spy) == 2
    assert first.agent_graph_key is None
    assert second.agent_graph_key is None


def test_env_kill_switch_disables_cache(monkeypatch, config, shared_saver, compile_spy):
    monkeypatch.setenv("EASY_AGENT_GRAPH_CACHE", "0")
    first = _make_agent(config, session_id="s1", workspace_name="w1")
    second = _make_agent(config, session_id="s2", workspace_name="w2")

    assert len(compile_spy) == 2
    assert first.agent_graph_key is None
    assert second.agent_graph_key is None
