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
    # 先查到 A 的数据，再对 B 连续空查：has_data_once 恒为 true，普通阈值不升级。
    # 注意默认配置下连空到 low_value_limit(6) 就被熔断线收口，硬顶(9)不可达——
    # 只有把熔断线调大到 9 之后，硬顶软止损才会生效。
    history = _mcp_history([_mcp(_mcp_body(1))] + [_mcp("") for _ in range(8)])
    result = _run_mcp(_mcp(""), history, low_value_limit=20)
    assert "⛔" in result.content
    assert "超出" in result.content
    assert "completed" in result.content


def test_mcp_empty_beyond_low_value_limit_fuses_instead_of_hard_stop():
    # 默认配置：连空 9 次在第 7 次就被低价值熔断线收口（提示「下次将熔断」），
    # 不会走到硬顶文案
    history = _mcp_history([_mcp(_mcp_body(1))] + [_mcp("") for _ in range(8)])
    result = _run_mcp(_mcp(""), history)
    assert "下一次调用将被系统熔断拦截" in result.content
    assert "超出「分页/分段遍历」" not in result.content


def test_mcp_empty_below_hard_limit_still_normal_traversal():
    # 取到过数据后连空几次（< 低价值熔断线 6）仍是正常遍历
    history = _mcp_history([_mcp(_mcp_body(1))] + [_mcp("") for _ in range(4)])
    result = _run_mcp(_mcp(""), history)
    assert "正常遍历" in result.content
    assert "熔断" not in result.content


def test_mcp_empty_after_data_escalates_to_fuse_warning():
    # 先查到 A 的数据，再连续空查到熔断线（默认 6）：不再是「正常遍历」，
    # 注入「下次调用将熔断」的升级提示（第 8 次调用会被 wrap_tool_call 拦截）
    history = _mcp_history([_mcp(_mcp_body(1))] + [_mcp("") for _ in range(6)])
    result = _run_mcp(_mcp(""), history)
    assert "熔断拦截" in result.content
    assert "连续 7 次" in result.content


def test_mcp_pagination_is_never_flagged():
    # 30 页分页取数：每页内容不同 → 计数始终为 0，一个字都不该注入
    history = _mcp_history([_mcp(_mcp_body(i)) for i in range(29)])
    result = _run_mcp(_mcp(_mcp_body(29)), history)
    assert result.content == _mcp(_mcp_body(29))


def test_mcp_repeated_identical_result_escalates_to_fuse_warning():
    # 同一批数据被原样反复取回到熔断线（默认 6）：注入「下次调用将熔断」
    history = _mcp_history([_mcp(_mcp_body(0)) for _ in range(6)])
    result = _run_mcp(_mcp(_mcp_body(0)), history)
    assert "熔断拦截" in result.content


def test_mcp_repeated_identical_result_converges_with_custom_limit():
    # 低价值熔断线调大后，收敛/停机提示仍然可达（软提示未删除，只是被熔断线前置）
    history = _mcp_history([_mcp(_mcp_body(0)) for _ in range(8)])
    result = _run_mcp(_mcp(_mcp_body(0)), history, low_value_limit=20)
    assert "收敛" in result.content


def test_mcp_repeated_identical_result_stops_with_custom_limit():
    history = _mcp_history([_mcp(_mcp_body(0)) for _ in range(12)])
    result = _run_mcp(_mcp(_mcp_body(0)), history, low_value_limit=20)
    assert "数据质量报告" in result.content


def test_mcp_low_value_rows_trigger_fuse_warning():
    # 场景 2（ forex 267 步）：每次都换参数、每次只回 1–2 行脏数据——
    # 不空、内容也不同，旧判定全不命中；低价值行数阈值把它抓出来
    history = _mcp_history(
        [_mcp(_mcp_body(page=i, rows=1)) for i in range(6)]
    )
    result = _run_mcp(_mcp(_mcp_body(page=99, rows=1)), history)
    assert "熔断拦截" in result.content


def test_mcp_substantial_rows_are_never_low_value():
    # 行数充足的分页结果永远不算低价值
    history = _mcp_history([_mcp(_mcp_body(page=i, rows=100)) for i in range(10)])
    result = _run_mcp(_mcp(_mcp_body(page=99, rows=100)), history)
    assert result.content == _mcp(_mcp_body(page=99, rows=100))


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


# ── 工具熔断（wrap_tool_call 硬拦截，2026-11-17）──────────────────────

from types import SimpleNamespace as _NS

