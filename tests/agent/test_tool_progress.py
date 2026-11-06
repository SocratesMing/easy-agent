"""工具进度中间件：空结果终止信号 + 原地打转提醒。"""

from __future__ import annotations

import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from easy_agent.agent.tool_progress import (
    NOTE_MARK,
    REPEAT_WARN_AFTER,
    ToolProgressMiddleware,
    body_fingerprint,
    consecutive_repeats,
    is_empty_mcp_body,
    is_empty_result,
    is_mcp_result,
    json_is_empty,
    looks_like_error,
    strip_mcp_envelope,
    strip_progress_notes,
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


# ── MCP 止损 ──────────────────────────────────────────────────────────

MCP_TOOL = "forex__get_forex_bars"


def _mcp_body(page: int = 0, rows: int = 100) -> str:
    return json.dumps(
        {"row_count": rows, "data": [{"page": page, "close": 133.1 + page}]},
        ensure_ascii=False,
    )


def _mcp(content: str, *, fetched_at: str = "2026-11-05T00:00:00Z", error: bool = False) -> str:
    marker = (
        f"[MCP source: demo/{MCP_TOOL}; fetched_at={fetched_at}; ok=true; "
        f"truncated=false; is_error={str(error).lower()}]"
    )
    return f"{marker}\n{content}"


def _mcp_history(contents: list[str], name: str = MCP_TOOL) -> list:
    messages: list = []
    for index, content in enumerate(contents):
        call_id = f"c{index}"
        messages.append(
            AIMessage(
                content="",
                tool_calls=[{"name": name, "args": {"page": index}, "id": call_id}],
            )
        )
        messages.append(ToolMessage(content=content, tool_call_id=call_id, name=name))
    return messages


def _run_mcp(content: str, history: list | None = None, name: str = MCP_TOOL, **kwargs):
    mw = ToolProgressMiddleware(**kwargs)
    request = SimpleNamespace(
        tool_call={"name": name, "args": {"page": 99}, "id": "cur"},
        state={"messages": list(history or [])},
    )
    return mw.annotate(request, ToolMessage(content=content, tool_call_id="cur", name=name))


def test_mcp_envelope_is_detected_and_stripped():
    text = _mcp('{"row_count": 1}')
    assert is_mcp_result(text) is True
    assert is_mcp_result("No files found") is False
    assert strip_mcp_envelope(text) == '{"row_count": 1}'


def test_json_is_empty_matrix():
    assert json_is_empty({"row_count": 0}) is True
    assert json_is_empty({"total": 0}) is True
    assert json_is_empty({"count": "0"}) is True
    assert json_is_empty({"data": []}) is True
    assert json_is_empty({"items": []}) is True
    assert json_is_empty({"data": {"items": [], "total": 0}}) is True
    assert json_is_empty([]) is True
    assert json_is_empty({}) is True
    # 有数据 / 判不出 → 不是空
    assert json_is_empty({"data": [{"a": 1}], "row_count": 1}) is False
    assert json_is_empty({"total": 5, "data": []}) is False  # 计数优先
    assert json_is_empty({"ok": True}) is None
    assert json_is_empty("plain text") is None


def test_is_empty_mcp_body_prefers_structured_fields():
    assert is_empty_mcp_body('{"row_count": 0, "data": []}') is True
    assert is_empty_mcp_body("") is True
    assert is_empty_mcp_body("[]") is True
    # 未解析成 JSON 的长文本不判空（避免误伤）
    assert is_empty_mcp_body("2023-01-01,133.1") is False


def test_fingerprint_ignores_envelope_and_own_note():
    raw = _mcp(_mcp_body(1), fetched_at="2026-11-05T00:00:00Z")
    later = _mcp(_mcp_body(1), fetched_at="2026-11-05T09:99:99Z")
    assert strip_mcp_envelope(raw) == strip_mcp_envelope(later)
    annotated = raw + "\n" + NOTE_MARK + "任意提示"
    assert body_fingerprint(strip_progress_notes(strip_mcp_envelope(annotated))) == (
        body_fingerprint(strip_mcp_envelope(raw))
    )


def test_mcp_first_empty_gets_gentle_note():
    result = _run_mcp(_mcp(""))
    assert "第 1 次尝试" in result.content


def test_mcp_second_empty_warns():
    history = _mcp_history([_mcp("")])
    result = _run_mcp(_mcp(""), history)
    assert "再查一次仍无结果" in result.content
    assert "连续 2 次" in result.content


def test_mcp_third_empty_stops_and_closes_todo():
    history = _mcp_history([_mcp(""), _mcp("")])
    result = _run_mcp(_mcp(""), history)
    assert "⛔" in result.content
    assert "连续 3 次" in result.content
    # write_todos 的 status 只有 pending/in_progress/completed，写 cancelled 会参数校验失败
    assert "completed" in result.content
    assert "cancelled" not in result.content


def test_mcp_empty_after_data_is_normal_traversal():
    # 取到过数据后的空结果 = 分页到底 / 该区间无数据，不升级、不要求关闭 todo
    history = _mcp_history([_mcp(_mcp_body(1))])
    result = _run_mcp(_mcp(""), history)
    assert "正常遍历" in result.content
    assert "⛔" not in result.content
    assert "completed" not in result.content


def test_mcp_empty_hard_limit_stops_even_after_data():
    # 先查到 A 的数据，再对 B 连续空查：has_data_once 恒为 true，普通阈值不升级，
    # 只能靠硬顶兜住（默认 3 × empty_limit = 9）。
    history = _mcp_history([_mcp(_mcp_body(1))] + [_mcp("") for _ in range(8)])
    result = _run_mcp(_mcp(""), history)
    assert "⛔" in result.content
    assert "超出" in result.content
    assert "completed" in result.content


def test_mcp_empty_below_hard_limit_still_normal_traversal():
    history = _mcp_history([_mcp(_mcp_body(1))] + [_mcp("") for _ in range(7)])
    result = _run_mcp(_mcp(""), history)
    assert "正常遍历" in result.content
    assert "⛔" not in result.content


def test_mcp_pagination_is_never_flagged():
    # 30 页分页取数：每页内容不同 → 计数始终为 0，一个字都不该注入
    history = _mcp_history([_mcp(_mcp_body(i)) for i in range(29)])
    result = _run_mcp(_mcp(_mcp_body(29)), history)
    assert result.content == _mcp(_mcp_body(29))


def test_mcp_repeated_identical_result_converges():
    history = _mcp_history([_mcp(_mcp_body(0)) for _ in range(8)])
    result = _run_mcp(_mcp(_mcp_body(0)), history)
    assert "收敛" in result.content


def test_mcp_repeated_identical_result_stops():
    history = _mcp_history([_mcp(_mcp_body(0)) for _ in range(12)])
    result = _run_mcp(_mcp(_mcp_body(0)), history)
    assert "数据质量报告" in result.content


def test_mcp_error_result_is_not_annotated():
    content = _mcp("boom", error=True)
    result = _run_mcp(content)
    assert result.content == content


def test_mcp_thresholds_are_configurable():
    mw = ToolProgressMiddleware(empty_limit=1)
    request = SimpleNamespace(
        tool_call={"name": MCP_TOOL, "args": {}, "id": "cur"},
        state={"messages": []},
    )
    result = mw.annotate(request, ToolMessage(content=_mcp(""), tool_call_id="cur", name=MCP_TOOL))
    assert "⛔" in result.content


def test_mcp_counts_only_current_turn():
    # 上一轮的 5 次空结果不能累积到本轮，本轮第 1 次空结果只能是温和提示
    previous = _mcp_history([_mcp("") for _ in range(5)])
    history = previous + [HumanMessage(content="换个区间再看看")]
    result = _run_mcp(_mcp(""), history)
    assert "第 1 次尝试" in result.content
    assert "⛔" not in result.content


def test_write_like_mcp_is_out_of_scope_by_design():
    # 非 MCP 信封、也不在四件套里的工具（含写类）一律不动
    content = "Successfully wrote 3 rows"
    result = _run_mcp(content, name="mysql__execute_sql")
    assert result.content == content


# ── 分段内容（真实 MCP 形态）与默认值来源 ─────────────────────────────


def _run_mcp_message(message: ToolMessage, name: str = MCP_TOOL, **kwargs):
    mw = ToolProgressMiddleware(**kwargs)
    request = SimpleNamespace(
        tool_call={"name": name, "args": {"page": 1}, "id": "cur"},
        state={"messages": []},
    )
    return mw.annotate(request, message)


def _envelope_block(body: str) -> list:
    """真实 MCP 工具的结果形态：信封与正文是两个 text 块。"""
    marker = (
        f"[MCP source: demo/{MCP_TOOL}; fetched_at=2026-11-05T00:00:00Z; "
        "ok=true; truncated=false; is_error=false]"
    )
    return [{"type": "text", "text": marker}, {"type": "text", "text": body}]


def test_mcp_list_content_gets_note_as_extra_block():
    # 真实 MCP 工具 response_format="content_and_artifact"，ToolMessage.content
    # 是 content block 列表。只认 str 会让止损在线上一次都不触发。
    blocks = _envelope_block('{"row_count": 0, "data": []}')
    result = _run_mcp_message(
        ToolMessage(content=list(blocks), tool_call_id="cur", name=MCP_TOOL)
    )
    assert isinstance(result.content, list)
    assert result.content[:2] == blocks  # 原有块一字不动
    assert NOTE_MARK in result.content[-1]["text"]
    assert "第 1 次尝试" in result.content[-1]["text"]


def test_mcp_list_content_keeps_media_blocks():
    # 图片块不能被尾注挤掉，也不能被当成文本
    blocks = [
        *_envelope_block('{"row_count": 0, "data": []}'),
        {"type": "image", "base64": "AAAA", "mime_type": "image/png"},
    ]
    result = _run_mcp_message(
        ToolMessage(content=list(blocks), tool_call_id="cur", name=MCP_TOOL)
    )
    assert result.content[2] == {"type": "image", "base64": "AAAA", "mime_type": "image/png"}
    assert NOTE_MARK in result.content[-1]["text"]


def test_body_fingerprint_distinguishes_equal_length_pages():
    # 两页内容不同、总长相同、开头 512 字符相同（固定 schema）：
    # 只取「长度 + 头尾 512」会撞出同一个指纹，合法分页被误判成无进展。
    head = "x" * 600
    left = head + "A" * 400
    right = head + "B" * 400
    assert len(left) == len(right)
    assert body_fingerprint(left) != body_fingerprint(right)


def test_middleware_defaults_follow_config_models():
    from easy_agent.bootstrap.config_models import (
        DEFAULT_TOOL_PROGRESS_CONVERGE_LIMIT,
        DEFAULT_TOOL_PROGRESS_EMPTY_HARD_LIMIT,
        DEFAULT_TOOL_PROGRESS_EMPTY_LIMIT,
        DEFAULT_TOOL_PROGRESS_STOP_LIMIT,
    )

    mw = ToolProgressMiddleware()
    assert mw.empty_limit == DEFAULT_TOOL_PROGRESS_EMPTY_LIMIT
    assert mw.converge_limit == DEFAULT_TOOL_PROGRESS_CONVERGE_LIMIT
    assert mw.stop_limit == DEFAULT_TOOL_PROGRESS_STOP_LIMIT
    assert mw.empty_hard_limit == DEFAULT_TOOL_PROGRESS_EMPTY_HARD_LIMIT
    # 硬顶不得小于普通空阈值，否则「从未取到数据」的分支永远走不到
    assert mw.empty_hard_limit >= mw.empty_limit
