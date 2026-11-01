"""T6：SessionState 注册表与三种 drop scope 的清理矩阵。

覆盖：
- ``drop`` 的 AGENT / SESSION / USER 三种 scope 各清哪些、保留哪些字段；
- LRU 淘汰会清掉连接器内存缓存，但下次建 Agent 会从 session_runtime 表重新读回；
- ``invalidate_user_agents`` 只清该用户的会话。
"""

from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

import pytest

from easy_agent.bootstrap.config import Config
from easy_agent.services import agent_manager as am
from easy_agent.services import session_runtime


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    monkeypatch.setattr(am, "_sessions", OrderedDict())


class _FakeTool:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeAgent:
    def __init__(self, **kwargs):
        self.model_name = kwargs.get("model_name") or kwargs["config"].active_model
        self.workspace_dir = "/tmp/ws"
        self.system_prompt = kwargs.get("system_prompt", "")
        self.safe_username = Config.sanitize_username(kwargs.get("username", ""))
        self.enable_web_search = bool(kwargs.get("enable_web_search", False))


def _full_state() -> am.SessionState:
    return am.SessionState(
        agent=SimpleNamespace(safe_username="u1"),
        expert_key="expert_a",
        connectors=("srv_a",),
        edit_target="expert_a",
        skill_scope=("docx",),
        turn_skills=["docx"],
        stream_task=SimpleNamespace(done=lambda: True),
        create_lock=SimpleNamespace(),
    )


def test_drop_agent_scope_clears_agent_bound_fields_only():
    am._sessions["s1"] = _full_state()

    am.drop("s1", am.DropScope.AGENT)

    state = am._sessions["s1"]
    assert state.agent is None
    assert state.expert_key == ""
    assert state.edit_target == ""
    assert state.skill_scope is am._SCOPE_UNSPECIFIED
    assert state.turn_skills == []
    # 连接器是用户显式设置 + session_runtime 表的缓存；流任务 / 创建锁随会话
    assert state.connectors == ("srv_a",)
    assert state.stream_task is not None
    assert state.create_lock is not None


def test_drop_session_scope_clears_everything_and_removes_entry():
    am._sessions["s1"] = _full_state()

    am.drop("s1", am.DropScope.SESSION)

    assert "s1" not in am._sessions


def test_drop_user_scope_only_clears_agent_and_expert():
    am._sessions["s1"] = _full_state()

    am.drop("s1", am.DropScope.USER)

    state = am._sessions["s1"]
    assert state.agent is None
    assert state.expert_key == ""
    # 用户级失效不动其余会话状态
    assert state.connectors == ("srv_a",)
    assert state.edit_target == "expert_a"
    assert state.skill_scope == ("docx",)
    assert state.turn_skills == ["docx"]
    assert state.stream_task is not None
    assert state.create_lock is not None


def test_drop_unknown_session_is_noop():
    am.drop("no-such-session", am.DropScope.AGENT)
    assert "no-such-session" not in am._sessions


def _state_with_agent(session_id: str, username: str, **fields) -> am.SessionState:
    state = am.SessionState(
        agent=SimpleNamespace(safe_username=Config.sanitize_username(username)), **fields
    )
    am._sessions[session_id] = state
    return state


def test_invalidate_user_agents_only_clears_that_users_sessions(monkeypatch):
    monkeypatch.setattr(
        am, "get_agent_graph_cache", lambda: SimpleNamespace(invalidate_user=lambda safe: 0)
    )
    alice = _state_with_agent(
        "alice-1", "alice", expert_key="expert_a", connectors=("srv_a",)
    )
    bob = _state_with_agent("bob-1", "bob", expert_key="expert_b", connectors=("srv_b",))

    assert am.invalidate_user_agents("alice") == 1

    assert am.get_session_agent("alice-1") is None
    assert am.get_session_expert("alice-1") == ""
    # 用户级失效保留连接器开关（用户显式选择）
    assert am.get_session_connectors("alice-1") == ("srv_a",)
    # 其他用户的会话原样保留
    assert am.get_session_agent("bob-1") is bob.agent
    assert am.get_session_expert("bob-1") == "expert_b"
    assert am.get_session_connectors("bob-1") == ("srv_b",)
    assert alice.agent is None