from easy_agent.agent.tool_progress import _fuse_text  # noqa: E402 - 测试文件末尾追加段


def _fuse_request(name: str, args: dict, history: list, thread_id: str | None = None):
    """构造熔断检查用的 request：state 含历史 + 当前 AIMessage（生产形态）。

    生产环境中工具执行时，带 tool_call 的 AIMessage 已经进了 state——
    预算计数（tool_call_count_in_turn）据此天然包含本次调用。
    """
    current_call = {"name": name, "args": args, "id": "cur"}
    request = _NS(tool_call=current_call, state={"messages": [*history, AIMessage(content="", tool_calls=[current_call])]})
    if thread_id:
        request.runtime = _NS(config={"configurable": {"thread_id": thread_id}})
    return request


def _pair(name: str, args: dict, content: str, index: int) -> list:
    call_id = f"h{index}"
    return [
        AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}]),
        ToolMessage(content=content, tool_call_id=call_id, name=name),
    ]


def test_fuse_blocks_fourth_same_args_empty_call():
    # 情况 1：同一查询连续 3 次空结果，第 4 次必须被拦截，真实工具不执行
    args = {"path": "/.agent/conversation_history/session_x.md", "pattern": "用户当前问题"}
    history = []
    for i in range(3):
        history += _pair("grep", args, "No matches found", i)
    mw = ToolProgressMiddleware()

    def handler(_req):
        raise AssertionError("熔断后不应执行真实工具")

    result = mw.wrap_tool_call(_fuse_request("grep", args, history), handler)
    assert isinstance(result, ToolMessage)
    assert "熔断" in result.content
    assert result.tool_call_id == "cur"


def test_fuse_streak_resets_when_data_found():
    # 中途查到过东西 → 连续计数清零，之后重新累计，不误伤
    args = {"path": "/x", "pattern": "p"}
    history = _pair("grep", args, "No matches found", 0)
    history += _pair("grep", args, "some content", 1)
    history += _pair("grep", args, "No matches found", 2)
    history += _pair("grep", args, "No matches found", 3)
    mw = ToolProgressMiddleware()
    result = mw.wrap_tool_call(
        _fuse_request("grep", args, history), lambda _req: _tool_message("No matches found", "grep")
    )
    assert "熔断" not in result.content


def test_different_args_are_not_fused_by_same_args_rule():
    # 每次换路径的合法探索，不落入「同参数连续空」熔断
    mw = ToolProgressMiddleware()
    history = []
    for i in range(3):
        history += _pair("grep", {"path": f"/p{i}"}, "No matches found", i)
    result = mw.wrap_tool_call(
        _fuse_request("grep", {"path": "/p3"}, history),
        lambda _req: _tool_message("No matches found", "grep"),
    )
    assert "熔断" not in result.content


def test_budget_fuses_call_beyond_budget_when_mostly_low_value():
    # 情况 2a：单工具预算（显式传 20 验证机制），且低价值结果占比过半时，
    # 第 budget+1 次起熔断。4 次低价值 + 1 次大结果交替（低价值占比 80%）：
    # 预算兜底在低价值连续计数（被大结果不断清零）永远够不到时接住。
    mw = ToolProgressMiddleware(tool_call_budget=20)
    history = []
    for i in range(20):
        rows = 500 if i % 5 == 4 else 1
        history += _pair("mcp__fx__bars", {"page": i}, _mcp(_mcp_body(page=i, rows=rows)), i)
    result = mw.wrap_tool_call(
        _fuse_request("mcp__fx__bars", {"page": 99}, history),
        lambda _req: ToolMessage(content=_mcp(_mcp_body(99, rows=1)), tool_call_id="cur", name="mcp__fx__bars"),
    )
    assert "熔断" in result.content
    assert "超出预算" in result.content


def test_budget_never_fuses_healthy_pagination():
    # 健康分页（每页都是足量新数据）哪怕超过预算（默认 30）也绝不能熔断——
    # 预算只对「低价值占比过半」的试探性调用生效
    mw = ToolProgressMiddleware()
    history = []
    for i in range(35):
        history += _pair("mcp__fx__bars", {"page": i}, _mcp(_mcp_body(page=i, rows=100)), i)
    result = mw.wrap_tool_call(
        _fuse_request("mcp__fx__bars", {"page": 99}, history),
        lambda _req: ToolMessage(content=_mcp(_mcp_body(99, rows=100)), tool_call_id="cur", name="mcp__fx__bars"),
    )
    assert "熔断" not in result.content


