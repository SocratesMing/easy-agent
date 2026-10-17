"""版本化迁移框架测试（ARCH-16）。"""

from easy_agent.db import migrations as mig
from easy_agent.db.database import Database


def _db(path) -> Database:
    db = Database({"type": "sqlite", "sqlite": {"path": str(path)}})
    db.init_tables()
    return db


def _versions(db):
    with db.get_connection() as conn:
        return mig.applied_versions(db, conn.cursor())


def test_baseline_recorded_on_first_init(tmp_path):
    db = _db(tmp_path / "m.db")
    # 引入迁移框架时已有 schema 版本记为 baseline（5）
    assert _versions(db) == [mig.BASELINE_VERSION]


def test_pending_migration_applied_once(tmp_path, monkeypatch):
    path = tmp_path / "m2.db"
    db = _db(path)
    applied = []

    def migration(db_, cursor):
        applied.append("ran")
        db_._execute(cursor, "CREATE TABLE IF NOT EXISTS test_migration_marker (id INTEGER)")

    monkeypatch.setattr(
        mig, "MIGRATIONS", [mig.Migration(version=7, description="test", fn=migration)]
    )
    db2 = _db(path)
    assert applied == ["ran"]
    assert 7 in _versions(db2)

    # 再次启动不应重复执行
    db3 = _db(path)
    assert applied == ["ran"]
    assert _versions(db3).count(7) == 1
