"""MCP 配置解析测试：标准 mcpServers 格式支持与 type→transport 归一化。"""

import json

from easy_agent.api.connectors import _extract_servers
from easy_agent.services import mcp as mcp_mod


def test_extract_servers_standard_mcpservers_format():
    """支持标准 Claude Desktop 格式 {"mcpServers": {...}}，type 归一化为 transport。"""
    sample = {
        "mcpServers": {
            "akshare-mcp": {
                "type": "sse",
                "url": "http://127.0.0.1:8005/sse",
                "timeout": 60,
                "description": "AkShare金融数据MCP服务器",
            }
        }
    }
    out = _extract_servers(sample)
    assert "akshare-mcp" in out
    cfg = out["akshare-mcp"]
    assert cfg["transport"] == "sse"
    assert cfg["url"] == "http://127.0.0.1:8005/sse"
    assert cfg["timeout"] == 60
    assert "type" not in cfg


def test_extract_servers_infers_stdio_transport_from_command():
    """标准 Claude 格式的 stdio 服务（command/args，无 type）应推断 transport=stdio。"""
    sample = {
        "mcpServers": {
            "mysql": {
                "command": "uv",
                "args": ["--directory", "path/to/mysql_mcp_server", "run", "mysql_mcp_server"],
                "env": {"MYSQL_HOST": "localhost"},
            }
        }
    }
    out = _extract_servers(sample)
    assert out["mysql"]["transport"] == "stdio"
    assert out["mysql"]["command"] == "uv"


def test_extract_servers_legacy_formats_still_work():
    assert _extract_servers(
        {"servers": {"y": {"type": "stdio", "command": "c"}}}
    ) == {"y": {"transport": "stdio", "command": "c"}}
    assert _extract_servers(
        {"name": "x", "type": "http", "url": "http://a"}
    ) == {"x": {"transport": "http", "url": "http://a"}}
    assert _extract_servers(
        {"myserver": {"transport": "stdio", "command": "npx"}}
    ) == {"myserver": {"transport": "stdio", "command": "npx"}}


def test_load_mcp_config_accepts_mcpservers_key(tmp_path, monkeypatch):
    cfg_path = tmp_path / "mcp.json"
    cfg_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "akshare-mcp": {
                        "type": "sse",
                        "url": "http://127.0.0.1:8005/sse",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_mod, "_find_mcp_config", lambda: cfg_path)
    loaded = mcp_mod.load_mcp_config()
    assert "akshare-mcp" in loaded
    assert loaded["akshare-mcp"]["transport"] == "sse"
    assert loaded["akshare-mcp"]["url"] == "http://127.0.0.1:8005/sse"


def test_normalize_servers_mapping_unwraps_polluted_mcpservers():
    """旧版误存的 {"servers": {"mcpServers": {"akshare-mcp": {...}}}} 应被展开。"""
    polluted = {
        "docs-langchain": {"url": "https://docs.langchain.com/mcp", "transport": "http"},
        "mcpServers": {
            "akshare-mcp": {
                "type": "sse",
                "url": "http://127.0.0.1:8005/sse",
                "timeout": 60,
            }
        },
    }
    out = mcp_mod.normalize_servers_mapping(polluted)
    assert set(out.keys()) == {"docs-langchain", "akshare-mcp"}
    assert out["akshare-mcp"]["transport"] == "sse"
    assert "type" not in out["akshare-mcp"]


def test_stdio_precheck_reports_missing_workdir():
    """stdio 服务的 --directory 路径不存在时应给出明确中文提示。"""
    cfg = {
        "command": "uv",
        "args": ["--directory", "path/to/mysql_mcp_server", "run", "mysql_mcp_server"],
        "transport": "stdio",
    }
    hint = mcp_mod._stdio_precheck(cfg)
    assert "工作目录不存在" in hint
    assert "path/to/mysql_mcp_server" in hint


def test_unpack_error_flattens_exception_group():
    eg = ExceptionGroup(
        "unhandled errors in a TaskGroup",
        [FileNotFoundError(2, "No such file or directory")],
    )
    assert "No such file or directory" in mcp_mod._unpack_error(eg)
