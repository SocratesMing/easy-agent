"""SSE 事件契约测试（ARCH-05 / C3）。

- 事件名集合 = 注册表 = ``SSEEvent`` 常量；
- ``docs/sse-events.md`` 与注册表一致；
- 生产者不手写 frame：只有 ``services/sse.py`` 可以出现 ``data: {...}``；
- 生产者引用的事件常量全部在注册表中，且注册表里的每个事件都有生产端引用；
- ``error`` / ``approval_required`` / ``done`` 的字段契约（含字段顺序）一致；
- 迁移后的生产者 frame 与迁移前逐字节等价。
"""

import ast
import re
from pathlib import Path

import pytest

from easy_agent.services.sse import (
    EVENT_SPECS,
    SSE_EVENT_TYPES,
    SSEEvent,
    TERMINAL_EVENT_TYPES,
    format_sse,
    is_terminal_event,
    parse_sse_frame,
    validate_event,
)
from easy_agent.services.sse_emitter import SSEEmitter, sse_stream

ROOT = Path(__file__).resolve().parents[2]

# 后端 SSE 生产者（新增生产者时加入此列表，避免契约漂移）。
# P1 编排器 / stream_processor 产事件 dict；sse_emitter 是统一出口；
# chat_bridge 只产 knowledge_evidence 事件 dict。
PRODUCERS = [
    "easy_agent/services/stream_processor.py",
    "easy_agent/services/streaming/orchestrator.py",
    "easy_agent/services/sse_emitter.py",
    "easy_agent/api/chat.py",
    "easy_agent/api/expert.py",
    "easy_agent/knowledges/chat/streaming.py",
    "easy_agent/knowledges/chat/chat_bridge.py",
]

_TYPE_RE = re.compile(r'"type"\s*:\s*"([a-z_]+)"')
_BLOCK_ONLY_TYPES = {"text", "image_url", "reasoning"}

# 迁移前 _REQUIRED_FIELDS 的逐项快照；C3 补登 approval_required 的两个必填字段
# （validate_event 仅测试/debug 使用，生产路径不调用，不改变 wire 行为）。
_EXPECTED_REQUIRED = {
    "start": ("session_id",),
    "thinking": ("content",),
    "content": ("content",),
    "tool_call": ("tool_name", "tool_call_id"),
    "tool_result": ("tool_name", "tool_call_id"),
    "approval_required": ("thread_id", "action_requests"),
    "done": ("session_id",),
    "error": ("content",),
}


def _produced_literal_types() -> set[str]:
    found: set[str] = set()
    for rel in PRODUCERS:
        path = ROOT / rel
        if not path.exists():
            continue
        found.update(_TYPE_RE.findall(path.read_text(encoding="utf-8")))
    return found - _BLOCK_ONLY_TYPES


def _referenced_event_constants() -> dict[str, set[str]]:
    """AST 扫描生产者引用的 ``SSEEvent.<NAME>`` 常量（忽略注释/字符串）。"""
    found: dict[str, set[str]] = {}
    for rel in PRODUCERS:
        path = ROOT / rel
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
        names = {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "SSEEvent"
        }
        found[rel] = names
    return found


# ── 注册表 ────────────────────────────────────────────────────────────


def test_event_constants_match_registry():
    constants = {
        name: value
        for name, value in vars(SSEEvent).items()
        if name.isupper() and isinstance(value, str)
    }
    assert set(constants.values()) == set(SSE_EVENT_TYPES)
    assert len(constants) == len(SSE_EVENT_TYPES)
    for name, value in constants.items():
        assert name.lower() == value, f"SSEEvent.{name} 应为 {name.lower()!r}，实际 {value!r}"


def test_registry_required_fields_match_legacy():
    actual = {
        name: spec.required for name, spec in EVENT_SPECS.items() if spec.required
    }
    assert actual == _EXPECTED_REQUIRED


def test_terminal_event_types():
    assert TERMINAL_EVENT_TYPES == {"done", "error", "approval_required"}
    assert {
        name for name, spec in EVENT_SPECS.items() if spec.terminal
    } == TERMINAL_EVENT_TYPES
    for payload in (
        {"type": "done", "session_id": "s"},
        {"type": "error", "content": "x"},
        {"type": "approval_required", "thread_id": "t", "action_requests": []},
    ):
        assert is_terminal_event(payload)
        assert is_terminal_event(format_sse(payload))
    assert not is_terminal_event({"type": "content", "content": "x"})
    assert not is_terminal_event(
        format_sse({"type": "content", "content": "x"})
    )


