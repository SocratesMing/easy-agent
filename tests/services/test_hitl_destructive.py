from types import SimpleNamespace

from easy_agent.hitl import (
    EXECUTE_HITL_TOOL_DESCRIPTION,
    _is_destructive_command,
    _is_directory_deletion,
    _is_knowledge_materialize,
    looks_like_mutating_tool,
)


def test_execute_description_mentions_sandbox():
    assert "bwrap" in EXECUTE_HITL_TOOL_DESCRIPTION
    assert "fail-closed" in EXECUTE_HITL_TOOL_DESCRIPTION
    assert "宿主机直接执行" not in EXECUTE_HITL_TOOL_DESCRIPTION


def test_file_rm_is_destructive_dir_rm_is_not():
    req = SimpleNamespace(tool_call={"args": {"command": "rm /workspace/a.txt"}})
    assert _is_destructive_command(req) is True
    assert _is_directory_deletion("rm -r /workspace/dir") is True
    req_dir = SimpleNamespace(tool_call={"args": {"command": "rm -r /workspace/dir"}})
    assert _is_destructive_command(req_dir) is False


def test_mutating_mcp_and_knowledge_materialize():
    assert looks_like_mutating_tool("mysql_execute_sql") is True
    assert looks_like_mutating_tool("read_file") is False
    req = SimpleNamespace(tool_call={"args": {"action": "materialize", "document_id": "d1"}})
    assert _is_knowledge_materialize(req) is True
    req_list = SimpleNamespace(tool_call={"args": {"action": "list"}})
    assert _is_knowledge_materialize(req_list) is False
