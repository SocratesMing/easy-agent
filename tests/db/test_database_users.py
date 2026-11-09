"""Database 用户方法测试：MySQL 字典游标计数、dev/test 自动生成强初始密码、
prod 仍必须显式注入强初始密码、生产环境轮换旧的已知管理员密码。
"""

from contextlib import contextmanager

import pytest

from easy_agent.bootstrap.env import PLACEHOLDER_PREFIX
from easy_agent.db.database import Database
from easy_agent.db.repositories.user_repository import UserRepository
from easy_agent.knowledges.personnel import bootstrap as personnel_bootstrap


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

    assert UserRepository(db).count_users() == 3


def _fresh_database(tmp_path: str, name: str) -> Database:
    database = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / name)}})
    database.init_tables()
    return database


@pytest.mark.parametrize("agent_env", ["dev", "test"])
@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        PLACEHOLDER_PREFIX + "EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD",
        "123456",
        "admin",
    ],
)
def test_dev_test_generates_a_persisted_strong_bootstrap_password(
    tmp_path, monkeypatch, agent_env, raw
):
    """dev/test 空值、哨兵或弱口令时不再阻断启动，而是生成 0600 持久化的强随机密码。"""
    monkeypatch.setenv("AGENT_ENV", agent_env)
    monkeypatch.setattr(
        personnel_bootstrap, "_ADMIN_PASSWORD_FILE", tmp_path / ".admin_bootstrap_password"
    )
    if raw is None:
        monkeypatch.delenv("EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD", raw)

    database = _fresh_database(tmp_path, f"{agent_env}-{raw is None}-admin.db")
    created = UserRepository(database).get_or_create_default_user()

    password_file = tmp_path / ".admin_bootstrap_password"
    assert password_file.exists()
    generated = password_file.read_text(encoding="utf-8").strip()
    assert len(generated) >= 12
    assert len(set(generated)) >= 4
    assert generated != raw
    assert created.username == "admin"
    assert database.verify_user_password("admin", generated) is not None
    assert database.verify_user_password("admin", "admin") is None


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        PLACEHOLDER_PREFIX + "EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD",
        "123456",
    ],
)
def test_prod_still_requires_an_explicit_strong_bootstrap_secret(
    tmp_path, monkeypatch, raw
):
    """prod 的空值、哨兵、弱口令必须继续 fail-fast，不能回退到自动生成的默认值。"""
    monkeypatch.setenv("AGENT_ENV", "prod")
    monkeypatch.setattr(
        personnel_bootstrap, "_ADMIN_PASSWORD_FILE", tmp_path / ".admin_bootstrap_password"
    )
    if raw is None:
        monkeypatch.delenv("EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD", raw)

    database = _fresh_database(tmp_path, f"prod-{raw is None}-admin.db")

    with pytest.raises(RuntimeError, match="EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD"):
        UserRepository(database).get_or_create_default_user()
    assert not (tmp_path / ".admin_bootstrap_password").exists()


def test_default_admin_uses_the_injected_bootstrap_secret(tmp_path, monkeypatch):
    secret = "One-Time-Admin-Secret-2026!"
    monkeypatch.setenv("EASYAGENT_BOOTSTRAP_ADMIN_PASSWORD", secret)
    database = Database(
        {"type": "sqlite", "sqlite": {"path": str(tmp_path / "admin.db")}}
    )
    database.init_tables()

    created = UserRepository(database).get_or_create_default_user()

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

    UserRepository(database).get_or_create_default_user()

    assert database.verify_user_password("admin", rotated_secret) is not None
    assert database.verify_user_password("admin", "admin") is None
