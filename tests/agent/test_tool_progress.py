"""工具进度中间件：空结果终止信号 + 原地打转提醒。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, ToolMessage

from easy_agent.agent.tool_progress import (
    REPEAT_WARN_AFTER,
    ToolProgressMiddleware,
    consecutive_repeats,
    is_empty_result,
    looks_like_error,
)


def _request(name: str, args: dict | None = None, messages: list | None = None):
    return SimpleNamespace(
        tool_call={"name": name, "args": args or {}, "id": "call_1"},
        state={"messages": list(messages or [])},
    )


def _tool_message(content: str, name: str = "ls") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id="call_1", name=name)


def _run(name: str, content: str, args: dict | None = None, messages: list | None = None):
    mw = ToolProgressMiddleware()
    return mw.annotate(_request(name, args, messages), _tool_message(content, name))


def test_empty_ls_gets_hint():
    result = _run("ls", "No files found")
    assert "此处为空" in result.content
    assert result.content.startswith("No files found")


def test_empty_grep_gets_hint():
    result = _run("grep", "No matches found")
    assert "此处为空" in result.content


def test_blank_execute_lookup_gets_hint():
    result = _run("execute", "", args={"command": "find /qts -maxdepth 3 -name '*.md'"})
    assert "此处为空" in result.content


def test_execute_non_lookup_is_left_alone():
    # mkdir 这类静默成功输出本来就是空，不能误判成「找不到」
    result = _run("execute", "", args={"command": "mkdir -p /workspace/out"})
    assert result.content == ""


def test_non_empty_result_untouched():
    content = "/workspace/uploads/a.txt\n/workspace/uploads/b.txt"
    result = _run("ls", content)
    assert result.content == content


def test_read_file_is_not_scoped():
    result = _run("read_file", "")
    assert result.content == ""


def test_error_result_is_not_annotated():
    # 工具失败自带 ToolErrorMiddleware 的重试提示，再叠一句会互相打架
    content = "Error: file not found\n（工具 read_file 未返回结果，请勿假设它已成功。）"
    result = _run("ls", content)
    assert result.content == content


def test_repeat_warning_after_threshold():
    calls = [
        {"name": "ls", "args": {"path": "/nope"}, "id": f"c{i}"}
        for i in range(REPEAT_WARN_AFTER)
    ]
    messages = [AIMessage(content="", tool_calls=[c]) for c in calls]
    result = _run("ls", "No files found", args={"path": "/nope"}, messages=messages)
    assert "连续 5 次" in result.content


def test_no_repeat_warning_below_threshold():
    calls = [{"name": "ls", "args": {"path": "/nope"}, "id": "c0"}]
    messages = [AIMessage(content="", tool_calls=calls)]
    result = _run("ls", "No files found", args={"path": "/nope"}, messages=messages)
    assert "连续" not in result.content


def test_different_args_break_the_streak():
    calls = [
        {"name": "ls", "args": {"path": "/a"}, "id": "c0"},
        {"name": "ls", "args": {"path": "/b"}, "id": "c1"},
    ]
    messages = [AIMessage(content="", tool_calls=[c]) for c in calls]
    result = _run("ls", "No files found", args={"path": "/b"}, messages=messages)
    assert "连续" not in result.content


def test_disabled_middleware_is_noop():
    mw = ToolProgressMiddleware(enabled=False)
    request = _request("ls")
    result = mw.annotate(request, _tool_message("No files found"))
    assert result.content == "No files found"


def test_non_text_content_untouched():
    message = ToolMessage(
        content=[{"type": "text", "text": "No files found"}],
        tool_call_id="call_1",
        name="ls",
    )
    result = ToolProgressMiddleware().annotate(_request("ls"), message)
    assert isinstance(result.content, list)


def test_non_tool_message_passthrough():
    sentinel = object()
    assert ToolProgressMiddleware().annotate(_request("ls"), sentinel) is sentinel


def test_wrap_tool_call_uses_handler_result():
    mw = ToolProgressMiddleware()
    request = _request("glob")
    result = mw.wrap_tool_call(request, lambda _req: _tool_message("No files found"))
    assert "此处为空" in result.content


async def test_awrap_tool_call_uses_handler_result():
    mw = ToolProgressMiddleware()
    request = _request("glob")

    async def handler(_req):
        return _tool_message("No files found")

    result = await mw.awrap_tool_call(request, handler)
    assert "此处为空" in result.content


# ── 纯函数 ────────────────────────────────────────────────────────────


def test_is_empty_result_matrix():
    assert is_empty_result("ls", {}, "No files found") is True
    assert is_empty_result("grep", {}, "No matches found") is True
    assert is_empty_result("ls", {}, "") is True
    assert is_empty_result("ls", {}, "a.txt") is False
    assert is_empty_result("execute", {"command": "ls /x"}, "") is True
    assert is_empty_result("execute", {"command": "echo hi"}, "") is False


def test_looks_like_error():
    assert looks_like_error("Error: boom") is True
    assert looks_like_error("错误：文件不存在") is True
    assert looks_like_error("No files found") is False


def test_consecutive_repeats_counts_current_call():
    call = {"name": "ls", "args": {"path": "/x"}, "id": "c0"}
    fingerprint = "ls:" + json.dumps({"path": "/x"}, ensure_ascii=False, sort_keys=True)
    state = {"messages": [AIMessage(content="", tool_calls=[call]) for _ in range(3)]}
    assert consecutive_repeats(state, fingerprint) == 3
    assert consecutive_repeats(None, fingerprint) == 0