def test_budget_boundary_call_at_budget_still_executes():
    # 第 budget（默认 30）次本身仍执行：熔断从 budget+1 次起
    mw = ToolProgressMiddleware()
    history = []
    for i in range(29):
        history += _pair("mcp__fx__bars", {"page": i}, _mcp(_mcp_body(page=i, rows=100)), i)
    result = mw.wrap_tool_call(
        _fuse_request("mcp__fx__bars", {"page": 99}, history),
        lambda _req: ToolMessage(content=_mcp(_mcp_body(99)), tool_call_id="cur", name="mcp__fx__bars"),
    )
    assert "熔断" not in result.content


def test_low_value_streak_fuses_seventh_call():
    # 情况 2b：每次换参数、每次只回 1 行——第 7 次起熔断
    mw = ToolProgressMiddleware()
    history = []
    for i in range(6):
        history += _pair("mcp__fx__bars", {"month": i}, _mcp(_mcp_body(page=i, rows=1)), i)
    result = mw.wrap_tool_call(
        _fuse_request("mcp__fx__bars", {"month": 99}, history),
        lambda _req: ToolMessage(content=_mcp(_mcp_body(99, rows=1)), tool_call_id="cur", name="mcp__fx__bars"),
    )
    assert "熔断" in result.content


def test_low_value_probes_below_limit_are_not_fused():
    mw = ToolProgressMiddleware()
    history = []
    for i in range(4):
        history += _pair("mcp__fx__bars", {"month": i}, _mcp(_mcp_body(page=i, rows=1)), i)
    result = mw.wrap_tool_call(
        _fuse_request("mcp__fx__bars", {"month": 99}, history),
        lambda _req: ToolMessage(content=_mcp(_mcp_body(99, rows=1)), tool_call_id="cur", name="mcp__fx__bars"),
    )
    assert "熔断" not in result.content


def test_fuse_log_contains_thread_id(caplog):
    # 日志观测要求：熔断日志必须带 thread_id 前缀，能定位到会话
    import logging

    args = {"path": "/x"}
    history = []
    for i in range(3):
        history += _pair("grep", args, "No matches found", i)
    mw = ToolProgressMiddleware()
    with caplog.at_level(logging.WARNING, logger="easy_agent.agent"):
        mw.wrap_tool_call(
            _fuse_request("grep", args, history, thread_id="thread-abc"),
            lambda _req: _tool_message("No matches found", "grep"),
        )
    assert any("[thread-abc]" in rec.message and "工具熔断" in rec.message for rec in caplog.records)


def test_fuse_disabled_middleware_never_blocks():
    mw = ToolProgressMiddleware(enabled=False)
    args = {"path": "/x"}
    history = []
    for i in range(5):
        history += _pair("grep", args, "No matches found", i)
    result = mw.wrap_tool_call(
        _fuse_request("grep", args, history), lambda _req: _tool_message("No matches found", "grep")
    )
    assert "熔断" not in result.content


def test_fuse_check_failure_does_not_block_tool():
    # state 结构异常时熔断检查必须静默放行，绝不阻断正常工具调用
    mw = ToolProgressMiddleware()
    request = _NS(tool_call={"name": "grep", "args": {"path": "/x"}, "id": "cur"}, state=None)
    result = mw.wrap_tool_call(request, lambda _req: _tool_message("content", "grep"))
    assert result.content == "content"


def test_fuse_text_mentions_stop_and_conclusion():
    text = _fuse_text("同一查询已连续 3 次返回空结果")
    assert "同一查询已连续 3 次返回空结果" in text
    assert "结论" in text
    assert NOTE_MARK in text


def test_write_like_tool_is_never_fused():
    # 熔断只覆盖探索型四件套 + MCP 查询工具：写类工具在长任务里合法调用
    # 几十次是常态，哪怕结果雷同也绝不能被预算/重复判定误熔断
    mw = ToolProgressMiddleware()
    history = []
    for i in range(25):
        history += _pair("edit_file", {"path": f"/f{i}.md"}, "File created successfully", i)
    result = mw.wrap_tool_call(
        _fuse_request("edit_file", {"path": "/f99.md"}, history),
        lambda _req: _tool_message("File created successfully", "edit_file"),
    )
    assert "熔断" not in result.content


def test_mcp_tool_first_call_is_never_fused():
    # MCP 工具首次调用时历史为空（无法识别类型），熔断必须放行
    mw = ToolProgressMiddleware()
    result = mw.wrap_tool_call(
        _fuse_request("mcp__fx__bars", {"page": 0}, []),
        lambda _req: ToolMessage(content=_mcp(_mcp_body(0)), tool_call_id="cur", name="mcp__fx__bars"),
    )
    assert "熔断" not in result.content


