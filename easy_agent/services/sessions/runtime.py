"""Cross-replica session runtime: stream occupancy, connectors, edit targets."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from easy_agent.db.repositories import session_runtime as session_runtime_repo
from easy_agent.db.repositories.session_runtime import STREAM_TTL_SECONDS
from easy_agent.utils.deployment import instance_owner

logger = logging.getLogger("easy_agent.session_runtime")


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


class SessionBusyError(RuntimeError):
    """Another replica already holds the stream lock for this session."""


def acquire_stream(db, session_id: str, *, ttl_seconds: int = STREAM_TTL_SECONDS) -> str:
    """Mark this instance as the stream owner. Raises SessionBusyError if held.

    使用 DB 原子条件更新（`try_acquire_session_stream`），避免旧实现
    「先读后写」在跨副本并发时的 TOCTOU：两个实例同时判断“没人持有”后都写入。
    """
    owner = instance_owner()
    now_dt = utc_now()
    until = _iso(now_dt + timedelta(seconds=ttl_seconds))
    now = _iso(now_dt)
    acquired, row = db.try_acquire_session_stream(session_id, owner, until, now)
    if acquired:
        return owner
    held_owner = (row or {}).get("stream_owner") or ""
    # 同进程重复 acquire（旧任务未释放）也视为忙：同会话不应有两条流。
    raise SessionBusyError(
        f"会话 {session_id} 正在实例 {held_owner or 'unknown'} 上生成，请稍后重试"
    )


def renew_stream(
    db, session_id: str, *, ttl_seconds: int = STREAM_TTL_SECONDS
) -> bool:
    """为当前实例持有的流锁续约；owner 已变化时返回 False（锁被接管）。"""
    return session_runtime_repo.renew_stream_lease(
        db, session_id, instance_owner(), ttl_seconds=ttl_seconds
    )


def release_stream(db, session_id: str) -> None:
    owner = instance_owner()
    row = db.get_session_runtime(session_id)
    if not row:
        return
    if row.get("stream_owner") == owner:
        db.upsert_session_runtime(
            session_id, stream_owner="", stream_until="", cancel_requested=0
        )


def is_stream_active(db, session_id: str, *, now: datetime | None = None) -> bool:
    """DB 视角的流活跃判断：``stream_owner`` 非空且 ``stream_until`` 未过期。

    多副本下这是唯一正确的判断依据——进程内 hub/任务表只代表当前 Pod。
    过期未释放的锁（Pod 崩溃等）会在 TTL 到点后自动变为不活跃。
    """
    row = db.get_session_runtime(session_id)
    if not row:
        return False
    owner = str(row.get("stream_owner") or "")
    until = str(row.get("stream_until") or "")
    if not owner or not until:
        return False
    try:
        until_dt = datetime.fromisoformat(until)
    except (TypeError, ValueError):
        return False
    if until_dt.tzinfo is None:
        until_dt = until_dt.replace(tzinfo=UTC)
    current = now or utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    return until_dt > current


def request_cancel(db, session_id: str) -> None:
    db.upsert_session_runtime(session_id, cancel_requested=1)


def load_connectors(db, session_id: str) -> tuple[str, ...] | None:
    row = db.get_session_runtime(session_id)
    if not row or row.get("connectors") is None:
        return None
    raw = row.get("connectors")
    if raw == "":
        return ()
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(data, list):
        return None
    return tuple(str(item) for item in data)


def save_connectors(db, session_id: str, connector_servers: list[str]) -> None:
    payload = json.dumps(list(connector_servers), ensure_ascii=False)
    db.upsert_session_runtime(session_id, connectors=payload)


def clear_connectors(db, session_id: str) -> None:
    db.upsert_session_runtime(session_id, connectors=None, clear_connectors=True)


def save_edit_target(db, session_id: str, expert_agent_id: str) -> None:
    db.upsert_session_runtime(session_id, edit_target=expert_agent_id)
