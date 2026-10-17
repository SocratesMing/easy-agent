import pytest

from easy_agent.db.database import Database


def test_mysql_failure_does_not_fall_back_to_sqlite(monkeypatch):
    def boom(self):
        raise RuntimeError("cannot connect")

    monkeypatch.setattr(Database, "_init_mysql_pool", boom)
    with pytest.raises(RuntimeError, match="禁止降级到 SQLite"):
        Database({"type": "mysql", "mysql": {"host": "127.0.0.1"}})
