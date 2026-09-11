from easy_agent.services.streaming import build_assistant_message_dict
from easy_agent.utils.reasoning_safety import (
    SAFE_REASONING_SUMMARY,
    redact_message_reasoning,
    safe_reasoning_event,
)


def test_raw_reasoning_is_removed_from_sse_storage_and_history():
    secret = "INTERNAL_REASONING_SENTINEL"
    announced: set[int] = set()
    event = safe_reasoning_event(
        {"type": "thinking", "content": secret, "full_content": secret, "step": 1},
        announced,
    )
    assert event["content"] == SAFE_REASONING_SUMMARY
    assert secret not in str(event)
    assert safe_reasoning_event(
        {"type": "thinking", "content": secret, "step": 1}, announced
    ) is None

    stored = build_assistant_message_dict(
        content="answer", thinking=secret, thinking_duration=1.0,
        tool_call_records=[],
        blocks=[{"type": "thinking", "content": secret, "step": 1}],
        input_tokens=1, output_tokens=1, total_tokens=2, context_tokens=1,
        elapsed_time=1.0, step_count=1,
    )
    assert stored["thinking"] is None
    assert stored["blocks"][0]["content"] == SAFE_REASONING_SUMMARY
    assert secret not in str(stored)

    historical = redact_message_reasoning({
        "role": "assistant", "content": "answer", "thinking": secret,
        "blocks": [{"type": "thinking", "content": secret, "step": 1}],
    })
    assert historical["thinking"] is None
    assert secret not in str(historical)
