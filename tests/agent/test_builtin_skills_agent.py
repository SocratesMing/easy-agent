"""DA-13 S2：真实 EasyAgent 建图（假模型，不联网）。

- 技能源固定为 ``/skills/builtin/``（只读、按启用集合过滤）+ ``/user-skills/``，同名时用户技能
  覆盖公共技能；停用 / 没添加的公共技能不出现在技能列表里；
- ``save_expert`` 由 expert-creator 技能带出：不在 agent 工具列表里，模型读到（或 pin 了）
  ``SKILL.md`` 之后的那次模型调用才绑定；没读就调用不会执行；开着审批时照常先审批。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import tool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from pydantic import Field

import easy_agent.agent.core as agent_mod
from easy_agent.agent import EasyAgent
from easy_agent.agent import builtin_skills as store
from easy_agent.agent.graph_cache import reset_agent_graph_cache
from easy_agent.agent.run_config import build_run_config
from easy_agent.agent.turn_context import turn_input
from easy_agent.bootstrap.config import Config

REPO_SKILLS = Path(__file__).resolve().parents[2] / "skills"
EXPERT_CREATOR_PATH = "/skills/builtin/expert-creator/SKILL.md"


def _tool_name(item) -> str:
    if isinstance(item, dict):
        return item.get("name") or (item.get("function") or {}).get("name", "")
    return getattr(item, "name", "")


class _ScriptedModel(GenericFakeChatModel):
    """按脚本回复；记录每次模型调用绑定的工具名与看到的消息。"""

    bound: list = Field(default_factory=list)
    seen: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self.bound.append({_tool_name(t) for t in tools})
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs).generations[0].message
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=message.content,
                id=message.id,
                tool_call_chunks=[
                    {"name": tc["name"], "args": json.dumps(tc["args"]), "id": tc["id"], "index": i}
                    for i, tc in enumerate(message.tool_calls or [])
                ],
            )
        )


def _call(name: str, args: dict, call_id: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _skill(root: Path, name: str, description: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\nbody of {name}\n", encoding="utf-8"
    )


@pytest.fixture()
def setup(tmp_path, monkeypatch):
    """canonical 公共技能目录 + 用户 u1 的清单（docx 启用、xlsx 停用、pdf 未添加）与 docx fork。"""
    canonical = tmp_path / "skills"
    _skill(canonical, "docx", "BUILTIN-DOCX-DESC")
    _skill(canonical, "xlsx", "BUILTIN-XLSX-DESC")
    _skill(canonical, "pdf", "BUILTIN-PDF-DESC")
    # 真实的 expert-creator（frontmatter 里的 metadata.include_tools: save_expert）
    shutil.copytree(REPO_SKILLS / "expert-creator", canonical / "expert-creator")

    cfg = Config.load()
    cfg.agent.workspace_dir = str(tmp_path / "workspace")
    cfg.agent.memories_dir = str(tmp_path / "memories")
    cfg.tools.skills_dir = str(canonical)
    for name in ("docx", "xlsx", "expert-creator"):
        store.add("u1", name, canonical, cfg)
    store.set_enabled("u1", "xlsx", False, cfg)
    _skill(tmp_path / "workspace" / "u1" / "skills", "docx", "USER-FORK-DOCX-DESC")

    reset_agent_graph_cache()
    monkeypatch.setenv("EASY_AGENT_GRAPH_CACHE", "1")
    saver = MemorySaver()
    monkeypatch.setattr(agent_mod, "_shared_graph_checkpointer", lambda: saver)
    yield cfg
    reset_agent_graph_cache()


@pytest.fixture()
def build(setup, monkeypatch):
    saved: list[str] = []

    @tool("save_expert")
    def fake_save_expert(name: str) -> str:
        """Save the expert configuration."""
        saved.append(name)
        return "saved"

    def _build(script, *, enable_hitl: bool = False, tools=(fake_save_expert,)):
        reset_agent_graph_cache()
        model = _ScriptedModel(messages=iter(script))
        monkeypatch.setattr(agent_mod, "create_model", lambda *a, **k: model)
        agent = EasyAgent(
            config=setup,
            system_prompt="P",
            username="u1",
            session_id="s1",
            workspace_name="w1",
            mcp_tools=list(tools),
            enable_hitl=enable_hitl,
        )
        return agent, model

    _build.saved = saved
    return _build


async def _run(agent, payload, thread_id: str = "s1"):
    config = build_run_config(agent, thread_id)
    await agent.agent.ainvoke(payload, config=config, context=agent.session_context)
    return await agent.agent.aget_state(config)


def _turn(text: str = "go", *, pinned=None):
    return turn_input([HumanMessage(content=text, id="m1")], message_id="m1", rendered=text, pinned_skills=pinned)


def _text(messages) -> str:
    return "\n".join(str(message.content) for message in messages)


# ── 技能源 ────────────────────────────────────────────────────────────


async def test_skill_list_merges_enabled_builtins_with_user_skills(build):
    agent, model = build([AIMessage(content="ok")])
    assert agent._resolve_skills_paths() == ["/skills/builtin/", "/user-skills/"]

    await _run(agent, _turn())
    prompt = _text(model.seen[0])

    assert "USER-FORK-DOCX-DESC" in prompt  # 同名时用户技能覆盖公共技能
    assert "/user-skills/docx/SKILL.md" in prompt
    assert "BUILTIN-DOCX-DESC" not in prompt
    assert "BUILTIN-XLSX-DESC" not in prompt  # 已停用
    assert "BUILTIN-PDF-DESC" not in prompt  # 没添加
    assert EXPERT_CREATOR_PATH in prompt


async def test_reading_a_disabled_builtin_skill_fails(build):
    agent, _ = build(
        [_call("read_file", {"file_path": "/skills/builtin/xlsx/SKILL.md"}, "r1"), AIMessage(content="ok")]
    )
    state = await _run(agent, _turn())
    result = next(m for m in state.values["messages"] if isinstance(m, ToolMessage))
    # 与真不存在的文件报错一样（不暴露没启用的公共技能是否存在）
    assert "not found" in str(result.content)
    assert "body of xlsx" not in str(result.content)


# ── save_expert 由 expert-creator 带出 ─────────────────────────────────


async def test_save_expert_is_bound_only_after_expert_creator_is_read(build):
    agent, model = build(
        [
            _call("read_file", {"file_path": EXPERT_CREATOR_PATH}, "r1"),
            _call("save_expert", {"name": "macro"}, "c1"),
            AIMessage(content="done"),
        ]
    )
    await _run(agent, _turn())

    assert "save_expert" not in model.bound[0]
    assert "save_expert" in model.bound[1]
    assert build.saved == ["macro"]


async def test_save_expert_called_before_the_skill_is_read_does_not_run(build):
    agent, model = build([_call("save_expert", {"name": "macro"}, "c1"), AIMessage(content="done")])
    state = await _run(agent, _turn())

    assert build.saved == []
    assert all("save_expert" not in names for names in model.bound)
    result = next(m for m in state.values["messages"] if isinstance(m, ToolMessage))
    assert result.status == "error"  # 当作未知工具


async def test_pinned_expert_creator_binds_save_expert_on_the_first_call(build):
    agent, model = build([AIMessage(content="ok")])
    await _run(agent, _turn(pinned=["expert-creator"]))
    assert "save_expert" in model.bound[0]


async def test_save_expert_still_waits_for_approval(build):
    agent, model = build(
        [
            _call("read_file", {"file_path": EXPERT_CREATOR_PATH}, "r1"),
            _call("save_expert", {"name": "macro"}, "c1"),
            AIMessage(content="done"),
        ],
        enable_hitl=True,
    )
    state = await _run(agent, _turn())
    actions = [
        request["name"]
        for interrupt in state.interrupts
        for request in interrupt.value["action_requests"]
    ]
    assert actions == ["save_expert"]
    assert build.saved == []

    await _run(agent, Command(resume={"decisions": [{"type": "approve"}]}))
    assert build.saved == ["macro"]


async def test_sessions_without_save_expert_need_no_skill_tool_wiring(build):
    """专家会话不注入 save_expert：不替换框架默认的 SkillsMiddleware，技能照常可读。"""
    agent, model = build(
        [_call("read_file", {"file_path": EXPERT_CREATOR_PATH}, "r1"), AIMessage(content="ok")],
        tools=(),
    )
    await _run(agent, _turn())
    assert all("save_expert" not in names for names in model.bound)
    assert "专家创建器" in _text(model.seen[1])  # 读到的 SKILL.md 正文


# ── S2 之后的回归（docs/design/2026-10-20-da13-skills-sources.md §15） ──────────


@pytest.mark.parametrize("skills_dir", ["", "/nonexistent/public-skills"])
async def test_agent_builds_without_a_public_skills_dir(setup, build, skills_dir):
    """没配公共技能目录时只有用户技能一个源；e27dae0 起这里曾抛 TypeError，所有对话失败。"""
    setup.tools.skills_dir = skills_dir
    agent, model = build([AIMessage(content="ok")])

    assert agent.builtin_skills_dir is None
    assert agent._resolve_skills_paths() == ["/user-skills/"]
    state = await _run(agent, _turn())
    assert state.values["messages"][-1].content == "ok"
    assert "USER-FORK-DOCX-DESC" in _text(model.seen[0])


async def test_skill_brings_out_a_prefixed_mcp_tool_by_its_original_name(tmp_path, build):
    """D4：开启 MCP 前缀后工具名是 srv__fetch_data，技能里写的是原名 fetch_data。

    改造前门控把工具摘出了工具列表，但 SkillsMiddleware 按真实名精确匹配，读了技能也带不出。
    """
    calls: list[str] = []

    @tool("srv__fetch_data")
    def fetch(query: str) -> str:
        """Fetch data from the srv MCP server."""
        calls.append(query)
        return "data"

    fetch.metadata = {"mcp_original_name": "fetch_data", "mcp_server": "srv"}
    skills = tmp_path / "workspace" / "u1" / "skills"
    (skills / "fetcher").mkdir(parents=True)
    (skills / "fetcher" / "SKILL.md").write_text(
        "---\nname: fetcher\ndescription: fetch data\nmetadata:\n  include_tools: fetch_data\n---\nuse it\n",
        encoding="utf-8",
    )
    agent, model = build(
        [
            _call("read_file", {"file_path": "/user-skills/fetcher/SKILL.md"}, "r1"),
            _call("srv__fetch_data", {"query": "cpi"}, "c1"),
            AIMessage(content="done"),
        ],
        tools=(fetch,),
    )
    await _run(agent, _turn())

    assert "srv__fetch_data" not in model.bound[0]
    assert "srv__fetch_data" in model.bound[1]
    assert calls == ["cpi"]
