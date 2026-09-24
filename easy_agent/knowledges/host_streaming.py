"""Adapt knowledge context and citations to the upstream streaming contract."""

import json

from ..services.streaming import (
    build_assistant_message_dict as host_message,
    chat_stream_generator as host_stream,
    format_sse,
)


def build_assistant_message_dict(*, extra_fields=None, total_tokens=None, **kwargs):
    message = host_message(**kwargs)
    message.update(extra_fields or {})
    return message


async def chat_stream_generator(*, context_prefix=None, initial_events=None,
                                assistant_metadata=None, **kwargs):
    content = kwargs.get("parsed_content") or kwargs["request"].message
    if context_prefix:
        kwargs["parsed_content"] = f"{context_prefix}\n\n## 用户当前问题\n{content}"
    stream = host_stream(**kwargs)

    def persist_metadata():
        if not assistant_metadata:
            return
        db, session_id = kwargs["db"], kwargs["session_id"]
        session = db.get_session(session_id)
        if session and session.messages and session.messages[-1].get("role") == "assistant":
            message = {**session.messages[-1], **assistant_metadata}
            db.update_last_assistant_message(session_id, message)
            db.update_last_assistant_message_row(session_id, message)

    try:
        first = True
        async for event in stream:
            if assistant_metadata:
                payload = json.loads(event.removeprefix("data: ").strip())
                if payload.get("type") in {"done", "error", "approval_required"}:
                    persist_metadata()
            yield event
            if first:
                first = False
                for initial in initial_events or []:
                    yield format_sse(initial)
    finally:
        await stream.aclose()
        persist_metadata()
