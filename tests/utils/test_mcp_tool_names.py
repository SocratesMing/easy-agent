"""D4：MCP 工具命名与「原始名 → 实际名」解析。

开关 ``EASY_MCP_TOOL_PREFIX`` 默认关闭，关闭时 ``services/mcp.py`` 不会调用这里的
命名函数，行为与旧版一致；本文件只验证命名函数与兼容匹配本身的正确性。
"""

from __future__ import annotations

from langchain_core.tools import tool

from easy_agent.utils.mcp_tool_names import (
    MAX_TOOL_NAME_CHARS,
    original_name,
    prefix_enabled,
    prefixed_name,
    rename_tool,
    resolve_tool_names,
)


def test_prefix_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("EASY_MCP_TOOL_PREFIX", raising=False)
    assert prefix_enabled() is False
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("EASY_MCP_TOOL_PREFIX", value)
        assert prefix_enabled() is True
    monkeypatch.setenv("EASY_MCP_TOOL_PREFIX", "0")
    assert prefix_enabled() is False


def test_prefixed_name_basic():
    assert prefixed_name("github", "create_issue") == "github__create_issue"


def test_prefixed_name_sanitizes_illegal_chars():
    assert prefixed_name("my server", "read.file") == "my_server__read_file"
    assert prefixed_name("a/b", "tool (v2)") == "a_b__tool_v2"


def test_prefixed_name_truncates_long_server_with_hash():
    first = prefixed_name("s" * 40, "tool")
    second = prefixed_name("s" * 41, "tool")
    assert len(first) <= MAX_TOOL_NAME_CHARS
    assert first != second  # 截断后仍要能区分不同 server
    assert first.split("__")[0].endswith(first.split("__")[0][-7:])


def test_prefixed_name_never_exceeds_limit():
    name = prefixed_name("http://very-long-server-name.example.com", "a" * 200)
    assert len(name) <= MAX_TOOL_NAME_CHARS
    assert "__" in name


@tool
def create_issue(title: str) -> str:
    """Create an issue."""
    return title


def test_rename_tool_keeps_original_name_in_metadata():
    renamed = rename_tool(create_issue, "github")
    assert renamed.name == "github__create_issue"
    assert original_name(renamed) == "create_issue"
    assert renamed.metadata["mcp_server"] == "github"
    # 原对象不被修改（model_copy 语义）
    assert create_issue.name == "create_issue"


def test_resolve_tool_names_exact_then_unique_suffix():
    available = ["github__create_issue", "jira__create_issue", "github__list_issues"]
    resolved, unresolved = resolve_tool_names(
        ["github__create_issue", "list_issues", "missing"], available
    )
    assert resolved == {
        "github__create_issue": "github__create_issue",
        "list_issues": "github__list_issues",
    }
    assert unresolved == ["missing"]


def test_resolve_tool_names_ambiguous_suffix_is_unresolved():
    available = ["github__create_issue", "jira__create_issue"]
    resolved, unresolved = resolve_tool_names(["create_issue"], available)
    assert resolved == {}
    assert unresolved == ["create_issue"]


def test_resolve_tool_names_keeps_builtin_names():
    resolved, unresolved = resolve_tool_names(["read_file"], ["read_file", "github__read_file"])
    assert resolved == {"read_file": "read_file"}
    assert unresolved == []
