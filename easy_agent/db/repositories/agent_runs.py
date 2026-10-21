"""一次聊天/恢复 run 的持久化 repository（多副本 Phase 2B）。

``agent_runs`` 记录「哪个 run 正在哪个 Pod 上跑」（``lease_owner`` /
``lease_until``）以及最终状态。租约是**恢复提示**而不是分布式锁：多 Pod 下
同一 ``run_id`` 的正常生命周期仍由会话级流锁（``session_runtime``）串行化；
这里只保证 Pod 崩溃/驱逐后，陈旧 ``running`` 行能被启动恢复标记为
``interrupted``，避免前端一直停在「执行中」。

所有时间均为 ISO 字符串，统一使用 UTC 生成（``+00:00`` 后缀）：``lease_until``
的大小比较必须跨 Pod 可比，不能混入本地时区。连接/事务由传入的 ``Database``
对象提供，SQL 占位符统一用 ``?``（``Database._execute`` 在 MySQL 下自动转 ``%s``）。
"""

from __future__ import annotations

from datetime import UTC, datetime

# run 状态机：running -> completed / failed / cancelled / interrupted（终态不再变更）
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUS_INTERRUPTED = "interrupted"

# 单次 stale 扫描上限：启动恢复按页处理，避免一次加载过多行。
DEFAULT_STALE_LIMIT = 500


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return dict(row) if not isinstance(row, dict) else row


def start_run(
    db,
    run_id: str,
    session_id: str,
    username: str,
    message_id: str,
    request_json: str,
    owner: str,
    lease_until: str,
) -> bool:
    """登记一次 run（幂等：同 ``run_id`` 重复调用不会覆盖既有状态）。

    返回 True 表示本次真正插入了新行；False 表示 ``run_id`` 已存在或写入失败。
    幂等插入而非 UPSERT：客户端重试/重复投递不应把已完成的 run 重新改回
    ``running``（否则会留下永远等不到终态的假运行）。
    """
    now = _now_iso()
    values = (
        run_id,
        session_id,
        username or "",
        message_id or "",
        STATUS_RUNNING,
        request_json or "",
        owner or "",
        lease_until or "",
        now,
        now,
    )
    with db.get_connection() as conn:
        cursor = conn.cursor()
        if db.db_type == "mysql":
            db._execute(
                cursor,
                """
                INSERT INTO agent_runs
                (run_id, session_id, username, message_id, status, request_json,
                 lease_owner, lease_until, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE run_id=VALUES(run_id)
                """,
                values,
            )
        else:
            db._execute(
                cursor,
                """
                INSERT INTO agent_runs
                (run_id, session_id, username, message_id, status, request_json,
                 lease_owner, lease_until, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO NOTHING
                """,
                values,
            )
        return cursor.rowcount > 0


def renew_run(db, run_id: str, owner: str, lease_until: str) -> bool:
    """为仍处于 ``running`` 且 owner 未变的 run 续约；返回是否续约成功。

    owner 条件避免「A Pod 崩溃后 B Pod 已接管恢复」时 A 的残留心跳把租约重新
    写回；状态条件避免给已落终态的行续期。
    """
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE agent_runs SET lease_until=?, updated_at=? "
            "WHERE run_id=? AND lease_owner=? AND status=?",
            (lease_until or "", _now_iso(), run_id, owner or "", STATUS_RUNNING),
        )
        return cursor.rowcount > 0


def finish_run(db, run_id: str, status: str, error: str = "") -> bool:
    """原子落终态：仅当当前状态仍是 ``running`` 时更新。

    返回 True 表示本次写入生效；False 表示 run 不存在或已被其它路径落过终态
    （例如启动恢复先标记了 ``interrupted``）。重复调用幂等：不会覆盖已有终态。
    """
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE agent_runs SET status=?, error=?, lease_until='', updated_at=? "
            "WHERE run_id=? AND status=?",
            (status, error or "", _now_iso(), run_id, STATUS_RUNNING),
        )
        return cursor.rowcount > 0


def get_run(db, run_id: str) -> dict | None:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, "SELECT * FROM agent_runs WHERE run_id=?", (run_id,))
        row = cursor.fetchone()
    return _row_to_dict(row)


def list_session_message_ids(db, session_id: str) -> list[str]:
    """该会话所有 run 的 message_id（去重、去空），用于清理每条消息一个的旧 thread（DA-03）。"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT DISTINCT message_id FROM agent_runs WHERE session_id=?",
            (session_id,),
        )
        rows = cursor.fetchall()
    ids = []
    for row in rows:
        value = row["message_id"] if isinstance(row, dict) else row[0]
        if value:
            ids.append(str(value))
    return ids


def list_stale_running(db, now_iso: str, limit: int = DEFAULT_STALE_LIMIT) -> list[dict]:
    """返回租约已过期的 ``running`` run（``lease_until < now_iso``）。"""
    if limit <= 0:
        return []
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT * FROM agent_runs WHERE status=? AND lease_until<? "
            "ORDER BY lease_until ASC LIMIT ?",
            (STATUS_RUNNING, now_iso or "", int(limit)),
        )
        rows = cursor.fetchall()
    result: list[dict] = []
    for row in rows:
        converted = _row_to_dict(row)
        if converted is not None:
            result.append(converted)
    return result


def mark_interrupted(db, run_id: str, error: str) -> bool:
    """把陈旧的 ``running`` run 标记为 ``interrupted``；多 Pod 竞争只有一个成功。

    条件更新 ``WHERE status='running'`` 是竞争裁决点：``recover_stale_runs``
    只有在返回 True 时才写 ``error`` 事件，因此同一 stale run 不会被多个 Pod
    重复收尾。
    """
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE agent_runs SET status=?, error=?, lease_until='', updated_at=? "
            "WHERE run_id=? AND status=?",
            (STATUS_INTERRUPTED, error or "", _now_iso(), run_id, STATUS_RUNNING),
        )
        return cursor.rowcount > 0



def has_other_live_run(db, session_id: str, run_id: str, now_iso: str) -> bool:
    """同一会话里除 ``run_id`` 以外是否还有租约未过期的 ``running`` run（新流在跑）。"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT run_id FROM agent_runs WHERE session_id=? AND run_id<>? "
            "AND status=? AND lease_until>=? LIMIT 1",
            (session_id, run_id, STATUS_RUNNING, now_iso or ""),
        )
        return cursor.fetchone() is not None


__all__ = [
    "DEFAULT_STALE_LIMIT",
    "STATUS_CANCELLED",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_INTERRUPTED",
    "STATUS_RUNNING",
    "finish_run",
    "get_run",
    "has_other_live_run",
    "list_stale_running",
    "mark_interrupted",
    "renew_run",
    "start_run",
]
