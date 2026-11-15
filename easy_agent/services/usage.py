"""Resolve authenticated business identities for model usage without username fallback."""

import logging
from contextlib import aclosing

from easy_agent.db.providers import get_token_usage_repository, get_user_repository
from easy_agent.services.streaming.sse import format_sse, parse_sse_frame
from easy_agent.utils.blocking import run_blocking
from easy_agent.utils.usage_context import UsageIdentity

logger = logging.getLogger(__name__)


async def _session_token_totals(username: str, session_id: str) -> dict | None:
    try:
        return await run_blocking(
            get_token_usage_repository().session_totals, username, session_id,
        )
    except Exception as exc:  # noqa: BLE001 - usage lookup must not interrupt chat
        logger.warning("session token totals unavailable | error=%s", type(exc).__name__)
        return None


async def session_usage_totals(usage: dict | None, username: str, session_id: str) -> dict | None:
    """Replace only input/output with ledger totals; message usage stays intact."""
    totals = await _session_token_totals(username, session_id)
    totals = totals or {"input_tokens": 0, "output_tokens": 0}
    if usage is None and not any(totals.values()):
        return None
    return {**(usage or {}), **totals}


async def with_session_token_totals(stream, username: str, session_id: str):
    """Publish absolute conversation totals before events are saved/replayed.

    The processor and persisted assistant messages retain per-call snapshots.
    Only the two displayed counts change; context/reasoning/time/steps pass through.
    """
    last_totals = {"input_tokens": 0, "output_tokens": 0}
    async with aclosing(stream):
        async for frame in stream:
            event = parse_sse_frame(frame)
            event_type = event.get("type")
            payload = event if event_type == "token_usage" else event.get("usage")
            if event_type in ("token_usage", "done") and isinstance(payload, dict):
                totals = await _session_token_totals(username, session_id)
                if totals is not None:
                    last_totals = totals
                payload.update(last_totals)
                frame = format_sse(event)
            yield frame


async def resolve_usage_identity(
    username: str, session_id: str | None = None,
) -> UsageIdentity | None:
    """A missing/failed lookup disables collection while preserving the business call."""
    try:
        if username:
            user = await run_blocking(get_user_repository().get_user_by_username, username)
            if user and isinstance(user.user_id, str) and user.user_id:
                return UsageIdentity(user.user_id, session_id)
    except Exception as exc:
        logger.warning("token usage skipped | reason=identity_lookup_failed error=%s", type(exc).__name__)
        return None
    logger.warning("token usage skipped | reason=identity_missing")
    return None
