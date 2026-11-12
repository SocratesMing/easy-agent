"""Resolve authenticated business identities for model usage without username fallback."""

import logging

from easy_agent.db.providers import get_user_repository
from easy_agent.utils.blocking import run_blocking
from easy_agent.utils.usage_context import UsageIdentity

logger = logging.getLogger(__name__)


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
