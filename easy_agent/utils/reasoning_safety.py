"""Public-output policy for model reasoning.

Provider chain-of-thought is internal data.  API/SSE consumers receive only a
fixed phase summary; persisted messages and historical reads use the same rule.
"""

from __future__ import annotations

from copy import deepcopy


SAFE_REASONING_SUMMARY = "正在分析并组织回答"


def safe_reasoning_event(event: dict, announced_steps: set[int]) -> dict | None:
    if event.get("type") != "thinking":
        return event
    step = int(event.get("step") or 0)
    if step in announced_steps:
        return None
    announced_steps.add(step)
    return {
        "type": "thinking",
        "content": SAFE_REASONING_SUMMARY,
        "full_content": SAFE_REASONING_SUMMARY,
        "step": step,
    }


def safe_blocks(blocks: list | None) -> list:
    safe: list[dict] = []
    for block in blocks or []:
        item = dict(block)
        if item.get("type") == "thinking":
            item["content"] = SAFE_REASONING_SUMMARY
        safe.append(item)
    return safe


def redact_message_reasoning(message: dict) -> dict:
    """Return a copy safe for API responses and diagnostic context files."""
    safe = deepcopy(message)
    safe["thinking"] = None
    if isinstance(safe.get("blocks"), list):
        safe["blocks"] = safe_blocks(safe["blocks"])
    return safe


def redact_messages_reasoning(messages: list[dict] | None) -> list[dict]:
    return [redact_message_reasoning(item) for item in messages or []]
