"""Collect complete provider usage once per model call, independent of chat messages.

The adapter transports snapshots in generation_info, as lists without an index:
langchain-core merges those lists without adding token counts or timestamps.
Contract: langchain-core 1.6.12 callbacks/manager.py and outputs/chat_generation.py;
langchain-openai 1.2.2 chat_models/base.py (revalidate when upgrading).
"""

import logging
import threading
import time
from dataclasses import asdict, dataclass
from uuid import uuid4

from langchain_core.callbacks import BaseCallbackHandler

from easy_agent.db.providers import get_token_usage_repository
from easy_agent.utils.usage_context import UsageIdentity, get_usage_identity

logger = logging.getLogger(__name__)
USAGE_KEY = "easy_agent_usage"


def observed_at_ms() -> int:
    return time.time_ns() // 1_000_000


def explicit_fields(value) -> dict:
    """Never treat SDK defaults as evidence of fields reported by the provider."""
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_unset=True)
    return {}


def complete_usage_snapshot(usage) -> dict | None:
    raw = explicit_fields(usage)
    counts = (raw.get("prompt_tokens"), raw.get("completion_tokens"))
    if not all(type(value) is int and value >= 0 for value in counts):
        return None
    return {
        "input_tokens": counts[0],
        "output_tokens": counts[1],
        "completed_at_ms": observed_at_ms(),
    }


@dataclass(frozen=True)
class UsageEvent:
    event_id: str
    user_id: str
    session_id: str | None
    model_key: str
    model_name: str
    completed_at_ms: int
    input_tokens: int
    output_tokens: int


def _event(snapshot, identity, *, event_id, model_key, model_name) -> UsageEvent | None:
    if identity is None:
        logger.debug("Token usage skipped | reason=missing_identity | model=%s", model_key)
        return None
    if snapshot is None:
        logger.info("Token usage skipped | reason=incomplete_usage | model=%s", model_key)
        return None
    return UsageEvent(
        event_id=event_id, user_id=identity.user_id, session_id=identity.session_id,
        model_key=model_key, model_name=model_name, **snapshot,
    )


def capture_sdk_usage(completion, *, model_key: str, model_name: str) -> UsageEvent | None:
    """Capture before validating the business result, flush outside its timeout."""
    raw = explicit_fields(completion)
    return _event(
        complete_usage_snapshot(raw.get("usage")), get_usage_identity(),
        event_id=f"sdk:{uuid4()}", model_key=model_key, model_name=model_name,
    )


def write_usage_event(event: UsageEvent | None) -> None:
    if event is None:
        return
    try:
        get_token_usage_repository().record_usage(**asdict(event))
    except Exception as exc:  # noqa: BLE001 - accounting cannot replace a business result
        logger.error(
            "Token usage write failed | reason=database_error | event_id=%s | model=%s/%s | error=%s",
            event.event_id, event.model_key, event.model_name, type(exc).__name__,
        )


def _final_snapshot(response) -> dict | None:
    last = None
    for group in getattr(response, "generations", ()):
        for generation in group:
            info = getattr(generation, "generation_info", None) or {}
            snapshots = info.pop(USAGE_KEY, [])
            # LangChain copies generation_info into message metadata. Keep this
            # internal evidence out of persisted final messages/API responses.
            message = getattr(generation, "message", None)
            if message is not None:
                message.response_metadata.pop(USAGE_KEY, None)
            for snapshot in snapshots if isinstance(snapshots, list) else []:
                if isinstance(snapshot, dict) and all(
                    type(snapshot.get(key)) is int and snapshot[key] >= 0
                    for key in ("input_tokens", "output_tokens", "completed_at_ms")
                ):
                    last = snapshot
    return last


class TokenUsageCallback(BaseCallbackHandler):
    """Shared-model callback: freeze scope at start, release the lock before IO.

    LangChain's async dispatcher copies contextvars into its executor. A real
    dispatcher contract test guards that behavior; end never reads ambient scope.
    """

    run_inline = False

    def __init__(self, model_key: str, model_name: str):
        self.model_key = model_key
        self.model_name = model_name
        self._pending: dict[object, UsageIdentity | None] = {}
        self._lock = threading.Lock()

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        with self._lock:
            self._pending[run_id] = get_usage_identity()

    def _finish(self, response, run_id):
        with self._lock:
            if run_id not in self._pending:
                return
            identity = self._pending.pop(run_id)
        snapshot = _final_snapshot(response)
        write_usage_event(_event(
            snapshot, identity, event_id=f"lc:{run_id}",
            model_key=self.model_key, model_name=self.model_name,
        ))

    def on_llm_end(self, response, *, run_id, **kwargs):
        self._finish(response, run_id)

    def on_llm_error(self, error, *, run_id, **kwargs):
        self._finish(kwargs.get("response"), run_id)
