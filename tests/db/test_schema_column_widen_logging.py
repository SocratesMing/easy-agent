"""MySQL 列加宽失败必须留 warning、删废弃索引失败留 debug（T1 吞错修复）。

``sessions.messages`` -> LONGTEXT、``session_messages.content`` / ``extra_data`` ->
MEDIUMTEXT 是按需执行的 MySQL DDL；原实现 ``except Exception: pass``，锁等待超时 /
权限不足时列没加宽都看不出来，要等到之后写长消息报 1406 Data too long 才暴露。
这里锁定三件事：失败时记 warning（写清表.列与异常）、不抛给调用方（启动流程继续）、
删废弃索引失败（索引本就不存在的正常情况）只记 debug。
"""

from __future__ import annotations

import logging

import easy_agent.db.schema as schema


class _FakeCursor:
    """MySQL 风格 cursor：SHOW COLUMNS 返回加宽前的旧类型，命中 fail_markers 的 SQL 抛错。"""

    def __init__(
        self,
        *,
        column_types: dict[str, dict[str, str]] | None = None,
        fail_markers: tuple[str, ...] = ("MODIFY COLUMN",),
        error: str = "模拟锁等待超时",
    ) -> None:
        self.column_types = column_types or {}
        self.fail_markers = fail_markers
        self.error = error
        self.executed: list[str] = []
        self._rows: list[tuple[str, str]] = []

    def execute(self, sql, params=None):
        self.executed.append(sql)
        self._rows = []
        if sql.startswith("SHOW COLUMNS FROM "):
            table = sql.rsplit(" ", 1)[-1]
            self._rows = list(self.column_types.get(table, {}).items())
        elif any(marker in sql for marker in self.fail_markers):
            raise RuntimeError(self.error)

    def fetchall(self):
        return list(self._rows)


class _FakeMySQLDatabase:
    db_type = "mysql"

    def __init__(self) -> None:
        self._column_cache: dict[str, set[str]] = {}
        self._column_type_cache: dict[str, dict[str, str]] = {}


def _old_column_types() -> dict[str, dict[str, str]]:
    """加宽前的旧类型，保证两处 MODIFY 都会被触发。"""
    return {
        "sessions": {
            "session_id": "varchar(255)",
            "title": "text",
            "messages": "text",
            "created_at": "varchar(50)",
            "updated_at": "varchar(50)",
            "username": "varchar(255)",
            "workspace_name": "varchar(255)",
            "pinned": "int",
            "todos": "text",
            "message_count": "int",
        },
        "session_messages": {
            "id": "bigint",
            "session_id": "varchar(255)",
            "role": "varchar(20)",
            "content": "text",
            "extra_data": "text",
            "created_at": "varchar(50)",
        },
    }


def _warning_messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_sessions_messages_widen_failure_logs_warning_and_keeps_going(caplog):
    db = _FakeMySQLDatabase()
    cursor = _FakeCursor(column_types=_old_column_types())

    with caplog.at_level(logging.WARNING, logger="easy_agent.db.schema"):
        schema._create_sessions_table(db, cursor, "LONGTEXT")  # 不抛异常 = 启动不中断

    messages = _warning_messages(caplog)
    assert any("sessions.messages" in m for m in messages), messages
    assert any("模拟锁等待超时" in m for m in messages), messages
    # 加宽失败不改变执行流：后面的索引照建。
    assert any("idx_sessions_user_list" in sql for sql in cursor.executed)


def test_session_messages_widen_failure_logs_warning_per_column(caplog):
    db = _FakeMySQLDatabase()
    cursor = _FakeCursor(column_types=_old_column_types())

    with caplog.at_level(logging.WARNING, logger="easy_agent.db.schema"):
        schema._create_session_messages_table(db, cursor, "AUTO_INCREMENT")  # 不抛异常

    messages = _warning_messages(caplog)
    assert any("session_messages.content" in m for m in messages), messages
    assert any("session_messages.extra_data" in m for m in messages), messages
    # 两列各一条 warning，且目标类型写清楚。
    mediumtext_warnings = [m for m in messages if "MEDIUMTEXT" in m]
    assert len(mediumtext_warnings) == 2, messages


def test_drop_retired_index_failure_logs_debug(caplog):
    db = _FakeMySQLDatabase()
    cursor = _FakeCursor(fail_markers=("DROP INDEX",), error="索引不存在")

    with caplog.at_level(logging.DEBUG, logger="easy_agent.db.schema"):
        schema._drop_retired_indexes(db, cursor)  # 索引不存在是目标状态，不抛异常

    debug_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("idx_updated_at" in m for m in debug_messages), debug_messages
    assert len(cursor.executed) == len(schema._RETIRED_INDEXES)
