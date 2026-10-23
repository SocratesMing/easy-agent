"""Database 用户方法测试：MySQL 字典游标计数、默认管理员必须注入强初始密码、
生产环境轮换旧的已知管理员密码。
"""

from contextlib import contextmanager

import pytest

from easy_agent.db.database import Database


def test_count_users_supports_mysql_dict_cursor(db, monkeypatch):
    class Cursor:
        def execute(self, query, params=None):
            self.query = query

        def fetchone(self):
            return {"total": 3}

    class Connection:
        def cursor(self):
            return Cursor()

    @contextmanager
    def get_connection():
        yield Connection()

    monkeypatch.setattr(db, "get_connection", get_connection)

    assert db.count_users() == 3


def test_default_admin_requires_an_explicit_strong_bootstrap_secret(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    database = Database(
        {"type": "sqlite", "sqlite": {"path": str(tmp_path / "no-admin.db")}}
    )
    database.init_tables()

    with pytest.raises(RuntimeError, match="EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD"):
        database.get_or_create_default_user()


def test_default_admin_uses_the_injected_bootstrap_secret(tmp_path, monkeypatch):
    secret = "One-Time-Admin-Secret-2026!"
    monkeypatch.setenv("EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD", secret)
    database = Database(
        {"type": "sqlite", "sqlite": {"path": str(tmp_path / "admin.db")}}
    )
    database.init_tables()

    created = database.get_or_create_default_user()

    assert created.username == "admin"
    assert database.verify_user_password("admin", secret) is not None
    assert database.verify_user_password("admin", "admin") is None


def test_production_rotates_a_legacy_known_admin_password(tmp_path, monkeypatch):
    database = Database(
        {"type": "sqlite", "sqlite": {"path": str(tmp_path / "legacy-admin.db")}}
    )
    database.init_tables()
    database.register_user("admin", "admin")
    rotated_secret = "Rotated-Production-Admin-2026!"
    monkeypatch.setenv("AGENT_ENV", "prod")
    monkeypatch.setenv("EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD", rotated_secret)

    database.get_or_create_default_user()

    assert database.verify_user_password("admin", rotated_secret) is not None
    assert database.verify_user_password("admin", "admin") is None