def test_execute_mkdir_loop_is_never_fused():
    # mkdir 这类静默成功的输出本就是空：创建多个目录的合法任务绝不能被
    # 「空结果熔断」或「空正文重复」误伤（非查找类命令不判空）
    mw = ToolProgressMiddleware()
    history = []
    for i in range(8):
        history += _pair("execute", {"command": f"mkdir -p /workspace/out{i}"}, "", i)
    result = mw.wrap_tool_call(
        _fuse_request("execute", {"command": "mkdir -p /workspace/out99"}, history),
        lambda _req: _tool_message("", "execute"),
    )
    assert "熔断" not in result.content


def test_execute_lookup_empty_loop_fuses_at_seventh_call():
    # 换路径反复空查的探索型循环（execute 查找类命令）：第 7 次熔断
    mw = ToolProgressMiddleware()
    history = []
    for i in range(6):
        history += _pair("execute", {"command": f"find /qts -name 'f{i}.md'"}, "", i)
    result = mw.wrap_tool_call(
        _fuse_request("execute", {"command": "find /qts -name 'f99.md'"}, history),
        lambda _req: _tool_message("", "execute"),
    )
    assert "熔断" in result.content


def test_execute_same_args_empty_lookup_fuses_at_fourth_call():
    # 情况 1 对 execute 查找类命令同样生效：同命令连续 3 次空输出，第 4 次熔断
    mw = ToolProgressMiddleware()
    history = []
    for i in range(3):
        history += _pair("execute", {"command": "find /qts -name 'x.md'"}, "", i)
    result = mw.wrap_tool_call(
        _fuse_request("execute", {"command": "find /qts -name 'x.md'"}, history),
        lambda _req: _tool_message("", "execute"),
    )
    assert "熔断" in result.content


def test_fuse_counts_annotated_history():
    # 关键回归：生产环境历史结果都带本模块追加的尾注（含不带 NOTE_MARK 的
    # 存量格式）。尾注剥不干净会让 is_empty 判 False，连续计数永远断掉，
    # 熔断一次都不会触发——必须按「剥过尾注的正文」判空。
    args = {"path": "/.agent/history.md", "pattern": "用户当前问题"}
    annotated = "No matches found\n（此处为空。继续换同级路径不会有结果，请改用已知正确路径，或直接说明数据缺失。）"
    history = []
    for i in range(3):
        history += _pair("grep", args, annotated, i)
    mw = ToolProgressMiddleware()
    result = mw.wrap_tool_call(
        _fuse_request("grep", args, history),
        lambda _req: _tool_message("No matches found", "grep"),
    )
    assert "熔断" in result.content


def test_non_envelope_result_on_mcp_tool_still_gets_hint():
    # 口径统一回归：工具历史里有信封结果（已识别为 MCP 工具），本次返回
    # 裸结果（无信封）——熔断在按 MCP 计数，提示也必须照给，否则模型会在
    # 收不到任何警告的情况下被熔断拦截。
    mw = ToolProgressMiddleware()
    history = []
    for i in range(5):
        history += _pair(
            "fx_bars", {"m": i}, _mcp(_mcp_body(page=i, rows=1)), i
        )
    result = mw.wrap_tool_call(
        _fuse_request("fx_bars", {"m": 99}, history),
        lambda _req: ToolMessage(  # 本次无信封
            content=_mcp_body(99, rows=1), tool_call_id="cur", name="fx_bars"
        ),
    )
    assert isinstance(result, ToolMessage)
    assert "低价值" in result.content or "有效数据" in result.content


def test_non_envelope_result_on_mcp_tool_still_gets_hint():
    # 口径统一回归：工具历史里有信封结果（已识别为 MCP 工具），本次返回
    # 裸结果（无信封）——熔断在按 MCP 计数，提示也必须照给，否则模型会在
    # 收不到任何警告的情况下被熔断拦截。
    mw = ToolProgressMiddleware()
    history = []
    for i in range(5):
        history += _pair(
            "fx_bars", {"m": i}, _mcp(_mcp_body(page=i, rows=1)), i
        )
    result = mw.wrap_tool_call(
        _fuse_request("fx_bars", {"m": 99}, history),
        lambda _req: ToolMessage(  # 本次无信封
            content=_mcp_body(99, rows=1), tool_call_id="cur", name="fx_bars"
        ),
    )
    assert isinstance(result, ToolMessage)
    assert "低价值" in result.content or "有效数据" in result.content


