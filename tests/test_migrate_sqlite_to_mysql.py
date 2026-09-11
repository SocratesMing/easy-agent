import sqlite3

import pytest

from scripts import migrate_sqlite_to_mysql as migration


class _ApplyConnection:
    def __init__(self):
        self.closed = False
        self.commits = 0
        self.rollbacks = 0

    def close(self):
        self.closed = True

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class _FakeDatabase:
    def __init__(self, connection):
        self.db_type = "mysql"
        self.connection = connection
        self.init_calls = 0
        self.closed = False

    def init_tables(self):
        self.init_calls += 1

    def _get_mysql_connection(self):
        return self.connection

    def close(self):
        self.closed = True


def _target():
    return migration.MySQLTarget(
        host="127.0.0.1",
        port=3307,
        user="easyagent",
        password="test-only",
        database="agent",
    )


def test_apply_skips_initializer_for_nonempty_target(monkeypatch):
    connection = _ApplyConnection()
    database = _FakeDatabase(connection)
    monkeypatch.setattr(migration, "Database", lambda _config: database)
    monkeypatch.setattr(migration, "_target_tables", lambda *_args: {"users"})
    monkeypatch.setattr(
        migration, "_ensure_source_columns", lambda *_args: ["user_id"]
    )
    monkeypatch.setattr(
        migration,
        "_target_columns",
        lambda *_args: [{"COLUMN_NAME": "user_id"}],
    )
    monkeypatch.setattr(migration, "_foreign_keys", lambda *_args: [])
    monkeypatch.setattr(
        migration, "_table_order", lambda tables, _foreign_keys: list(tables)
    )
    monkeypatch.setattr(
        migration,
        "_copy_table",
        lambda *_args, **_kwargs: migration.TableStats(source=1, skipped=1),
    )
    monkeypatch.setattr(migration, "_validate_foreign_keys", lambda *_args: None)
    monkeypatch.setattr(migration, "_sync_auto_increment", lambda *_args: None)

    migration._apply(object(), _target(), ["users"], batch_size=100)

    assert database.init_calls == 0
    assert connection.commits == 2
    assert database.closed is True


def test_apply_rejects_incomplete_nonempty_target_without_initializing(monkeypatch):
    connection = _ApplyConnection()
    database = _FakeDatabase(connection)
    monkeypatch.setattr(migration, "Database", lambda _config: database)
    monkeypatch.setattr(migration, "_target_tables", lambda *_args: {"users"})

    with pytest.raises(migration.MigrationError, match="sessions"):
        migration._apply(
            object(),
            _target(),
            ["users", "sessions"],
            batch_size=100,
        )

    assert database.init_calls == 0
    assert connection.commits == 0
    assert database.closed is True


class _DuplicateCursor:
    def __init__(self, *, primary_exists, inserted=False):
        self.primary_exists = primary_exists
        self.inserted = inserted
        self.rowcount = 0
        self._result = None
        self.inserted_params = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        if sql.startswith("INSERT INTO"):
            self.inserted_params.append(params)
            self.rowcount = 1 if self.inserted else 0
            self._result = None
        elif sql.startswith("SELECT 1 FROM"):
            self._result = {"exists": 1} if self.primary_exists else None

    def fetchone(self):
        return self._result


class _CopyConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def _source_users(rows, *, include_employee_id=False):
    source = sqlite3.connect(":memory:")
    source.row_factory = sqlite3.Row
    if include_employee_id:
        source.execute(
            "CREATE TABLE users (user_id TEXT PRIMARY KEY, employee_id TEXT)"
        )
        source.executemany(
            "INSERT INTO users (user_id, employee_id) VALUES (?, ?)", rows
        )
    else:
        source.execute("CREATE TABLE users (user_id TEXT PRIMARY KEY, username TEXT)")
        source.executemany(
            "INSERT INTO users (user_id, username) VALUES (?, ?)", rows
        )
    return source


def test_copy_table_treats_existing_primary_key_as_idempotent_skip(monkeypatch):
    source = _source_users([("user-1", "alice")])
    cursor = _DuplicateCursor(primary_exists=True)
    monkeypatch.setattr(
        migration, "_primary_columns", lambda *_args: ("user_id",)
    )

    stats = migration._copy_table(
        _CopyConnection(cursor),
        source,
        "agent",
        "users",
        ["user_id", "username"],
        batch_size=100,
    )

    assert stats.source == 1
    assert stats.inserted == 0
    assert stats.skipped == 1
    source.close()


def test_copy_table_rejects_non_primary_unique_conflict(monkeypatch):
    source = _source_users([("user-2", "duplicate-username")])
    cursor = _DuplicateCursor(primary_exists=False)
    monkeypatch.setattr(
        migration, "_primary_columns", lambda *_args: ("user_id",)
    )

    with pytest.raises(migration.MigrationError, match="非主键唯一约束冲突"):
        migration._copy_table(
            _CopyConnection(cursor),
            source,
            "agent",
            "users",
            ["user_id", "username"],
            batch_size=100,
        )
    source.close()


def test_copy_table_normalizes_blank_employee_ids_to_null(monkeypatch):
    source = _source_users(
        [("user-1", ""), ("user-2", "   "), ("user-3", "E-003")],
        include_employee_id=True,
    )
    cursor = _DuplicateCursor(primary_exists=False, inserted=True)
    monkeypatch.setattr(
        migration, "_primary_columns", lambda *_args: ("user_id",)
    )

    stats = migration._copy_table(
        _CopyConnection(cursor),
        source,
        "agent",
        "users",
        ["user_id", "employee_id"],
        batch_size=100,
    )

    assert stats.inserted == 3
    assert cursor.inserted_params == [
        ("user-1", None),
        ("user-2", None),
        ("user-3", "E-003"),
    ]
    source.close()
