"""SQLite 单机运行约束回归（PERF-07：WAL/busy_timeout/synchronous）。"""

from easy_agent.db.database import Database


def test_sqlite_pragmas_are_applied(tmp_path):
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / "pragmas.db")}})
    db.init_tables()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode")
        assert str(cursor.fetchone()[0]).lower() == "wal"
        cursor.execute("PRAGMA busy_timeout")
        assert int(cursor.fetchone()[0]) == 30000
        cursor.execute("PRAGMA synchronous")
        # synchronous=NORMAL 对应 1；WAL 下兼顾安全与写入性能
        assert int(cursor.fetchone()[0]) == 1
