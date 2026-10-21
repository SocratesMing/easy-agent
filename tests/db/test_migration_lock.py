"""迁移锁与启动只校验模式测试（多副本 Phase 3）。"""

from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from easy_agent.db.database import Database, init_database
from easy_agent.db.migration_lock import schema_migration_lock


def _db(tmp_path: Path) -> Database:
    return Database(
        {"type": "sqlite", "sqlite": {"path": str(tmp_path / "migrate.db")}}
    )


def _hold_lock(db_path: str, ready, release):
    db = Database({"type": "sqlite", "sqlite": {"path": db_path}})
    with schema_migration_lock(db, timeout=10):
        ready.set()
        release.wait(10)
    db.close()


def test_sqlite_lock_creates_sidecar_and_serializes_processes(tmp_path):
    db = _db(tmp_path)
    with schema_migration_lock(db, timeout=10):
        lock_path = Path(str(db.db_path) + ".migrate.lock")
        assert lock_path.exists()

    ready = multiprocessing.Event()
    release = multiprocessing.Event()
    proc = multiprocessing.Process(
        target=_hold_lock, args=(db.db_path, ready, release)
    )
    proc.start()
    try:
        assert ready.wait(10), "子进程未获得迁移锁"
        with pytest.raises(RuntimeError, match="迁移锁超时"):
            with schema_migration_lock(db, timeout=1):
                pass
    finally:
        release.set()
        proc.join(10)
        if proc.is_alive():
            proc.terminate()
    db.close()


def test_verify_schema_current_passes_after_init(tmp_path):
    db = _db(tmp_path)
    db.init_tables()
    db.verify_schema_current()  # 不抛
    db.close()


def test_verify_schema_current_rejects_old_version(tmp_path):
    db = _db(tmp_path)
    db.init_tables()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE schema_meta SET meta_value=? WHERE meta_key=?",
            ("0", "schema_version"),
        )
        conn.commit()
    with pytest.raises(RuntimeError, match="migrate_db.py"):
        db.verify_schema_current()
    db.close()


def test_init_database_auto_migrate_off_rejects_old_schema(tmp_path, monkeypatch):
    db_path = str(tmp_path / "gate.db")
    db = Database({"type": "sqlite", "sqlite": {"path": db_path}})
    db.init_tables()
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE schema_meta SET meta_value=? WHERE meta_key=?",
            ("0", "schema_version"),
        )
        conn.commit()
    db.close()

    monkeypatch.setenv("EASY_AUTO_MIGRATE", "0")
    with pytest.raises(RuntimeError, match="migrate_db.py"):
        init_database({"type": "sqlite", "sqlite": {"path": db_path}})


def test_init_database_auto_migrate_default_still_works(tmp_path, monkeypatch):
    monkeypatch.delenv("EASY_AUTO_MIGRATE", raising=False)
    db_path = str(tmp_path / "auto.db")
    db = init_database({"type": "sqlite", "sqlite": {"path": db_path}})
    db.verify_schema_current()
    db.close()
