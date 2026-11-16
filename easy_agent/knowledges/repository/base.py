"""仓储层通用能力：连接助手、字段白名单过滤与时间/行转换。"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from easy_agent.db.database import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_dict(row: object | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class RepositoryBaseMixin:
    """所有仓储 Mixin 的基座：持有数据库连接并封装单语句读写。"""

    def __init__(self, db: Database):
        self.db = db

    # ---------------- 通用内部辅助 ----------------

    def _fetch_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        """执行单条查询并返回首行；无结果返回 None。"""

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, sql, params)
            return _row_dict(cursor.fetchone())

    def _fetch_all(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """执行单条查询并返回全部行。"""

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, sql, params)
            return [dict(row) for row in cursor.fetchall()]

    def _execute_write(self, sql: str, params: tuple = ()) -> int:
        """执行单条写语句并返回受影响行数。"""

        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, sql, params)
            return int(cursor.rowcount or 0)

    @staticmethod
    def _filter_update_fields(
        fields: Mapping[str, object], allowed: set[str], *, coerce_retryable: bool = False
    ) -> dict[str, object]:
        """按白名单过滤更新字段，补 updated_at，并可选把 retryable 归一为 0/1。"""
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return {}
        if coerce_retryable and "retryable" in updates:
            updates["retryable"] = int(bool(updates["retryable"]))
        updates["updated_at"] = _now()
        return updates

    def _update_by_id(
        self, table: str, row_id: str, allowed: set[str], fields: Mapping[str, object]
    ) -> None:
        """按白名单过滤字段后更新单行，并刷新 updated_at。"""
        updates = self._filter_update_fields(fields, allowed)
        if not updates:
            return
        assignments = ", ".join(f"{column}=?" for column in updates)
        self._execute_write(
            f"UPDATE {table} SET {assignments} WHERE id=?",
            (*updates.values(), row_id),
        )

    def _count(self, sql: str, params: tuple) -> int:
        row = self._fetch_one(sql, params)
        return int(row["count"]) if row is not None else 0
