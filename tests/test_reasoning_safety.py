"""推理内容脱敏工具：仅用于诊断/日志等对外场景。

历史会话接口（``/agent/sessions/{id}`` 与 ``/history``）已改为返回完整思考内容，
与会话所有者的实时 SSE 体验一致；本文件验证脱敏函数本身仍然可用。
"""

from easy_agent.services.streaming import build_assistant_message_dict
from easy_agent.utils.reasoning_safety import (
    SAFE_REASONING_SUMMARY,
    redact_message_reasoning,
    redact_messages_reasoning,
)


def test_raw_reasoning_is_redacted_on_historical_read():
    secret = "INTERNAL_REASONING_SENTINEL"

    # 持久化消息由 build_assistant_message_dict 构建（保留原始 thinking）
    stored = build_assistant_message_dict(
        content="answer", thinking=secret, thinking_duration=1.0,
        tool_call_records=[],
        blocks=[{"type": "thinking", "content": secret, "step": 1}],
        input_tokens=1, output_tokens=1, context_tokens=1,
        elapsed_time=1.0, step_count=1,
    )

    # 历史读取出口必须抹掉 thinking 并替换 blocks 中的思考内容
    historical = redact_message_reasoning(stored)
    assert historical["thinking"] is None
    assert historical["blocks"][0]["content"] == SAFE_REASONING_SUMMARY
    assert secret not in str(historical)


def test_redact_messages_reasoning_handles_none_and_empty():
    assert redact_messages_reasoning(None) == []
    assert redact_messages_reasoning([]) == []
