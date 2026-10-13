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


def test_load_user_mcp_config_never_falls_back_to_global(tmp_path, monkeypatch):
    """load_user_mcp_config 只认用户自己的 mcp.json，不回退全局。

    连接器页的 installed 判断依赖这个语义：config/mcp.json 同时是全局 mcp.json，
    回退会让公共目录里的条目全被误判成「已安装」。
    """
    from easy_agent.config import Config

    global_file = tmp_path / "global-mcp.json"
    global_file.write_text(
        json.dumps({"servers": {"from-global": {"transport": "stdio", "command": "echo"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(mcp_mod, "_find_mcp_config", lambda: global_file)
    user_path = tmp_path / "user" / "mcp.json"

    # 用户文件不存在 → 空（而不是全局里的 from-global）
    monkeypatch.setattr(Config, "get_user_mcp_path", classmethod(lambda cls, u, c=None: user_path))
    assert mcp_mod.load_user_mcp_config("someone") == {}
    # 全局回退仍然只影响 load_mcp_config
    assert "from-global" in mcp_mod.load_mcp_config("someone")

    # 用户文件存在 → 只返回用户自己的
    user_path.parent.mkdir(parents=True, exist_ok=True)
    user_path.write_text(
        json.dumps({"servers": {"mine": {"transport": "stdio", "command": "echo"}}}),
        encoding="utf-8",
    )
    assert set(mcp_mod.load_user_mcp_config("someone")) == {"mine"}


def test_load_user_mcp_config_survives_broken_json(tmp_path, monkeypatch):
    """用户 mcp.json 损坏时降级为空映射，不抛异常。"""
    from easy_agent.config import Config

    user_path = tmp_path / "mcp.json"
    user_path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(Config, "get_user_mcp_path", classmethod(lambda cls, u, c=None: user_path))

    assert mcp_mod.load_user_mcp_config("someone") == {}


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
