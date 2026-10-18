"""批次 3：agent 装配/辅助职责拆分的模块边界测试。

锁定三件事：
1. 旧导入路径（``easy_agent.agent``）与新模块是同一对象（纯 re-export）；
2. workspace/skills/permissions 辅助方法通过 mixin 继承到 ``EasyAgent``，
   且在轻量实例上仍可调用；
3. ``build_agent_middleware`` 是只依赖 ``config``/``max_steps`` 的纯函数，
   返回顺序稳定；``_build_backend_for_context`` 只依赖最小 context 即可调用。
"""

from types import SimpleNamespace

import pytest
from deepagents.backends import CompositeBackend

import easy_agent.agent as agent_mod
import easy_agent.agent_backend as backend_mod
import easy_agent.agent_middleware as middleware_mod
import easy_agent.agent_workspace as workspace_mod
from easy_agent.agent import EasyAgent
from easy_agent.config import Config

MOVED_METHODS = (
    "_load_memory_file",
    "load_session_memory",
    "load_long_term_memory",
    "_get_os_info",
    "_uploads_manifest_enabled",
    "_memory_sources",
    "_safe_external_dirs",
    "_build_permissions",
    "_skill_write_policy",
    "_build_skill_write_permissions",
    "_discover_user_skill_names",
    "_log_user_skills",
    "_read_skill_description",
    "_resolve_skills_paths",
)


def _minimal_context(tmp_path):
    """只带 ``_build_backend_for_context`` 实际读取字段的最小上下文。"""
    return SimpleNamespace(
        workspace_virtual_path="/workspace",
        workspace_dir=str(tmp_path / "workspace" / "alice" / "session" / "s1"),
        user_skills_dir=str(tmp_path / "workspace" / "alice" / "skills"),
        long_term_memory_file=str(tmp_path / "memories" / "alice" / "AGENTS.md"),
        safe_username="alice",
        shell_env={"EASY_USERNAME": "alice"},
        sid="s1",
    )


def _config(tmp_path) -> Config:
    config = Config.load()
    config.agent.workspace_dir = str(tmp_path / "workspace")
    config.agent.memories_dir = str(tmp_path / "memories")
    config.agent.external_dirs = {}
    config.agent.denied_dirs = []
    config.agent.skill_write = "new-only"
    return config


@pytest.fixture
def lightweight_agent(monkeypatch, tmp_path) -> EasyAgent:
    """跳过建图，只保留 workspace/skills 辅助方法所需的属性。"""
    monkeypatch.setattr(EasyAgent, "_create_agent", lambda self: None)
    return EasyAgent(
        config=_config(tmp_path),
        system_prompt="BASE PROMPT",
        username="alice",
        session_id="sess-agent-modules",
    )


# ── re-export 身份 ───────────────────────────────────────────


def test_backend_builder_is_reexported_same_object():
    assert (
        agent_mod._build_backend_for_context
        is backend_mod._build_backend_for_context
    )


def test_long_term_memory_constant_is_reexported_same_object():
    assert (
        agent_mod.LONG_TERM_MEMORY_VIRTUAL_PATH
        is workspace_mod.LONG_TERM_MEMORY_VIRTUAL_PATH
    )
    assert agent_mod.LONG_TERM_MEMORY_VIRTUAL_PATH == "/memories/AGENTS.md"


def test_middleware_builder_is_reexported_same_object():
    assert agent_mod.build_agent_middleware is middleware_mod.build_agent_middleware


def test_moved_symbols_are_defined_in_new_modules():
    assert (
        backend_mod._build_backend_for_context.__module__
        == "easy_agent.agent_backend"
    )
    assert (
        middleware_mod.build_agent_middleware.__module__
        == "easy_agent.agent_middleware"
    )
    for name in MOVED_METHODS:
        assert (
            getattr(workspace_mod.AgentWorkspaceMixin, name).__module__
            == "easy_agent.agent_workspace"
        ), name


def test_easyagent_inherits_workspace_mixin_methods():
    assert issubclass(EasyAgent, workspace_mod.AgentWorkspaceMixin)
    for name in MOVED_METHODS:
        assert getattr(EasyAgent, name) is getattr(
            workspace_mod.AgentWorkspaceMixin, name
        ), name


# ── mixin 方法在 EasyAgent 上可调用 ──────────────────────────


def test_mixin_helpers_callable_on_easyagent(lightweight_agent: EasyAgent):
    agent = lightweight_agent
    assert agent._get_os_info().startswith("## OS: ")
    assert agent._discover_user_skill_names() == []
    assert agent._resolve_skills_paths() == []
    assert agent._safe_external_dirs() == {}
    assert agent._skill_write_policy() == "new-only"
    assert isinstance(agent._build_permissions([], {}), list)
    assert isinstance(agent._uploads_manifest_enabled(), bool)
    assert agent._read_skill_description(agent.workspace_dir) == ""


def test_mixin_memory_helpers_on_empty_workspace(lightweight_agent: EasyAgent):
    agent = lightweight_agent
    sources = agent._memory_sources()
    assert f"{agent.workspace_virtual_path}/memory.md" in sources
    assert workspace_mod.LONG_TERM_MEMORY_VIRTUAL_PATH in sources
    # 首轮：会话记忆/长期记忆文件都不存在 → 空字符串
    assert agent.load_session_memory() == ""
    assert agent.load_long_term_memory() == ""


# ── build_agent_middleware 纯函数 ────────────────────────────


def test_build_agent_middleware_type_and_order():
    config = SimpleNamespace(tools=SimpleNamespace(result_log_truncate=321))
    middleware = middleware_mod.build_agent_middleware(config, 7)
    assert [type(item).__name__ for item in middleware] == [
        "TodoListMiddleware",
        "LoggingMiddleware",
        "ModelCallLimitMiddleware",
        "ToolCallLimitMiddleware",
    ]


def test_easyagent_build_middleware_delegates(monkeypatch, lightweight_agent):
    captured: dict = {}

    def fake_builder(config, max_steps):
        captured["config"] = config
        captured["max_steps"] = max_steps
        return ["stub-middleware"]

    monkeypatch.setattr(agent_mod, "build_agent_middleware", fake_builder)

    assert lightweight_agent._build_middleware() == ["stub-middleware"]
    assert captured["config"] is lightweight_agent.config
    assert captured["max_steps"] == lightweight_agent.max_steps


# ── _build_backend_for_context 纯函数（最小 context） ─────────


def test_backend_builder_works_with_minimal_fake_context(tmp_path, monkeypatch):
    monkeypatch.delenv("EASY_SANDBOX", raising=False)
    config = _config(tmp_path)

    backend = backend_mod._build_backend_for_context(
        config, _minimal_context(tmp_path), []
    )

    assert isinstance(backend, CompositeBackend)
    assert set(backend.routes) == {
        "/workspace/",
        "/memories/",
        "/user-skills/",
    }