def _fake_factories() -> dict:
    def _build(name: str):
        return lambda **kwargs: _FakeTool(name)

    return {
        "access_knowledge_original": lambda tools, **kwargs: tools,
        "create_scheduled_task": _build("create_scheduled_task"),
        "save_expert": _build("save_expert"),
        "read_uploaded_file": _build("read_uploaded_file"),
    }


@pytest.fixture
def manager_env(monkeypatch, tmp_path):
    async def _no_mcp(_user=None, server_names=None):
        return []

    monkeypatch.setattr(am, "EasyAgent", _FakeAgent)
    monkeypatch.setattr(am, "load_mcp_tools_for_user", _no_mcp)
    monkeypatch.setattr(am, "create_web_search_tool", lambda *a, **k: None)
    monkeypatch.setattr(am, "_tool_factories", _fake_factories)
    _fake = SimpleNamespace(
        get_user_by_username=lambda username: None,
        get_session=lambda session_id: None,
        get_session_runtime=lambda session_id: None,
        upsert_session_runtime=lambda *args, **kwargs: None,
    )
    # T3b：agent_manager 改走 db.providers 的领域 repository；测试里整体换成假端口。
    for _provider in (
        "get_session_repository",
        "get_session_runtime_repository",
        "get_user_repository",
    ):
        monkeypatch.setattr(am, _provider, lambda _fake=_fake: _fake)
    monkeypatch.setattr(
        Config,
        "get_user_mcp_path",
        classmethod(lambda cls, username, config=None: Path("/nonexistent/mcp.json")),
    )
    config = SimpleNamespace(
        active_model="model-x",
        agent=SimpleNamespace(workspace_dir=str(tmp_path)),
        expert_tags=None,
        file_context=SimpleNamespace(enabled=False),
    )
    monkeypatch.setattr(
        am,
        "_agent_config",
        {
            "config": config,
            "system_prompt": "base prompt",
            "skills_root": "",
            "agent_env": "test",
            "win": False,
        },
    )
    return config


async def test_lru_eviction_reloads_connectors_from_db(manager_env, monkeypatch):
    """LRU 淘汰清掉连接器内存缓存；重建 Agent 时从 session_runtime 表重新读回。"""
    for index in range(am._AGENT_CACHE_MAX):
        am._sessions[f"old-{index}"] = am.SessionState(
            agent=SimpleNamespace(model_name="m", safe_username="u"),
            connectors=("srv-from-db",),
        )
    # 无 Agent 的会话不参与 LRU 计数，也不会被淘汰
    am._sessions["aux"] = am.SessionState(connectors=("srv-aux",))

    loaded: list[str] = []

    def fake_load_connectors(db, session_id):
        loaded.append(session_id)
        return ("srv-from-db",)

    monkeypatch.setattr(session_runtime, "load_connectors", fake_load_connectors)

    # 再建一个 Agent 触发 LRU：最久未用的 old-0 被淘汰，连接器缓存被清掉
    await am._get_or_create_agent_unlocked("new-session", "u", "ws")
    assert "old-0" not in am._sessions
    assert am.get_session_connectors("new-session") == ("srv-from-db",)
    assert am.get_session_connectors("aux") == ("srv-aux",)

    # 淘汰后重温该会话：连接器从 session_runtime 表重新读回
    await am._get_or_create_agent_unlocked("old-0", "u", "ws")
    assert "old-0" in loaded
    assert am.get_session_connectors("old-0") == ("srv-from-db",)
    assert am.get_session_agent("old-0") is not None
