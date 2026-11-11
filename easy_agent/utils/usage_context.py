"""Immutable per-execution billing identity; no database or service dependencies."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class UsageIdentity:
    user_id: str
    session_id: str | None = None


_identity: ContextVar[UsageIdentity | None] = ContextVar("token_usage_identity", default=None)


def get_usage_identity() -> UsageIdentity | None:
    return _identity.get()


@contextmanager
def usage_scope(user_id: str | None, session_id: str | None = None):
    """``None`` explicitly disables collection, including an inherited identity."""
    token = _identity.set(UsageIdentity(user_id, session_id) if user_id else None)
    try:
        yield
    finally:
        _identity.reset(token)
