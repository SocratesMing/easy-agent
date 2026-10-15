"""专家切换导致的会话 Agent 缓存驱逐（agent_manager 的 12 行核心改动）。

这是本次方案里唯一触碰核心链路的改动，必须锁住四个场景：

| 场景 | 预期 |
|---|---|
| 全程通用 | 缓存命中，不重建（与改动前行为一致） |
| 专家 A 连续两次 | 第二次缓存命中 |
| 想 A -> B | 第二次驱逐并重建 |
| A -> 通用 | 驱逐并重建，专家人设不残留 |

用例完全离线：EasyAgent / MCP 加载 / 数据库访问全部打桩。
"""

from types import SimpleNamespace

import pytest

import easy_agent.services.agent_manager as am
from easy_agent.config import Config

SID = "session-abcdef"


class _FakeAgent:
    """记录构造参数，模拟 EasyAgent 上被驱逐判断读取的属性。"""

    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.model_name = kwargs.get("model_name") or "deepseek"
        self.enable_web_search = bool(kwargs.get("enable_web_search"))
        self.system_prompt_extra = kwargs.get("system_prompt_extra", "")
        self.workspace_dir = "/tmp/ws"
        self.workspace_virtual_path = "/workspace"
        # invalidate_user_agents 依赖该属性筛选同用户的缓存 Agent
        self.safe_username = Config.sanitize_username(kwargs.get("username") or "")
        _FakeAgent.instances.append(self)


@pytest.fixture()
def manager(db, monkeypatch, tmp_path):
    """把 agent_manager 的外部依赖全部换成本地假实现。"""
    am._session_agents.clear()
    am._session_expert.clear()
    am._session_connectors.clear()
    _FakeAgent.instances = []

    monkeypatch.setattr(am, "EasyAgent", _FakeAgent)
    monkeypatch.setattr(am, "get_database", lambda: db)

    async def _no_mcp(_user=None, server_names=None):
        return []

    monkeypatch.setattr(am, "load_mcp_tools_for_user", _no_mcp)
    monkeypatch.setattr(
        am,
        "_agent_config",
        {
            "config": SimpleNamespace(
                active_model="deepseek",
                agent=SimpleNamespace(workspace_dir=str(tmp_path)),
            ),
            "system_prompt": "SYS",
            "skills_root": "",
        },
    )
    yield am
    am._session_agents.clear()
    am._session_expert.clear()


async def _get(manager, expert_id=None, prompt=""):
    return await manager.get_or_create_agent_for_session(
        SID,
        "testuser",
        "ws-name",
        model_name="deepseek",
        enable_web_search=False,
        system_prompt_extra=prompt,
        expert_agent_id=expert_id,
    )


# ── 场景 1：全程通用 ──────────────────────────────────────────────────


async def test_generic_chat_reuses_cache(manager):
    a1 = await _get(manager)
    a2 = await _get(manager)

    assert a1 is a2                       # 缓存命中，零重建
    assert len(_FakeAgent.instances) == 1
    assert manager.get_session_expert(SID) == ""


async def test_not_passing_expert_is_same_as_generic(manager):
    """不传 expert_agent_id（老路径）与显式传 None/"" 行为一致。"""
    a1 = await manager.get_or_create_agent_for_session(
        SID, "testuser", "ws-name", model_name="deepseek", enable_web_search=False
    )
    a2 = await _get(manager)
    assert a1 is a2
    assert manager.get_session_expert(SID) == ""


# ── 场景 2：同一专家连续两次 ──────────────────────────────────────────


async def test_same_expert_reuses_cache(manager):
    a1 = await _get(manager, "expert_A", "你是A")
    a2 = await _get(manager, "expert_A", "你是A")

    assert a1 is a2
    assert len(_FakeAgent.instances) == 1
    assert manager.get_session_expert(SID) == "expert_A"
    assert a1.kwargs["system_prompt_extra"] == "你是A"


# ── 场景 3：A -> B ───────────────────────────────────────────────────


async def test_switch_expert_evicts_and_rebuilds(manager):
    a1 = await _get(manager, "expert_A", "你是A")
    a2 = await _get(manager, "expert_B", "你是B")

    assert a1 is not a2
    assert len(_FakeAgent.instances) == 2
    assert a2.kwargs["system_prompt_extra"] == "你是B"
    assert manager.get_session_expert(SID) == "expert_B"


# ── 场景 4：专家 -> 通用（最容易漏测的一条） ──────────────────────────


async def test_switch_back_to_generic_evicts(manager):
    a1 = await _get(manager, "expert_A", "你是A")
    a2 = await _get(manager, None, "")

    assert a1 is not a2
    assert len(_FakeAgent.instances) == 2
    assert a2.kwargs["system_prompt_extra"] == ""
    assert manager.get_session_expert(SID) == ""


async def test_generic_then_expert_evicts(manager):
    """反向：普通聊天后再选专家，专家必须生效。"""
    a1 = await _get(manager, None, "")
    a2 = await _get(manager, "expert_A", "你是A")

    assert a1 is not a2
    assert a2.kwargs["system_prompt_extra"] == "你是A"


# ── 缓存清理 ──────────────────────────────────────────────────────────


async def test_remove_session_agent_clears_expert_record(manager):
    await _get(manager, "expert_A", "你是A")
    assert manager.get_session_expert(SID) == "expert_A"

    manager.remove_session_agent(SID)
    assert manager.get_session_expert(SID) == ""
    assert SID not in am._session_agents

    # 清理后重新获取应该是新建实例
    a2 = await _get(manager, "expert_A", "你是A")
    assert len(_FakeAgent.instances) == 2
    assert a2.kwargs["system_prompt_extra"] == "你是A"


def test_get_session_expert_unknown_session(manager):
    assert manager.get_session_expert("no-such-session") == ""


async def test_invalidate_user_agents_clears_expert_record(manager):
    await _get(manager, "expert_A", "你是A")
    assert manager.get_session_expert(SID) == "expert_A"

    am.invalidate_user_agents("testuser")
    assert manager.get_session_expert(SID) == ""
