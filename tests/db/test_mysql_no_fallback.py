"""业务库测试：MySQL 连不上时单机降级到 SQLite，生产多副本仍然拒绝启动。"""

import pytest

from easy_agent.db.database import Database


def test_mysql_failure_falls_back_to_sqlite_on_single_node(monkeypatch, tmp_path):
    def boom(self):
        raise RuntimeError("cannot connect")

    monkeypatch.setattr(Database, "_init_mysql_pool", boom)
    monkeypatch.setenv("AGENT_ENV", "dev")
    db = Database(
        {
            "type": "mysql",
            "mysql": {"host": "127.0.0.1"},
            "sqlite": {"path": str(tmp_path / "easy_agent.db")},
        }
    )
    assert db.db_type == "sqlite"
    assert db.db_path == str(tmp_path / "easy_agent.db")


def test_mysql_failure_refuses_sqlite_when_prod_has_multiple_replicas(monkeypatch):
    def boom(self):
        raise RuntimeError("cannot connect")

    monkeypatch.setattr(Database, "_init_mysql_pool", boom)
    monkeypatch.setenv("AGENT_ENV", "prod")
    monkeypatch.setenv("EASY_REPLICA_COUNT", "2")
    with pytest.raises(RuntimeError, match="禁止降级到 SQLite"):
        Database({"type": "mysql", "mysql": {"host": "127.0.0.1"}})
