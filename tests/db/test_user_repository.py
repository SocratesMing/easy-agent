"""C1 剩余域：``UserRepository`` 委托与权限语义。"""

from easy_agent.db.database import Database
from easy_agent.db.repositories.user_repository import UserRepository


def _make_db(tmp_path) -> Database:
    db = Database(
        {"type": "sqlite", "sqlite": {"path": str(tmp_path / "users.db")}}
    )
    db.init_tables()
    return db


def test_repository_delegates_registration_lookup_and_password_reset(tmp_path):
    db = _make_db(tmp_path)
    repo = UserRepository(db)

    assert repo.database is db
    assert repo.get_user_by_username("alice") is None

    created = repo.register_user("alice", "secret123", employee_id="E001")
    assert created is not None
    assert repo.get_user_by_username("alice").user_id == created.user_id
    assert repo.get_user_by_employee_id("E001").username == "alice"
    assert repo.get_user_by_account("E001").username == "alice"
    assert repo.verify_user_password("alice", "secret123") is not None
    assert repo.verify_user_password("alice", "wrong") is None

    assert repo.increment_user_token_version("alice") == 1
    assert repo.get_user_token_version("alice") == 1
    assert repo.increment_user_token_version("missing") == 0

    assert repo.update_user_password("alice", "new-hash") is True
    assert repo.update_user_password("missing", "new-hash") is False

    assert repo.count_users() == 1
    assert [u.username for u in repo.list_users(limit=10, offset=0)] == ["alice"]


def test_repository_passwordless_login_respects_existing_user_id(tmp_path):
    db = _make_db(tmp_path)
    repo = UserRepository(db)

    user, created = repo.get_or_create_passwordless_user("portal", user_id="u-1")
    assert created is True
    assert user.user_id == "u-1"

    existing, created_again = repo.get_or_create_passwordless_user(
        "portal", user_id="ignored"
    )
    assert created_again is False
    assert existing.user_id == "u-1"

    conflict, conflict_created = repo.get_or_create_passwordless_user(
        "other", user_id="u-1"
    )
    assert conflict is None
    assert conflict_created is False
    assert repo.get_user_by_username("other") is None
