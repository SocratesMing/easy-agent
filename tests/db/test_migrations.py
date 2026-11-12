"""版本化迁移框架测试（ARCH-16）。"""

import easy_agent.knowledges.core.schema  # noqa: F401 - 注册知识库建表
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
    # 引入迁移框架时已有 schema 版本记为 baseline（5）；之后的迁移在新库上也各执行一次
    assert _versions(db) == [mig.BASELINE_VERSION, *sorted(m.version for m in mig.MIGRATIONS)]


def test_migration_versions_follow_the_baseline():
    versions = [m.version for m in mig.MIGRATIONS]
    assert versions == sorted(set(versions))
    assert all(v > mig.BASELINE_VERSION for v in versions)


def test_pending_migration_applied_once(tmp_path, monkeypatch):
    path = tmp_path / "m2.db"
    _db(path)
    applied = []
    # 取一个比现有迁移都新的版本号，避免和真实迁移撞版本
    pending_version = max(m.version for m in mig.MIGRATIONS) + 1

    def migration(db_, cursor):
        applied.append("ran")
        db_._execute(cursor, "CREATE TABLE IF NOT EXISTS test_migration_marker (id INTEGER)")

    monkeypatch.setattr(
        mig,
        "MIGRATIONS",
        [mig.Migration(version=pending_version, description="test", fn=migration)],
    )
    db2 = _db(path)
    assert applied == ["ran"]
    assert pending_version in _versions(db2)

    # 再次启动不应重复执行
    db3 = _db(path)
    assert applied == ["ran"]
    assert _versions(db3).count(pending_version) == 1


def test_date_folder_names_are_zero_padded(tmp_path):
    """研报日期目录补零：只处理「年 → 月 → 日」链路，已补零 / 链路外的目录不动。"""
    db = _db(tmp_path / "folders.db")
    with db.get_connection() as conn:
        cursor = conn.cursor()
        rows = [
            ("year", None, "2026年"),
            ("m2", "year", "2月"),
            ("m10", "year", "10月"),
            ("d2", "m2", "2日"),
            ("d9", "m10", "9日"),
            ("d11", "m10", "11日"),
            ("loose", None, "2日"),  # 不在年/月链路上，不应改名
        ]
        for folder_id, parent_id, name in rows:
            db._execute(
                cursor,
                "INSERT INTO knowledge_folders "
                "(id, base_id, parent_id, name, sort_order, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (folder_id, "base", parent_id, name, 0, "2026-01-01", "2026-01-01"),
            )
        conn.commit()
        mig.normalize_date_folder_names(db, cursor)
        conn.commit()
        db._execute(cursor, "SELECT id, name FROM knowledge_folders")
        names = {row[0]: row[1] for row in cursor.fetchall()}

    assert names == {
        "year": "2026年",
        "m2": "02月",
        "m10": "10月",
        "d2": "02日",
        "d9": "09日",
        "d11": "11日",
        "loose": "2日",
    }
