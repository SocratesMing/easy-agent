"""连接器名字解析 + 「会话级连接器开关」注入 MCP 工具。

1. ``parse_connector_directives`` / ``resolve_connector_servers`` 把连接器展示名（含
   旧前端写进消息文本的 ``使用连接器[展示名]`` 指令）映射为 mcp.json 的 server 名 ——
   输入框「+」→ 连接器面板的开关（``/agent/connectors/session``）复用同一套映射；
2. ``agent_manager`` 按会话记录（``set_session_connectors``）决定给 Agent 注入哪些
   连接器的工具：未设置 = 全部启用，空集合 = 全部关闭，变化时驱逐缓存 Agent。

用例完全离线：目录读取 / mcp.json / MCP 客户端 / EasyAgent 全部打桩。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from easy_agent.bootstrap.config import Config
from easy_agent.services import agent_manager as am
from easy_agent.services import connectors, mcp

SID = "session-connectors"

FAKE_CATALOG = [
    {"id": "mysql", "name": "MySQL 数据库", "description": "", "icon": "", "config": {"command": "x"}},
    {"id": "gaode", "name": "高德地图", "description": "", "icon": "", "config": {"command": "y"}},
]


# ── 1. 文本解析 ───────────────────────────────────────────────────────


def test_parse_single_directive():
    names = connectors.parse_connector_directives("使用连接器[MySQL 数据库] 帮我查订单表")
    assert names == ["MySQL 数据库"]


def test_parse_multiple_dedupes_and_accepts_fullwidth_brackets():
    text = "使用连接器[高德地图] 使用连接器【内部知识检索】 使用连接器[高德地图] 规划路线"
    assert connectors.parse_connector_directives(text) == ["高德地图", "内部知识检索"]


def test_parse_ignores_plain_text_and_empty_names():
    assert connectors.parse_connector_directives("我要用连接器，但没选") == []
    assert connectors.parse_connector_directives("使用连接器[] 使用连接器[  ]") == []
    assert connectors.parse_connector_directives("") == []


def test_parse_caps_directive_count():
    text = " ".join(f"使用连接器[c{i}]" for i in range(50))
    names = connectors.parse_connector_directives(text)
    assert len(names) == connectors.MAX_CONNECTOR_DIRECTIVES


# ── 2. 名字 → server 名映射 ───────────────────────────────────────────


@pytest.fixture
def fake_catalog(monkeypatch):
    """用假目录替换公共配置读取，避免依赖 config/mcp.json 的实际内容。"""
    monkeypatch.setattr(connectors, "load_catalog", lambda path=None: FAKE_CATALOG)


def _patch_user_servers(monkeypatch, servers):
    monkeypatch.setattr(
        mcp, "load_user_mcp_config", lambda username, config=None: servers
    )


def test_resolve_maps_display_name_to_catalog_id(monkeypatch, fake_catalog):
    """前端胶囊展示的是目录里的中文名，需映射回 mcp.json 的 key（目录 id）。"""
    _patch_user_servers(monkeypatch, {"mysql": {"command": "x"}})

    servers, unknown = connectors.resolve_connector_servers(["MySQL 数据库"], "u")

    assert servers == ["mysql"]
    assert unknown == []


def test_resolve_keeps_custom_server_name(monkeypatch, fake_catalog):
    _patch_user_servers(monkeypatch, {"内部知识检索": {"command": "z"}})

    servers, unknown = connectors.resolve_connector_servers(
        ["内部知识检索", "MySQL 数据库"], "u"
    )

    assert servers == ["内部知识检索"]      # 自定义连接器直接用 server 名
    assert unknown == ["MySQL 数据库"]       # 未安装的目录连接器不生效，单独回报


def test_resolve_prefers_exact_server_name_over_catalog_display_name(monkeypatch, fake_catalog):
    """用户自建的 server 名与目录展示名撞名时，精确匹配优先。"""
    _patch_user_servers(monkeypatch, {"高德地图": {"command": "custom"}})

    servers, unknown = connectors.resolve_connector_servers(["高德地图"], "u")

    assert servers == ["高德地图"]
    assert unknown == []


def test_parse_message_connectors_end_to_end(monkeypatch, fake_catalog):
    _patch_user_servers(monkeypatch, {"mysql": {"command": "x"}})

    servers, unknown = connectors.parse_message_connectors(
        "使用连接器[MySQL 数据库] 使用连接器[没这个] 帮我看看订单", "u"
    )

    assert servers == ["mysql"]
    assert unknown == ["没这个"]


# ── 3. 只加载选中连接器的工具 ─────────────────────────────────────────


class _FakeMcpClient:
    """按 server 名返回假工具，记录每次实际加载的 server。"""

    loads: list[str] = []
    broken: set[str] = set()

    def __init__(self, config):
        self.config = config

    async def get_tools(self, server_name=None):
        _FakeMcpClient.loads.append(server_name)
        if server_name in _FakeMcpClient.broken:
            raise RuntimeError(f"boom: {server_name}")
        return [
            SimpleNamespace(name=f"{server_name}_tool1"),
            SimpleNamespace(name=f"{server_name}_tool2"),
        ]


@pytest.fixture
def mcp_env(monkeypatch, tmp_path):
    """把 mcp.json 解析与 MCP 客户端都换成假实现。"""
    config_file = tmp_path / "mcp.json"
    config_file.write_text("{}", encoding="utf-8")
    servers = {
        "mysql": {"command": "x"},
        "gaode": {"command": "y"},
        "内部知识检索": {"command": "z"},
    }
    mcp._mcp_tools_cache.clear()
    _FakeMcpClient.loads = []
    _FakeMcpClient.broken = set()
    monkeypatch.setattr(mcp, "_resolve_mcp_config_path", lambda username=None: config_file)
    monkeypatch.setattr(mcp, "load_mcp_config", lambda username=None: dict(servers))
    monkeypatch.setattr(mcp, "MultiServerMCPClient", _FakeMcpClient)
    yield servers
    mcp._mcp_tools_cache.clear()


async def test_filtered_load_only_touches_selected_server(mcp_env):
    tools = await mcp.get_mcp_tools(server_names=["gaode"])

    assert [t.name for t in tools] == ["gaode_tool1", "gaode_tool2"]
    assert _FakeMcpClient.loads == ["gaode"]


async def test_full_load_reuses_cached_servers(mcp_env):
    await mcp.get_mcp_tools(server_names=["gaode"])
    tools = await mcp.get_mcp_tools()

    # 全量加载只补齐未缓存的两个 server，gaode 不再重连
    assert _FakeMcpClient.loads == ["gaode", "mysql", "内部知识检索"]
    assert [t.name for t in tools] == [
        "mysql_tool1", "mysql_tool2",
        "gaode_tool1", "gaode_tool2",
        "内部知识检索_tool1", "内部知识检索_tool2",
    ]


async def test_unknown_server_is_skipped(mcp_env):
    tools = await mcp.get_mcp_tools(server_names=["gaode", "不存在"])

    assert [t.name for t in tools] == ["gaode_tool1", "gaode_tool2"]


async def test_no_matching_server_returns_empty(mcp_env):
    assert await mcp.get_mcp_tools(server_names=["不存在"]) == []


async def test_broken_server_does_not_break_others(mcp_env):
    _FakeMcpClient.broken = {"gaode"}

    tools = await mcp.get_mcp_tools()

    names = [t.name for t in tools]
    assert "mysql_tool1" in names and "内部知识检索_tool1" in names
    assert not any(n.startswith("gaode_") for n in names)


# ── 4. 会话 Agent：按连接器注入 + 变更驱逐 ────────────────────────────


class _FakeAgent:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.model_name = kwargs.get("model_name") or "deepseek"
        self.enable_web_search = bool(kwargs.get("enable_web_search"))
        self.system_prompt = "SYS"
        self.system_prompt_extra = kwargs.get("system_prompt_extra", "")
        self.workspace_dir = "/tmp/ws"
        self.workspace_virtual_path = "/workspace"
        self.safe_username = Config.sanitize_username(kwargs.get("username") or "")
        _FakeAgent.instances.append(self)


@pytest.fixture
def manager(monkeypatch, tmp_path):
    """隔离 agent_manager：只关心「以什么 server_names 加载了 MCP 工具 + 是否重建」。"""
    am._session_agents.clear()
    am._session_expert.clear()
    am._session_connectors.clear()
    _FakeAgent.instances = []
    calls: list[list[str] | None] = []

    async def _record_mcp(_user=None, server_names=None):
        calls.append(server_names)
        return []

    monkeypatch.setattr(am, "EasyAgent", _FakeAgent)
    monkeypatch.setattr(am, "load_mcp_tools_for_user", _record_mcp)
    monkeypatch.setattr(
        am, "get_database", lambda: SimpleNamespace(get_user_by_username=lambda u: None)
    )
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
    yield calls
    am._session_agents.clear()
    am._session_expert.clear()
    am._session_connectors.clear()


async def _get(model_name="deepseek"):
    return await am.get_or_create_agent_for_session(
        SID, "testuser", "ws-name", model_name=model_name, enable_web_search=False
    )


async def test_session_without_switch_loads_all(manager):
    """没动过开关的会话（新建会话默认态）：注入全部启用连接器，且缓存命中不重复加载。"""
    a1 = await _get()
    a2 = await _get()

    assert a1 is a2                       # 未设置开关：缓存命中
    assert manager == [None]              # server_names=None = 加载全部启用连接器
    assert am.get_session_connectors(SID) is None


async def test_session_switch_limits_tools_and_evicts(manager):
    """写入会话开关：立即驱逐缓存 Agent，下次请求只加载选中的连接器。"""
    await _get()
    evicted = am.set_session_connectors(SID, ["gaode"])
    a2 = await _get()

    assert evicted == 1                            # 有缓存 Agent -> 被驱逐
    assert _FakeAgent.instances[-1] is a2
    assert len(_FakeAgent.instances) == 2
    assert manager == [None, ["gaode"]]
    assert am.get_session_connectors(SID) == ("gaode",)

    a3 = await _get()                              # 同一套连接器：缓存命中
    assert a3 is a2
    assert len(_FakeAgent.instances) == 2
    assert manager == [None, ["gaode"]]


async def test_switching_and_clearing_connectors_rebuilds(manager):
    """开关多次变化逐次驱逐重建；全部关闭时传空列表（不再回退成「注入全部」）。"""
    await _get()
    am.set_session_connectors(SID, ["gaode"])
    await _get()
    am.set_session_connectors(SID, ["mysql", "gaode"])
    await _get()
    am.set_session_connectors(SID, [])
    last = await _get()

    assert last is _FakeAgent.instances[-1]
    assert manager == [None, ["gaode"], ["mysql", "gaode"], []]
    assert am.get_session_connectors(SID) == ()


async def test_hitl_resume_keeps_cached_agent(manager):
    """HITL 恢复不重设开关：不因连接器差异驱逐，保住 checkpointer 的中断态。"""
    am.set_session_connectors(SID, ["gaode"])
    a1 = await _get()
    resumed = await am.get_or_create_agent_for_session(
        SID, "testuser", "ws-name", model_name="deepseek"
    )

    assert resumed is a1
    assert manager == [["gaode"]]                  # 未重新加载 MCP 工具


async def test_cache_rebuild_reuses_session_connectors(manager):
    """缓存被其他路径驱逐后（如换模型）重建，仍沿用该会话的开关设置。"""
    am.set_session_connectors(SID, ["gaode"])
    await _get()
    await _get(model_name="other-model")

    assert manager == [["gaode"], ["gaode"]]
    assert len(_FakeAgent.instances) == 2


async def test_remove_session_agent_keeps_connector_record(manager):
    """记忆更新 / 取消生成等场景也会调用 remove_session_agent：会话开关不得被清掉。"""
    am.set_session_connectors(SID, ["gaode"])
    await _get()
    am.remove_session_agent(SID)

    assert am.get_session_connectors(SID) == ("gaode",)


async def test_clear_session_connectors_removes_record(manager):
    """会话被删除时才清掉开关记录（clear_session_connectors）。"""
    am.set_session_connectors(SID, ["gaode"])
    am.clear_session_connectors(SID)

    assert am.get_session_connectors(SID) is None
