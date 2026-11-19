"""思考循环守卫：本轮末尾连续相同思考 → 摘工具强制收口（软着陆）。"""

from __future__ import annotations

from types import SimpleNamespace

from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage

from easy_agent.agent.thinking_loop import (
    LOOP_BREAK_NOTE,
    ThinkingLoopMiddleware,
    normalize_thinking,
    thinking_loop_streak,
    thinking_text,
)

_LONG_THINKING = (
    "用户的问题涉及金市营收情况数据域：按业务条线拆解最新一期累计的营业净收入"
    "与非息收入，对比该中心经营计划完成进度，标出缺口最大的条线。注意：第 4 个"
    "问题涉及对未来预测外推，技能说明里明确不适用。我应该如实说明不能预测。"
    "我需要先加载「金市营收情况」技能，读取 SKILL.md，然后按技能规则取数。"
)


def _ai(thinking: str, *, calls: list | None = None) -> AIMessage:
    message = AIMessage(content="")
    if thinking:
        message.additional_kwargs["reasoning_content"] = thinking
    if calls:
        message.tool_calls = calls
    return message


def _request(messages: list, tools: list | None = None):
    # 用真实 ModelRequest（而非 SimpleNamespace）：软着陆走 request.override()，
    # 替身会绕过 override 的真实行为，测了个寂寞。
    return ModelRequest(
        model=object(),  # 本中间件不触模型，占位即可
        messages=list(messages),
        state={"messages": list(messages)},
        tools=list(tools or []),
        tool_choice="auto",
        runtime=SimpleNamespace(config={"configurable": {"thread_id": "t1"}}),
    )


def _run_mw(request, *, limit=3, min_chars=100, enabled=True):
    mw = ThinkingLoopMiddleware(limit=limit, min_chars=min_chars, enabled=enabled)
    seen = {}

    def handler(req):
        seen["tools"] = list(req.tools)
        seen["messages"] = list(req.messages)
        seen["tool_choice"] = req.tool_choice
        return "final"

    result = mw.wrap_model_call(request, handler)
    return result, seen


# ── 归一化与思考提取 ─────────────────────────────────────────────


def test_normalize_thinking_collapses_whitespace():
    assert normalize_thinking("  a \n\n  b\t c  ") == "a b c"


def test_thinking_text_reads_reasoning_content():
    assert thinking_text(_ai(_LONG_THINKING)) == _LONG_THINKING


def test_thinking_text_falls_back_to_thinking_blocks():
    message = AIMessage(content=[{"type": "thinking", "text": "先读技能文档"}])
    assert thinking_text(message) == "先读技能文档"


def test_thinking_text_empty_for_plain_answer():
    assert thinking_text(AIMessage(content="直接回答")) == ""


# ── 连续计数 ─────────────────────────────────────────────────────


def test_streak_counts_identical_long_thinking():
    messages = [HumanMessage(content="出报告")] + [_ai(_LONG_THINKING) for _ in range(3)]
    assert thinking_loop_streak(messages, min_chars=100) == 3


def test_streak_zero_when_thinking_differs():
    messages = [
        HumanMessage(content="出报告"),
        _ai(_LONG_THINKING),
        _ai(_LONG_THINKING + "补充一点。"),
    ]
    # 末尾这条思考与前一条不同：streak 只有 1，远低于阈值，放行
    assert thinking_loop_streak(messages, min_chars=100) == 1


def test_streak_ignores_cross_turn_repeat():
    # 上一轮末尾与下一轮开头思考相同是合法行为：只在本轮内计数
    messages = [
        HumanMessage(content="第一问"),
        _ai(_LONG_THINKING),
        _ai(_LONG_THINKING),
        HumanMessage(content="第二问"),
        _ai(_LONG_THINKING),
    ]
    assert thinking_loop_streak(messages, min_chars=100) == 1


def test_short_thinking_breaks_streak_and_not_counted():
    # todo 驱动的合法「继续」模式：短思考不参与计数且打断连续序列
    messages = [
        HumanMessage(content="执行任务"),
        _ai(_LONG_THINKING),
        _ai(_LONG_THINKING),
        _ai("继续执行下一项。"),
        _ai(_LONG_THINKING),
    ]
    assert thinking_loop_streak(messages, min_chars=100) == 1