def test_docs_match_registry():
    doc = (ROOT / "docs/sse-events.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"^\| `([a-z_]+)` \|", doc, re.M))
    assert documented == set(SSE_EVENT_TYPES), (
        f"docs/sse-events.md 与注册表不一致 | 仅文档: {sorted(documented - SSE_EVENT_TYPES)} "
        f"| 仅代码: {sorted(SSE_EVENT_TYPES - documented)}"
    )


# ── 序列化 / 反序列化 ─────────────────────────────────────────────────


def test_format_sse_is_canonical():
    frame = format_sse({"type": "content", "content": "hi"})
    assert frame == 'data: {"type": "content", "content": "hi"}\n\n'


def test_parse_sse_frame_roundtrip_preserves_field_order():
    payload = {"type": "content", "content": "中文", "step": 0}
    parsed = parse_sse_frame(format_sse(payload))
    assert parsed == payload
    assert list(parsed) == list(payload)
    # 宽容：无空格前缀 / 尾部空白 / 非 data 行纯 JSON
    assert parse_sse_frame('data:{"type":"done","session_id":"s"}') == {
        "type": "done",
        "session_id": "s",
    }
    assert parse_sse_frame('  data: {"type": "error", "content": "x"}\r\n\r\n') == {
        "type": "error",
        "content": "x",
    }


def test_validate_event_accepts_known_and_rejects_unknown():
    validate_event({"type": "content", "content": "x"})
    with pytest.raises(ValueError):
        validate_event({"type": "not_a_real_event"})
    with pytest.raises(ValueError):
        validate_event({"type": "done"})  # 缺 session_id


# ── 生产者不手写 frame / 事件名不漂移 ─────────────────────────────────


def test_all_produced_event_types_registered():
    produced = _produced_literal_types()
    missing = produced - SSE_EVENT_TYPES
    assert not missing, f"生产端发出但注册表未登记的事件: {sorted(missing)}"


def test_producer_event_constants_registered_and_all_used():
    referenced = _referenced_event_constants()
    constant_names = {
        name for name in vars(SSEEvent) if name.isupper() and not name.startswith("_")
    }
    used_types: set[str] = set()
    for rel, names in referenced.items():
        unknown = names - constant_names
        assert not unknown, f"{rel} 引用了未定义的 SSEEvent 常量: {sorted(unknown)}"
        used_types |= {getattr(SSEEvent, name) for name in names}
    assert used_types == set(SSE_EVENT_TYPES), (
        "注册表事件与生产端引用不一致 | 无生产端: "
        f"{sorted(SSE_EVENT_TYPES - used_types)} | 未登记: {sorted(used_types - SSE_EVENT_TYPES)}"
    )


def test_only_sse_module_builds_frames():
    offenders: list[str] = []
    for rel in PRODUCERS:
        path = ROOT / rel
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "data: {" in text or 'f"data:' in text:
            offenders.append(rel)
    assert not offenders, f"以下生产者手写 SSE frame，应改用 format_sse/SSEEmitter: {offenders}"


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
        for literal in _EVENT_ONLY_LITERALS:
            assert literal not in text, f"{rel} 仍存在事件字面量 {literal}；请改用 SSEEvent.*"


# ── SSEEmitter 契约 ──────────────────────────────────────────────────


def test_emitter_matches_format_sse_byte_for_byte():
    emitter = SSEEmitter("sid")
    cases = [
        (emitter.start(), {"type": "start", "session_id": "sid"}),
        (
            emitter.content("hi", step=2),
            {"type": "content", "content": "hi", "step": 2},
        ),
        (
            emitter.content("no-step"),
            {"type": "content", "content": "no-step"},
        ),
        (emitter.content_end(), {"type": "content_end", "content": ""}),
        (emitter.error("boom"), {"type": "error", "content": "boom"}),
        (
            emitter.done(already_completed=True),
            {
                "type": "done",
                "session_id": "sid",
                "already_completed": True,
            },
        ),
    ]
    for frame, payload in cases:
        assert frame == format_sse(payload)
        assert list(parse_sse_frame(frame)) == list(payload)


def test_emitter_requires_session_id_for_start_and_done():
    with pytest.raises(ValueError):
        SSEEmitter().done()
    with pytest.raises(ValueError):
        SSEEmitter().start()


def test_emitter_error_done_approval_field_contract():
    emitter = SSEEmitter("s-1")
    error_payload = parse_sse_frame(emitter.error("处理失败: boom"))
    assert list(error_payload) == ["type", "content"]
    validate_event(error_payload)

    done_payload = parse_sse_frame(
        emitter.done(elapsed_time=1.23, usage={"input_tokens": 1})
    )
    assert list(done_payload) == ["type", "session_id", "elapsed_time", "usage"]
    validate_event(done_payload)

    approval_payload = parse_sse_frame(
        emitter.approval_required(
            thread_id="t-1",
            action_requests=[{"name": "delete_file"}],
            blocks=[],
        )
    )
    assert list(approval_payload) == [
        "type",
        "thread_id",
        "action_requests",
        "blocks",
    ]
    validate_event(approval_payload)


def test_emitter_knowledge_evidence_skips_empty_payload():
    emitter = SSEEmitter("s-1")
    assert emitter.knowledge_evidence([], []) is None
    frame = emitter.knowledge_evidence([{"snippet": "x"}], [])
    assert parse_sse_frame(frame) == {
        "type": "knowledge_evidence",
        "evidence": [{"snippet": "x"}],
        "warnings": [],
    }


def test_emitter_validate_true_rejects_unknown_event():
    emitter = SSEEmitter(validate=True)
    with pytest.raises(ValueError):
        emitter.emit("not_a_real_event")


# ── sse_stream ────────────────────────────────────────────────────────


async def test_sse_stream_sync_iterable_preserves_order():
    events = [
        {"type": "start", "session_id": "s"},
        {"type": "done", "session_id": "s"},
    ]
    frames = [f async for f in sse_stream(events, emitter=SSEEmitter())]
    assert frames == [format_sse(events[0]), format_sse(events[1])]


async def test_sse_stream_async_iterable():
    async def _events():
        yield {"type": "content", "content": "a", "step": 0}
        yield {"type": "content_end", "content": ""}

    frames = [f async for f in sse_stream(_events(), emitter=SSEEmitter())]
    assert [parse_sse_frame(f)["type"] for f in frames] == [
        "content",
        "content_end",
    ]


async def test_sse_stream_rejects_raw_strings():
    with pytest.raises(TypeError):
        async for _ in sse_stream("data: {}"):  # type: ignore[arg-type]
            pass


# ── 迁移后生产者的逐字节等价证据 ─────────────────────────────────────


async def test_chat_busy_error_frame_unchanged(monkeypatch):
    """并发兜底 error 帧：与迁移前的字面量逐字节一致。"""
    from easy_agent.api import chat as chat_api

    monkeypatch.setattr(chat_api, "is_session_streaming", lambda sid: True)
    frames = [
        frame
        async for frame in chat_api._detached_event_stream(
            None, "session-1", "sid"
        )
    ]
    assert frames == [
        'data: {"type": "error", "content": "本会话正在生成回复，请等待完成或先取消当前生成"}\n\n'
    ]


async def test_expert_refusal_frames_unchanged():
    """专家拒答流：事件顺序、字段名与字段顺序与迁移前一致。"""
    from easy_agent.api import expert as expert_api

    class _Repo:
        def __init__(self):
            self.blob = []
            self.row = []

        def update_last_assistant_message(self, session_id, message):
            self.blob.append(message)

        def update_last_assistant_message_row(self, session_id, message):
            self.row.append(message)

    repo = _Repo()
    frames = [
        frame
        async for frame in expert_api._refusal_event_stream(
            "session-abc", repo, "请提问我领域内的问题"
        )
    ]
    payloads = [parse_sse_frame(frame) for frame in frames]

    assert [p["type"] for p in payloads] == [
        "start",
        "content",
        "content_end",
        "done",
    ]
    assert frames[0] == format_sse(
        {"type": "start", "session_id": "session-abc"}
    )
    assert list(payloads[0]) == ["type", "session_id"]
    assert list(payloads[1]) == ["type", "content", "step"]
    assert list(payloads[2]) == ["type", "content"]
    assert list(payloads[3]) == [
        "type",
        "session_id",
        "elapsed_time",
        "usage",
    ]
    assert list(payloads[3]["usage"]) == [
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "context_length",
        "auto_compress_tokens",
        "context_tokens",
        "step_count",
    ]
    # 落库发生在推送之前（行为不变）
    assert len(repo.blob) == 1 and len(repo.row) == 1
    assert repo.blob[0]["content"] == "请提问我领域内的问题"


def test_sse_event_spec_is_frozen():
    from dataclasses import FrozenInstanceError

    spec = EVENT_SPECS["done"]
    with pytest.raises(FrozenInstanceError):
        spec.terminal = False  # type: ignore[misc]


def test_emitter_session_id_property():
    assert SSEEmitter("s").session_id == "s"
    assert SSEEmitter().session_id is None


# ── C3b：orchestrator 迁移到 SSEEmitter 的证据 ──────────────────────


def test_orchestrator_emits_frames_only_through_sse_emitter():
    """编排器不再手写 "data: " 帧，也不直接调用 format_sse。"""
    text = (ROOT / "easy_agent/services/streaming/orchestrator.py").read_text(
        encoding="utf-8"
    )
    assert "data:" not in text
    assert "format_sse" not in text
    assert "SSEEmitter(" in text


def test_orchestrator_event_name_set_unchanged():
    """编排器可发出的事件名集合固定为 6 个（其余事件经 frame 透传）。

    C3b 迁移后，start/content_end/done/error 走 ``SSEEmitter`` 快捷方法，
    approval_required（chat）也走快捷方法；只有需要显式排序的两处仍直接引用
    ``SSEEvent`` 常量（``THINKING_END``、resume 的 ``APPROVAL_REQUIRED``）。
    """
    referenced = _referenced_event_constants()
    assert referenced["easy_agent/services/streaming/orchestrator.py"] == {
        "APPROVAL_REQUIRED",
        "THINKING_END",
    }
    emitter = SSEEmitter("s")
    produced = {
        parse_sse_frame(emitter.start())["type"],
        parse_sse_frame(emitter.content_end())["type"],
        parse_sse_frame(emitter.error("x"))["type"],
        parse_sse_frame(emitter.done())["type"],
        parse_sse_frame(
            emitter.approval_required(thread_id="t", action_requests=[])
        )["type"],
        SSEEvent.THINKING_END,
    }
    assert produced == {
        "start",
        "thinking_end",
        "content_end",
        "approval_required",
        "error",
        "done",
    }
    assert produced <= SSE_EVENT_TYPES
