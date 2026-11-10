"""会话运行期状态 repository（ARCH-03 Phase 2 第一步）。

从 `Database` 中抽出 session_runtime 相关 SQL：连接/事务仍由传入的 db 对象
提供，repository 只负责该领域的读写，避免 `Database` 继续膨胀。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

#: 会话流锁的租期（秒）：持有者崩溃后最多这么久，别的实例就能接管
STREAM_TTL_SECONDS = 30 * 60


def get_session_runtime(db, session_id: str) -> dict | None:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor, "SELECT * FROM session_runtime WHERE session_id=?", (session_id,)
        )
        row = cursor.fetchone()
    if not row:
        return None
    return dict(row) if not isinstance(row, dict) else row


def try_acquire_session_stream(
    db,
    session_id: str,
    owner: str,
    until_iso: str,
    now_iso: str,
) -> tuple[bool, dict | None]:
    """原子抢占会话流锁；成功返回 (True, None)，被占用返回 (False, row)。"""

    def _read_row(cursor) -> dict | None:
        db._execute(
            cursor,
            "SELECT * FROM session_runtime WHERE session_id=?",
            (session_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return dict(row) if not isinstance(row, dict) else row

    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            UPDATE session_runtime
            SET stream_owner=?, stream_until=?, cancel_requested=0, updated_at=?
            WHERE session_id=?
              AND (stream_owner IS NULL OR stream_owner='' OR stream_until < ?)
            """,
            (owner, until_iso, now_iso, session_id, now_iso),
        )
        if cursor.rowcount > 0:
            return True, None

        row = _read_row(cursor)
        if row is not None:
            return False, row

        try:
            db._execute(
                cursor,
                """
                INSERT INTO session_runtime
                (session_id, connectors, edit_target, stream_owner, stream_until,
                 cancel_requested, updated_at)
                VALUES (?, NULL, '', ?, ?, 0, ?)
                """,
                (session_id, owner, until_iso, now_iso),
            )
            return True, None
        except Exception:  # noqa: BLE001 - 唯一键冲突（并发初始化）
            return False, _read_row(cursor)


def renew_session_stream(
    db, session_id: str, owner: str, until_iso: str, now_iso: str
) -> bool:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE session_runtime SET stream_until=?, updated_at=? "
            "WHERE session_id=? AND stream_owner=? AND stream_until>?",
            (until_iso, now_iso, session_id, owner, now_iso),
        )
        return cursor.rowcount > 0


