"""MCP transport 归一化测试（FW-10）。"""

from easy_agent.services.mcp import normalize_servers_mapping


def test_url_only_defaults_to_streamable_http():
    out = normalize_servers_mapping({"remote": {"url": "http://mcp.local/mcp"}})
    assert out["remote"]["transport"] == "streamable_http"


def test_command_defaults_to_stdio():
    out = normalize_servers_mapping({"local": {"command": "python", "args": ["s.py"]}})
    assert out["local"]["transport"] == "stdio"


def test_transport_aliases_are_normalized():
    out = normalize_servers_mapping(
        {
            "a": {"type": "streamable-http", "url": "http://x"},
            "b": {"transport": "http", "url": "http://y"},
            "c": {"type": "sse", "url": "http://z"},
        }
    )
    assert out["a"]["transport"] == "streamable_http"
    assert out["b"]["transport"] == "streamable_http"
    assert out["c"]["transport"] == "sse"


def test_explicit_transport_wins_over_url_default():
    out = normalize_servers_mapping(
        {"legacy": {"url": "http://legacy", "transport": "sse"}}
    )
    assert out["legacy"]["transport"] == "sse"


def test_nested_wrapper_is_expanded():
    out = normalize_servers_mapping(
        {"mcpServers": {"remote": {"url": "http://mcp.local/mcp"}}}
    )
    assert "mcpServers" not in out
    assert out["remote"]["transport"] == "streamable_http"