def test_short_identical_thinking_never_triggers():
    messages = [HumanMessage(content="执行任务")] + [
        _ai("继续执行下一项。") for _ in range(5)
    ]
    assert thinking_loop_streak(messages, min_chars=100) == 0


def test_tool_calls_do_not_break_streak():
    # SKILL.md 循环的真实形态：思考相同、参数也相同；带工具调用不影响计数
    calls = [{"name": "read_file", "args": {"file_path": "/skills/builtin/x/SKILL.md"}, "id": "c"}]
    messages = [HumanMessage(content="出报告")] + [
        _ai(_LONG_THINKING, calls=calls) for _ in range(3)
    ]
    assert thinking_loop_streak(messages, min_chars=100) == 3


def test_messages_without_thinking_do_not_count():
    messages = [HumanMessage(content="出报告"), AIMessage(content="直接回答")]
    assert thinking_loop_streak(messages, min_chars=100) == 0


# ── 软着陆行为 ───────────────────────────────────────────────────


def _loop_messages(n: int = 3) -> list:
    return [HumanMessage(content="出报告")] + [_ai(_LONG_THINKING) for _ in range(n)]


def test_loop_triggers_tool_strip_and_nudge():
    request = _request(_loop_messages(3), tools=["stub-tool"])
    result, seen = _run_mw(request)
    assert result == "final"
    assert seen["tools"] == []  # 工具全部摘除：模型物理上无法再发起调用
    assert seen["tool_choice"] is None
    assert seen["messages"][-1].content == LOOP_BREAK_NOTE
    # 原请求消息原样保留，终态指令只追加在末尾
    assert seen["messages"][:-1] == list(request.messages)


def test_no_loop_passes_request_through():
    request = _request(_loop_messages(2), tools=["stub-tool"])
    _, seen = _run_mw(request)
    assert seen["tools"] == ["stub-tool"]
    assert len(seen["messages"]) == len(request.messages)


def test_threshold_respects_limit_config():
    request = _request(_loop_messages(3), tools=["stub-tool"])
    _, seen = _run_mw(request, limit=4)
    assert seen["tools"] == ["stub-tool"]  # 3 次重复达不到 limit=4，放行


def test_disabled_middleware_is_noop():
    request = _request(_loop_messages(5), tools=["stub-tool"])
    _, seen = _run_mw(request, enabled=False)
    assert seen["tools"] == ["stub-tool"]


def test_broken_state_is_swallowed():
    # 守卫失败绝不阻断正常调用：state 异常吞掉后原样放行
    mw = ThinkingLoopMiddleware()
    request = SimpleNamespace(
        messages=[],
        state=None,  # getattr(state, "messages") 走 None 分支
        tools=["stub-tool"],
        tool_choice="auto",
        runtime=SimpleNamespace(config={}),
    )
    _, seen = _run_mw(request, )
    assert seen["tools"] == ["stub-tool"]


def test_loop_break_note_has_no_mechanism_words():
    """收口提示不能出现内部机制描述（2026-11-19 事故：模型把它原样转述给用户）。

    线上答复曾经是「金市营收情况的数据查询工具已被系统移除」——正是照抄
    ``LOOP_BREAK_NOTE`` 里"已移除全部工具"的结果。这里锁死措辞。
    """
    from easy_agent.agent.thinking_loop import LOOP_BREAK_NOTE

    for banned in ("已移除全部工具", "移除全部工具", "检测到循环", "系统判定", "中间件"):
        assert banned not in LOOP_BREAK_NOTE
    # 必须给出可直接抄的业务句式，否则模型会自己编原因
    assert "数据服务当前未接入" in LOOP_BREAK_NOTE
    # 归因必须是条件式的：循环不一定由"缺数据"造成，不能预置单一结论
    assert "如果确实是" in LOOP_BREAK_NOTE
    assert "不要套用" in LOOP_BREAK_NOTE