def renew_stream_lease(
    db, session_id: str, owner: str, *, ttl_seconds: int = STREAM_TTL_SECONDS
) -> bool:
    """为 ``owner`` 持有的流锁续约一个租期；owner 已变化时返回 False（锁被接管）。

    services 层的 ``session_runtime.renew_stream`` 和仓储的 ``renew_stream`` 心跳都走这里。
    """
    now = datetime.now(UTC)
    return renew_session_stream(
        db,
        session_id,
        owner,
        (now + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds"),
        now.isoformat(timespec="seconds"),
    )


def release_session_stream(db, session_id: str, owner: str, now_iso: str) -> bool:
    """条件释放会话流锁：只有锁仍归 ``owner`` 时才清空，返回是否释放了。"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE session_runtime SET stream_owner='', stream_until='', "
            "cancel_requested=0, updated_at=? WHERE session_id=? AND stream_owner=?",
            (now_iso, session_id, owner),
        )
        return cursor.rowcount > 0


def upsert_session_runtime(
    db,
    session_id: str,
    *,
    connectors: Any = None,
    edit_target: Any = None,
    stream_owner: Any = None,
    stream_until: Any = None,
    cancel_requested: Any = None,
    clear_connectors: bool = False,
) -> None:
    # 只更新显式字段，避免先读整行再写回时覆盖新取得的租约。
    fields: dict[str, Any] = {"updated_at": datetime.now(UTC).isoformat()}
    for name, value in (("connectors", connectors), ("edit_target", edit_target),
                        ("stream_owner", stream_owner), ("stream_until", stream_until),
                        ("cancel_requested", cancel_requested)):
        if value is not None:
            fields[name] = int(value) if name == "cancel_requested" else value
    if clear_connectors:
        fields["connectors"] = None
    columns = ["session_id", *fields]
    updates = ", ".join(f"{key}=VALUES({key})" if db.db_type == "mysql"
                        else f"{key}=excluded.{key}" for key in fields)
    suffix = "ON DUPLICATE KEY UPDATE" if db.db_type == "mysql" else "ON CONFLICT(session_id) DO UPDATE SET"
    with db.get_connection() as conn:
        db._execute(conn.cursor(),
                    f"INSERT INTO session_runtime ({', '.join(columns)}) "
                    f"VALUES ({', '.join('?' for _ in columns)}) {suffix} {updates}",
                    (session_id, *fields.values()))


class SessionRuntimeRepository:
    """会话运行期状态（``session_runtime`` 表）repository。

    覆盖流锁（原子抢占 / 续约 / 条件释放）、连接器开关缓存与待修改专家绑定。
    :mod:`easy_agent.services.sessions.runtime` 的函数只调用同名方法，因此服务层
    可以直接接收本对象（duck typing），不必再依赖 ``Database``。

    方法签名写全；SQL、事务边界与状态语义仍在模块级函数里，本类不改变它们。
    """

    def __init__(self, db: Any) -> None:
        self._db = db

    def guarded_commit(self, session_id: str, owner: str, commit) -> Any:
        """持有业务库行写锁直到 checkpoint 提交结束，堵住检查与写入间的接管窗口。

        commit 必须只访问独立 checkpoint 库；本函数在线程运行，避免阻塞事件循环。
        UPDATE 同时验证每次操作独立的 owner、未过期和未取消。
        """
        now = datetime.now(UTC).isoformat(timespec="microseconds")
        with self._db.get_connection() as conn:
            cursor = conn.cursor()
            self._db._execute(
                cursor,
                "UPDATE session_runtime SET updated_at=? WHERE session_id=? "
                "AND stream_owner=? AND stream_until>? AND cancel_requested=0",
                (now, session_id, owner, now),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("压缩租约已丢失或操作已取消，未提交")
            return commit()

    def get_session_runtime(self, session_id: str) -> dict | None:
        return get_session_runtime(self._db, session_id)

    def try_acquire_session_stream(
        self,
        session_id: str,
        owner: str,
        until_iso: str,
        now_iso: str,
    ) -> tuple[bool, dict | None]:
        """原子抢占会话流锁；成功返回 ``(True, None)``，被占用返回 ``(False, row)``。"""
        return try_acquire_session_stream(
            self._db, session_id, owner, until_iso, now_iso
        )

    def renew_session_stream(
        self, session_id: str, owner: str, until_iso: str, now_iso: str
    ) -> bool:
        return renew_session_stream(self._db, session_id, owner, until_iso, now_iso)

    def renew_stream_lease(
        self, session_id: str, owner: str, *, ttl_seconds: int = STREAM_TTL_SECONDS
    ) -> bool:
        """为 ``owner`` 持有的流锁续约一个租期；owner 已变化时返回 False。"""
        return renew_stream_lease(
            self._db, session_id, owner, ttl_seconds=ttl_seconds
        )

    def release_session_stream(
        self, session_id: str, owner: str, now_iso: str
    ) -> bool:
        """条件释放会话流锁：只有锁仍归 ``owner`` 时才清空。"""
        return release_session_stream(self._db, session_id, owner, now_iso)

    def upsert_session_runtime(
        self,
        session_id: str,
        *,
        connectors: Any = None,
        edit_target: Any = None,
        stream_owner: Any = None,
        stream_until: Any = None,
        cancel_requested: Any = None,
        clear_connectors: bool = False,
    ) -> None:
        """按字段部分更新（未传的字段保留原值）；``clear_connectors`` 清空连接器缓存。"""
        upsert_session_runtime(
            self._db,
            session_id,
            connectors=connectors,
            edit_target=edit_target,
            stream_owner=stream_owner,
            stream_until=stream_until,
            cancel_requested=cancel_requested,
            clear_connectors=clear_connectors,
        )

