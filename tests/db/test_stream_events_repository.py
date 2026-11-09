"""多副本 Phase 2A：``stream_events`` repository 顺序/分页/清理语义。"""

from types import SimpleNamespace

from easy_agent.db import schema as schema_module
from easy_agent.db.repositories.stream_events import (
    append_stream_event,
    clear_stream_events,
    delete_stream_events_before,
    list_stream_events,
)
from easy_agent.db.schema import EXPECTED_TABLES, SCHEMA_VERSION


def _frame(event_type: str, **fields) -> str:
    import json

    payload = {"type": event_type, **fields}
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def test_schema_declares_stream_events_table():
    assert SCHEMA_VERSION >= 8
    assert "stream_events" in EXPECTED_TABLES


def test_append_returns_increasing_ids_and_list_preserves_order(db):
    f1 = _frame("start", session_id="s1")
    f2 = _frame("content", content="你好")
    f3 = _frame("done", session_id="s1")

    id1 = append_stream_event(db, "s1", f1)
    id2 = append_stream_event(db, "s1", f2)
    id3 = append_stream_event(db, "s1", f3)

    assert 0 < id1 < id2 < id3

    rows = list_stream_events(db, "s1")
    assert [row["event_id"] for row in rows] == [id1, id2, id3]
    assert [row["payload"] for row in rows] == [f1, f2, f3]
    assert all(row["session_id"] == "s1" for row in rows)
    assert all(row["created_at"] for row in rows)


def test_list_after_id_and_limit_and_session_isolation(db):
    ids = [append_stream_event(db, "s1", _frame("content", content=str(i))) for i in range(5)]
    append_stream_event(db, "s2", _frame("content", content="other"))

    rest = list_stream_events(db, "s1", after_id=ids[1])
    assert [row["event_id"] for row in rest] == ids[2:]
    assert all(row["session_id"] == "s1" for row in rest)

    page = list_stream_events(db, "s1", after_id=0, limit=2)
    assert [row["event_id"] for row in page] == ids[:2]
    assert list_stream_events(db, "s1", limit=0) == []
    assert list_stream_events(db, "missing") == []


def test_clear_stream_events_only_touches_one_session(db):
    append_stream_event(db, "s1", _frame("content", content="old-1"))
    append_stream_event(db, "s1", _frame("content", content="old-2"))
    append_stream_event(db, "s2", _frame("content", content="keep"))

    assert clear_stream_events(db, "s1") == 2
    assert list_stream_events(db, "s1") == []
    assert len(list_stream_events(db, "s2")) == 1
    assert clear_stream_events(db, "s1") == 0


def test_delete_stream_events_before(db):
    append_stream_event(db, "s1", _frame("content", content="a"))
    append_stream_event(db, "s1", _frame("content", content="b"))
    append_stream_event(db, "s2", _frame("content", content="c"))

    assert delete_stream_events_before(db, "0000-01-01T00:00:00") == 0
    assert len(list_stream_events(db, "s1")) == 2

    deleted = delete_stream_events_before(db, "9999-12-31T23:59:59")
    assert deleted == 3
    assert list_stream_events(db, "s1") == []
    assert list_stream_events(db, "s2") == []


def test_mysql_ddl_uses_bigint_autoincrement_and_longtext():
    """双栈建表口径：MySQL 必须 BIGINT AUTO_INCREMENT + LONGTEXT。"""
    executed: list[str] = []

    class _Cursor:
        def execute(self, sql, params=None):  # noqa: ANN001
            executed.append(sql)

    fake_db = SimpleNamespace(db_type="mysql")
    schema_module._create_stream_events_table(fake_db, _Cursor())

    ddl = "\n".join(executed)
    assert "CREATE TABLE IF NOT EXISTS stream_events" in ddl
    assert "BIGINT PRIMARY KEY AUTO_INCREMENT" in ddl
    assert "LONGTEXT NOT NULL" in ddl
    assert "AUTOINCREMENT" not in ddl
