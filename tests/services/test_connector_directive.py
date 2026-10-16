"""「使用连接器[X]」指令：文本解析、名字映射与按连接器注入 MCP 工具。

前端输入框选中连接器后会把 ``使用连接器[展示名]`` 前置到消息文本里，后端据此：

1. 解析出连接器名（可多条、可同名去重）；
2. 映射为 mcp.json 的 server 名（展示名 → 目录 id；自定义连接器直接命中 server 名）；
3. 只给本轮 Agent 注入这些连接器的工具，连接器变化时驱逐缓存的 Agent。

用例完全离线：目录读取 / mcp.json / MCP 客户端 / EasyAgent 全部打桩。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from easy_agent.config import Config
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


async def _get(connectors_sel=None, model_name="deepseek"):
    kwargs = {"model_name": model_name, "enable_web_search": False}
    if connectors_sel is not None:
        kwargs["connector_servers"] = connectors_sel
    return await am.get_or_create_agent_for_session(
        SID, "testuser", "ws-name", **kwargs
    )


async def test_no_connector_selected_loads_all(manager):
    a1 = await _get()
    a2 = await _get()

    assert a1 is a2                       # 未指定连接器：缓存命中，与改动前行为一致
    assert manager == [None]              # server_names=None = 加载全部启用连接器；命中缓存不再加载
    assert am.get_session_connectors(SID) == ()


async def test_selected_connector_limits_tools_and_evicts(manager):
    await _get()
    a2 = await _get(["gaode"])

    assert _FakeAgent.instances[-1] is a2
    assert len(_FakeAgent.instances) == 2          # 连接器变化 -> 驱逐重建
    assert manager == [None, ["gaode"]]
    assert am.get_session_connectors(SID) == ("gaode",)

    a3 = await _get(["gaode"])                     # 同一套连接器：缓存命中
    assert a3 is a2
    assert len(_FakeAgent.instances) == 2
    assert manager == [None, ["gaode"]]


async def test_switching_and_clearing_connectors_rebuilds(manager):
    await _get(["gaode"])
    await _get(["mysql", "gaode"])
    c4 = await _get([])                            # 撤掉全部连接器 -> 回到「注入全部」

    assert _FakeAgent.instances[-1].model_name == "deepseek"
    assert c4 is _FakeAgent.instances[-1]
    assert manager == [["gaode"], ["mysql", "gaode"], None]
    assert am.get_session_connectors(SID) == ()


async def test_hitl_resume_keeps_cached_agent(manager):
    """HITL 恢复不传连接器：不因连接器差异驱逐，保住 checkpointer 的中断态。"""
    a1 = await _get(["gaode"])
    resumed = await am.get_or_create_agent_for_session(
        SID, "testuser", "ws-name", model_name="deepseek"
    )

    assert resumed is a1
    assert manager == [["gaode"]]                  # 未重新加载 MCP 工具


async def test_follow_cache_rebuild_reuses_recorded_connectors(manager):
    """缓存被其他路径驱逐后（如换模型）重建，仍沿用该会话已记录的连接器。"""
    await _get(["gaode"])
    await _get(model_name="other-model")           # 不传连接器 = 跟随缓存

    assert manager == [["gaode"], ["gaode"]]
    assert len(_FakeAgent.instances) == 2


async def test_remove_session_agent_clears_connector_record(manager):
    await _get(["gaode"])
    am.remove_session_agent(SID)

    assert am.get_session_connectors(SID) == ()
