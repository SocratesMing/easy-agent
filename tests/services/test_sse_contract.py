"""SSE 事件契约测试（ARCH-05）。

- 注册表覆盖所有后端生产者实际发出的事件类型；
- docs/sse-events.md 与注册表一致；
- format_sse 唯一实现格式正确。
"""

import re
from pathlib import Path

from easy_agent.services.sse import SSE_EVENT_TYPES, format_sse, validate_event

ROOT = Path(__file__).resolve().parents[2]

# 后端 SSE 生产者（新增生产者时加入此列表，避免契约漂移）
PRODUCERS = [
    "easy_agent/services/stream_processor.py",
    "easy_agent/services/streaming.py",
    "easy_agent/api/chat.py",
    "easy_agent/api/expert.py",
    "easy_agent/knowledges/chat/streaming.py",
    "easy_agent/knowledges/chat/chat_bridge.py",
]

_TYPE_RE = re.compile(r'"type"\s*:\s*"([a-z_]+)"')
_BLOCK_ONLY_TYPES = {"text", "image_url", "reasoning"}


def _produced_types() -> set[str]:
    found: set[str] = set()
    for rel in PRODUCERS:
        path = ROOT / rel
        if not path.exists():
            continue
        found.update(_TYPE_RE.findall(path.read_text(encoding="utf-8")))
    return found - _BLOCK_ONLY_TYPES


def test_all_produced_event_types_registered():
    produced = _produced_types()
    missing = produced - SSE_EVENT_TYPES
    assert not missing, f"生产端发出但注册表未登记的事件: {sorted(missing)}"


def test_docs_match_registry():
    doc = (ROOT / "docs/sse-events.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", doc, re.M))
    assert documented == set(SSE_EVENT_TYPES), (
        f"docs/sse-events.md 与注册表不一致 | 仅文档: {sorted(documented - SSE_EVENT_TYPES)} "
        f"| 仅代码: {sorted(SSE_EVENT_TYPES - documented)}"
    )


def test_format_sse_is_canonical():
    frame = format_sse({"type": "content", "content": "hi"})
    assert frame == 'data: {"type": "content", "content": "hi"}\n\n'


def test_validate_event_accepts_known_and_rejects_unknown():
    validate_event({"type": "content", "content": "x"})
    try:
        validate_event({"type": "not_a_real_event"})
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("未知事件必须抛 ValueError")


# 仅事件类型（content/thinking/tool_call 同时也是持久化 block 类型，不在此列）
_EVENT_ONLY_LITERALS = (
    '"type": "start"',
    '"type": "content_end"',
    '"type": "tool_result"',
    '"type": "tool_call_delta"',
    '"type": "todo_list"',
    '"type": "token_usage"',
    '"type": "knowledge_evidence"',
    '"type": "approval_required"',
    '"type": "done"',
    '"type": "error"',
    '"type": "thinking_start"',
    '"type": "thinking_end"',
)


def test_producers_use_event_constants_and_no_literals():
    for rel in PRODUCERS:
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        has_literal = any(literal in text for literal in _EVENT_ONLY_LITERALS)
        for literal in _EVENT_ONLY_LITERALS:
            assert literal not in text, f"{rel} 仍存在事件字面量 {literal}；请改用 SSEEvent.*"
        if has_literal or "SSEEvent" in text:
            assert "SSEEvent" in text, f"{rel} 未引用 SSEEvent 常量（ARCH-05 漂移风险）"
