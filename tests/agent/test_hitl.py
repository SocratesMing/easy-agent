from types import SimpleNamespace

from easy_agent.hitl import EXECUTE_HITL_TOOL_DESCRIPTION, _is_destructive_command


def test_execute_description_mentions_sandbox():
    assert "沙箱" in EXECUTE_HITL_TOOL_DESCRIPTION
    assert "没有沙箱隔离" not in EXECUTE_HITL_TOOL_DESCRIPTION


def test_destructive_rm_detected():
    request = SimpleNamespace(tool_call={"args": {"command": "rm /workspace/a.txt"}})
    assert _is_destructive_command(request) is True