# ── 情况 1b：同参数累计执行上限（2026-11-18）────────────────────────


def test_same_args_total_fuses_ninth_call_even_with_varying_output():
    # 情况 1b：同一条命令原样重跑 8 次（输出每次带"时间戳"微变，空结果/
    # 重复指纹/低价值行数判定全部擦边漏过），第 9 次必须被拦截。
    args = {"command": "cd /workspace && python3 probe_with_params.py"}
    mw = ToolProgressMiddleware()
    history = []
    for i in range(8):
        output = f"probe ok at 2026-11-18T14:{i:02d}:00 latency={i}ms\n"
        history += _pair("execute", args, output, i)
    result = mw.wrap_tool_call(
        _fuse_request("execute", args, history),
        lambda _req: ToolMessage(content="probe ok", tool_call_id="cur", name="execute"),
    )
    assert isinstance(result, ToolMessage)
    assert "熔断" in result.content
    assert "累计执行 8 次" in result.content


def test_same_args_total_passes_below_limit():
    # 7 次执行 + 第 8 次：未到上限（默认 8），放行
    args = {"command": "cd /workspace && python3 probe_with_params.py"}
    mw = ToolProgressMiddleware()
    history = []
    for i in range(7):
        output = f"probe ok at 2026-11-18T14:{i:02d}:00\n"
        history += _pair("execute", args, output, i)
    result = mw.wrap_tool_call(
        _fuse_request("execute", args, history),
        lambda _req: ToolMessage(content="probe ok", tool_call_id="cur", name="execute"),
    )
    assert "熔断" not in result.content


def test_same_args_total_counts_across_interruptions():
    # 不要求连续：中间穿插其它调用/其它参数，同参数的累计照样计入
    mw = ToolProgressMiddleware()
    history = []
    for i in range(8):
        history += _pair("execute", {"command": "python3 probe.py"}, f"ok {i}", i)
        history += _pair("execute", {"command": "ls /workspace"}, "a.txt\nb.txt", 100 + i)
    result = mw.wrap_tool_call(
        _fuse_request("execute", {"command": "python3 probe.py"}, history),
        lambda _req: ToolMessage(content="ok", tool_call_id="cur", name="execute"),
    )
    assert "累计执行 8 次" in result.content


def test_same_args_total_ignores_varying_args():
    # 合法探索：参数每次都换（分页/换路径），累计规则绝不误伤
    mw = ToolProgressMiddleware()
    history = []
    for i in range(20):
        history += _pair("execute", {"command": f"python3 probe.py --page {i}"}, f"ok {i}", i)
    result = mw.wrap_tool_call(
        _fuse_request("execute", {"command": "python3 probe.py --page 99"}, history),
        lambda _req: ToolMessage(content="ok", tool_call_id="cur", name="execute"),
    )
    assert "熔断" not in result.content


def test_same_args_total_fuses_mcp_tool_with_varying_output():
    # MCP 工具同样受同参数累计上限保护：同参数重查 8 次（输出带 fetched_at
    # 微变 → 重复指纹判不出），第 9 次拦截
    mw = ToolProgressMiddleware()
    history = []
    for i in range(8):
        fetched = f"2026-11-05T00:{i:02d}:00Z"
        history += _pair(
            MCP_TOOL, {"symbol": "XAU"}, _mcp(_mcp_body(page=0, rows=50), fetched_at=fetched), i
        )
    result = mw.wrap_tool_call(
        _fuse_request(MCP_TOOL, {"symbol": "XAU"}, history),
        lambda _req: ToolMessage(content=_mcp(_mcp_body(page=0, rows=50)), tool_call_id="cur", name=MCP_TOOL),
    )
    assert isinstance(result, ToolMessage)
    assert "累计执行 8 次" in result.content


def test_same_args_total_limit_respects_config():
    # 阈值从配置传入：limit=3 时第 4 次起熔断
    mw = ToolProgressMiddleware(same_args_total_limit=3)
    args = {"command": "python3 probe.py"}
    history = []
    for i in range(3):
        history += _pair("execute", args, f"ok {i}", i)
    result = mw.wrap_tool_call(
        _fuse_request("execute", args, history),
        lambda _req: ToolMessage(content="ok", tool_call_id="cur", name="execute"),
    )
    assert "累计执行 3 次" in result.content
