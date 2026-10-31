"""接口 /agent/settings 的测试：模型列表、系统提示词、联网搜索状态。

MCP（连接器）接口已迁到 ``/agent/connectors``，用例见 tests/api/test_connectors.py。
"""

import json
from pathlib import Path

import easy_agent.api.settings as settings_api

from easy_agent.services import mcp as mcp_mod


def test_models(client):
    resp = client.get("/agent/settings/models")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "active_model" in data
    # models 为模型配置列表，至少包含一个名为 deepseek 的模型
    models = data["models"]
    assert isinstance(models, list)
    names = [m.get("name") for m in models]
    assert "deepseek" in names
    assert data["active_model"] == "deepseek"


def test_models_hides_models_without_api_key(client, monkeypatch):
    """未配置 API Key（环境变量缺失 → 空串）的模型不应出现在可选列表。

    配置里 api_key 常写 ``${XXX_API_KEY}``，环境变量缺失时解析为空串，
    这类模型选中必然请求失败，必须从下拉里剔除。
    """
    from types import SimpleNamespace

    def _prov(model: str, api_key: str):
        return SimpleNamespace(
            model=model,
            provider="p",
            protocol="openai",
            context_length=1000,
            api_key=api_key,
        )

    cfg = SimpleNamespace(
        models={
            "with-key": _prov("m1", "sk-ok"),
            "no-key": _prov("m2", ""),
            "blank-key": _prov("m3", "   "),
        },
        active_model="with-key",
    )
    monkeypatch.setattr(settings_api, "get_agent_config", lambda: {"config": cfg})

    resp = client.get("/agent/settings/models")
    assert resp.status_code == 200
    data = resp.json()
    assert [m["name"] for m in data["models"]] == ["with-key"]
    assert data["active_model"] == "with-key"


def test_web_search_status_not_configured(client):
    """未配置 api_key（测试配置默认）时 configured=false，前端按钮置灰。"""
    resp = client.get("/agent/settings/web-search")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is True
    assert data["configured"] is False
    assert data["reason"] == "not_configured"
    assert "NoLimit" in data["time_ranges"]
    # 不泄露 api_url / api_key
    assert "api_key" not in data and "api_url" not in data


def test_web_search_status_configured(client, monkeypatch):
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        web_search=SimpleNamespace(
            enabled=True,
            provider="tavily",
            api_url="http://search.example/api/v1/webSearch",
            api_key="ws-key",
            default_time_range="OneWeek",
        )
    )
    monkeypatch.setattr(settings_api, "get_agent_config", lambda: {"config": cfg})

    resp = client.get("/agent/settings/web-search")
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert data["reason"] == ""
    assert data["provider"] == "tavily"
    assert data["default_time_range"] == "OneWeek"


def test_web_search_status_disabled(client, monkeypatch):
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        web_search=SimpleNamespace(
            enabled=False,
            provider="tavily",
            api_url="http://search.example/api/v1/webSearch",
            api_key="ws-key",
            default_time_range="NoLimit",
        )
    )
    monkeypatch.setattr(settings_api, "get_agent_config", lambda: {"config": cfg})

    resp = client.get("/agent/settings/web-search")
    assert resp.status_code == 200
    data = resp.json()
    assert data["enabled"] is False
    assert data["configured"] is False
    assert data["reason"] == "disabled"


def test_main_app_never_imports_mcp_server_subproject():
    """导入隔离：主应用代码不得 import mcp-server 子项目（只通过 HTTP/数据库耦合）。"""
    import re

    banned_patterns = (
        re.compile(r"^\s*(from|import)\s+easy_mcp_server", re.MULTILINE),
        re.compile(r"^\s*(from|import)\s+easy_agent\.mcp_servers", re.MULTILINE),
    )
    src_root = Path(__file__).resolve().parents[2] / "easy_agent"
    offenders = [
        str(p)
        for p in src_root.rglob("*.py")
        if "__pycache__" not in p.parts
        and any(pat.search(p.read_text(encoding="utf-8")) for pat in banned_patterns)
    ]
    assert offenders == [], f"主应用 import 了 MCP 子项目: {offenders}"


def test_mcp_config_expands_environment_placeholders(tmp_path, monkeypatch):
    global_path = tmp_path / "mcp.json"
    global_path.write_text(
        json.dumps(
            {
                "servers": {
                    "env-server": {
                        "transport": "stdio",
                        "command": "echo",
                        "env": {
                            "MCP_PASSWORD": "${MCP_TEST_PASSWORD}",
                            "MCP_HOST": "${MCP_TEST_HOST:-127.0.0.1}",
                        },
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_mod, "_find_mcp_config", lambda: global_path)
    monkeypatch.setenv("MCP_TEST_PASSWORD", "test-password")
    monkeypatch.delenv("MCP_TEST_HOST", raising=False)

    config = mcp_mod.load_mcp_config(None)

    assert config["env-server"]["env"] == {
        "MCP_PASSWORD": "test-password",
        "MCP_HOST": "127.0.0.1",
    }


def test_knowledge_status_reports_enabled_flag(client):
    """侧边栏只看 enabled，不带 Ragflow 地址或密钥。"""
    from easy_agent.app import app

    from easy_agent.knowledges.core.config import KnowledgeConfig

    previous = getattr(app.state, "knowledge_config", None)
    try:
        app.state.knowledge_config = KnowledgeConfig(
            enabled=True, base_url="http://ragflow.local", api_key="test-key"
        )
        enabled = client.get("/agent/settings/knowledge")
        assert enabled.status_code == 200
        assert enabled.json() == {"enabled": True}

        app.state.knowledge_config = KnowledgeConfig(enabled=False)
        disabled = client.get("/agent/settings/knowledge")
        assert disabled.json() == {"enabled": False}
    finally:
        if previous is None:
            delattr(app.state, "knowledge_config")
        else:
            app.state.knowledge_config = previous
