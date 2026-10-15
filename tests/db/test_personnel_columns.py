from easy_agent.db.database import Database, init_database


def test_new_user_has_personnel_columns(db):
    db.register_user("alice", "secret123", employee_id="E001")
    u = db.get_user_by_username("alice")
    assert u.account_status == "active"
    assert u.display_name == "" and u.department_id == "" and u.department_name == ""
    assert u.position == "" and u.mobile == "" and u.personnel_source == ""


def _reinit(db):
    """对同一 SQLite 文件重跑迁移入口，返回新的 Database 句柄。"""
    return init_database({"type": "sqlite", "sqlite": {"path": str(db.db_path)}})


def test_migration_is_idempotent(db):
    db.register_user("alice", "secret123", employee_id="E001")
    _reinit(db)
    again = _reinit(db)
    u = again.get_user_by_username("alice")
    assert u is not None and u.account_status == "active"
    with again.get_connection() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    for col in (
        "display_name",
        "department_id",
        "department_name",
        "position",
        "mobile",
        "account_status",
        "personnel_source",
    ):
        assert cols.count(col) == 1, f"{col} 重复或缺失: {cols}"


def test_backfill_department_id_from_organization_id(db):
    db.register_user("alice", "secret123", employee_id="E001")
    with db.get_connection() as conn:
        conn.execute(
            "UPDATE users SET organization_id=?, department_id='' WHERE username=?",
            ("dept-xyz", "alice"),
        )
    migrated = _reinit(db)
    assert migrated.get_user_by_username("alice").department_id == "dept-xyz"


def test_row_to_user_falls_back_when_columns_missing():
    row = {
        "user_id": "u1",
        "username": "alice",
        "password_hash": "h",
        "organization_id": "dept-xyz",
        "created_at": "t0",
        "updated_at": "t1",
    }
    u = Database._row_to_user(row)
    assert u.account_status == "active"
    assert u.department_id == "dept-xyz"
    assert u.display_name == "" and u.personnel_source == ""
