#!/usr/bin/env python3
"""Safely copy an EasyAgent SQLite database into MySQL.

The source is always opened read-only.  The default mode is a dry run; writes
require both ``--apply`` and an exact ``--confirm-database`` value.  Existing
MySQL rows are never updated or deleted.
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import pymysql
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from easy_agent.db.database import Database  # noqa: E402


ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
SAFE_DATABASE_NAME = re.compile(r"^[A-Za-z0-9_$]+$")
DEFAULT_DEV_PASSWORD = "easyagent_dev_only"


class MigrationError(RuntimeError):
    """Raised when the migration cannot continue without risking data loss."""


@dataclass(frozen=True)
class MySQLTarget:
    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str = "utf8mb4"
    connect_timeout: int = 10
    read_timeout: int = 30
    write_timeout: int = 30

    def database_config(self) -> dict[str, Any]:
        return {
            "type": "mysql",
            "mysql": {
                "host": self.host,
                "port": self.port,
                "user": self.user,
                "password": self.password,
                "database": self.database,
                "charset": self.charset,
                "connect_timeout": self.connect_timeout,
                "read_timeout": self.read_timeout,
                "write_timeout": self.write_timeout,
                "pool": {
                    "pool_size": 2,
                    "max_overflow": 1,
                    "pool_recycle": 3600,
                },
            },
            # Database falls back to this path on a MySQL connection failure.
            # The script checks db_type before init_tables(), so it is never used.
            "sqlite": {"path": ":memory:"},
        }


@dataclass(frozen=True)
class ForeignKey:
    table: str
    columns: tuple[str, ...]
    referenced_table: str
    referenced_columns: tuple[str, ...]
    constraint_name: str


@dataclass
class TableStats:
    source: int = 0
    inserted: int = 0
    skipped: int = 0


def _quote_mysql(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


def _quote_sqlite(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _expand_env(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    unresolved: list[str] = []

    def replace(match: re.Match[str]) -> str:
        name, default = match.group(1), match.group(2)
        if name in os.environ:
            return os.environ[name]
        if default is not None:
            return default
        unresolved.append(name)
        return match.group(0)

    expanded = ENV_PATTERN.sub(replace, value)
    if unresolved:
        raise MigrationError(
            "配置中的环境变量未设置: " + ", ".join(sorted(set(unresolved)))
        )
    return expanded


def _read_mysql_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise MigrationError(f"配置文件不存在: {path}")
    with path.open(encoding="utf-8") as stream:
        content = yaml.safe_load(stream)
    if not isinstance(content, dict):
        raise MigrationError(f"配置文件格式无效: {path}")
    database = content.get("database")
    mysql = database.get("mysql") if isinstance(database, dict) else None
    if not isinstance(mysql, dict):
        raise MigrationError(f"配置文件缺少 database.mysql: {path}")
    return {str(key): _expand_env(value) for key, value in mysql.items()}


def _target_from_args(args: argparse.Namespace) -> MySQLTarget:
    values: dict[str, Any] = {
        "host": os.environ.get("EASYAGENT_MYSQL_HOST", "127.0.0.1"),
        "port": int(os.environ.get("EASYAGENT_MYSQL_PORT", "3307")),
        "user": os.environ.get("EASYAGENT_MYSQL_USER", "easyagent"),
        "password": os.environ.get(
            args.password_env, DEFAULT_DEV_PASSWORD
        ),
        "database": os.environ.get("EASYAGENT_MYSQL_DATABASE", "agent"),
        "charset": "utf8mb4",
        "connect_timeout": 10,
        "read_timeout": 30,
        "write_timeout": 30,
    }
    if args.config:
        values.update(_read_mysql_yaml(args.config))

    for name in ("host", "port", "user", "password", "database", "charset"):
        cli_value = getattr(args, name, None)
        if cli_value is not None:
            values[name] = cli_value

    try:
        values["port"] = int(values["port"])
        for name in ("connect_timeout", "read_timeout", "write_timeout"):
            values[name] = int(values.get(name, 30))
    except (TypeError, ValueError) as exc:
        raise MigrationError("MySQL 端口或超时参数必须是整数") from exc

    if not (1 <= values["port"] <= 65535):
        raise MigrationError("MySQL 端口必须介于 1 和 65535 之间")
    if not SAFE_DATABASE_NAME.fullmatch(str(values["database"])):
        raise MigrationError("MySQL 库名只能包含字母、数字、_或$")

    return MySQLTarget(
        host=str(values["host"]),
        port=values["port"],
        user=str(values["user"]),
        password=str(values.get("password", "")),
        database=str(values["database"]),
        charset=str(values.get("charset", "utf8mb4")),
        connect_timeout=values["connect_timeout"],
        read_timeout=values["read_timeout"],
        write_timeout=values["write_timeout"],
    )


def _open_sqlite_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise MigrationError(f"SQLite 文件不存在: {resolved}")
    connection = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    quick_check = connection.execute("PRAGMA quick_check").fetchone()
    if not quick_check or quick_check[0] != "ok":
        connection.close()
        raise MigrationError(f"SQLite quick_check 失败: {quick_check}")
    foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchmany(20)
    if foreign_key_errors:
        connection.close()
        raise MigrationError(
            "SQLite 存在外键异常，为防止将孤立数据写入 MySQL，已停止: "
            + repr([tuple(row) for row in foreign_key_errors])
        )
    connection.execute("BEGIN")
    return connection


def _source_tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def _source_columns(connection: sqlite3.Connection, table: str) -> list[sqlite3.Row]:
    return list(connection.execute(f"PRAGMA table_info({_quote_sqlite(table)})"))


def _source_count(connection: sqlite3.Connection, table: str) -> int:
    row = connection.execute(
        f"SELECT COUNT(*) FROM {_quote_sqlite(table)}"
    ).fetchone()
    return int(row[0])


def _open_mysql_server(target: MySQLTarget):
    return pymysql.connect(
        host=target.host,
        port=target.port,
        user=target.user,
        password=target.password,
        charset=target.charset,
        connect_timeout=target.connect_timeout,
        read_timeout=target.read_timeout,
        write_timeout=target.write_timeout,
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def _database_exists(connection, database: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.SCHEMATA WHERE SCHEMA_NAME=%s",
            (database,),
        )
        return cursor.fetchone() is not None


def _target_tables(connection, database: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT TABLE_NAME FROM information_schema.TABLES
            WHERE TABLE_SCHEMA=%s AND TABLE_TYPE='BASE TABLE'
            """,
            (database,),
        )
        return {str(row["TABLE_NAME"]) for row in cursor.fetchall()}


def _target_columns(connection, database: str, table: str) -> list[dict[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA,
                   ORDINAL_POSITION
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
            ORDER BY ORDINAL_POSITION
            """,
            (database, table),
        )
        return list(cursor.fetchall())


def _primary_columns(connection, database: str, table: str) -> tuple[str, ...]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COLUMN_NAME
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s
              AND CONSTRAINT_NAME='PRIMARY'
            ORDER BY ORDINAL_POSITION
            """,
            (database, table),
        )
        return tuple(str(row["COLUMN_NAME"]) for row in cursor.fetchall())


def _foreign_keys(connection, database: str) -> list[ForeignKey]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME,
                   REFERENCED_COLUMN_NAME, CONSTRAINT_NAME, ORDINAL_POSITION
            FROM information_schema.KEY_COLUMN_USAGE
            WHERE TABLE_SCHEMA=%s AND REFERENCED_TABLE_NAME IS NOT NULL
            ORDER BY TABLE_NAME, CONSTRAINT_NAME, ORDINAL_POSITION
            """,
            (database,),
        )
        rows = cursor.fetchall()

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = (
            str(row["TABLE_NAME"]),
            str(row["CONSTRAINT_NAME"]),
            str(row["REFERENCED_TABLE_NAME"]),
        )
        grouped[key].append(row)

    result: list[ForeignKey] = []
    for (table, constraint, referenced), parts in grouped.items():
        result.append(
            ForeignKey(
                table=table,
                columns=tuple(str(part["COLUMN_NAME"]) for part in parts),
                referenced_table=referenced,
                referenced_columns=tuple(
                    str(part["REFERENCED_COLUMN_NAME"]) for part in parts
                ),
                constraint_name=constraint,
            )
        )
    return result


def _table_order(tables: Sequence[str], foreign_keys: Sequence[ForeignKey]) -> list[str]:
    nodes = set(tables)
    children: dict[str, set[str]] = {table: set() for table in nodes}
    indegree: dict[str, int] = {table: 0 for table in nodes}
    for fk in foreign_keys:
        if (
            fk.table not in nodes
            or fk.referenced_table not in nodes
            or fk.table == fk.referenced_table
        ):
            continue
        if fk.table not in children[fk.referenced_table]:
            children[fk.referenced_table].add(fk.table)
            indegree[fk.table] += 1

    ready = sorted(table for table, degree in indegree.items() if degree == 0)
    ordered: list[str] = []
    while ready:
        table = ready.pop(0)
        ordered.append(table)
        for child in sorted(children[table]):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
                ready.sort()

    if len(ordered) != len(nodes):
        cycle = sorted(table for table, degree in indegree.items() if degree > 0)
        raise MigrationError(
            "MySQL 外键依赖存在循环，无法安全确定迁移顺序: " + ", ".join(cycle)
        )
    return ordered


def _sqlite_type_to_mysql(declared_type: str | None) -> str:
    normalized = (declared_type or "").strip().upper()
    varchar = re.fullmatch(r"(?:VAR)?CHAR\s*\(\s*(\d+)\s*\)", normalized)
    if varchar:
        length = max(1, min(int(varchar.group(1)), 16383))
        return f"VARCHAR({length})"
    if "INT" in normalized:
        return "BIGINT"
    if any(token in normalized for token in ("REAL", "FLOA", "DOUB")):
        return "DOUBLE"
    if "BLOB" in normalized or not normalized:
        return "LONGBLOB"
    if any(token in normalized for token in ("NUMERIC", "DECIMAL")):
        return "DECIMAL(65,30)"
    return "LONGTEXT"


def _ensure_source_columns(
    connection,
    source: sqlite3.Connection,
    database: str,
    table: str,
) -> list[str]:
    source_info = _source_columns(source, table)
    source_names = [str(row[1]) for row in source_info]
    target_names = {
        str(row["COLUMN_NAME"])
        for row in _target_columns(connection, database, table)
    }
    missing = [row for row in source_info if str(row[1]) not in target_names]
    if missing:
        with connection.cursor() as cursor:
            for row in missing:
                name = str(row[1])
                mysql_type = _sqlite_type_to_mysql(row[2])
                cursor.execute(
                    f"ALTER TABLE {_quote_mysql(table)} "
                    f"ADD COLUMN {_quote_mysql(name)} {mysql_type} NULL"
                )
                print(f"  + 补充历史列 {table}.{name} ({mysql_type})")
    return source_names


def _iter_source_rows(
    connection: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
    batch_size: int,
) -> Iterable[sqlite3.Row]:
    selected = ", ".join(_quote_sqlite(column) for column in columns)
    cursor = connection.execute(
        f"SELECT {selected} FROM {_quote_sqlite(table)}"
    )
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            return
        yield from rows


def _normalize_source_value(table: str, column: str, value: Any) -> Any:
    """Normalize legacy values that conflict with current MySQL constraints."""

    # employee_id is optional but has a UNIQUE index in the current schema.
    # SQLite historically stored an omitted employee number as ``''``; MySQL
    # permits multiple NULL values in a UNIQUE index, but only one empty string.
    if (
        table == "users"
        and column == "employee_id"
        and isinstance(value, str)
        and not value.strip()
    ):
        return None
    return value


def _primary_exists(
    cursor,
    table: str,
    primary_columns: Sequence[str],
    values: dict[str, Any],
) -> bool:
    predicates = " AND ".join(
        f"{_quote_mysql(column)} <=> %s" for column in primary_columns
    )
    cursor.execute(
        f"SELECT 1 FROM {_quote_mysql(table)} WHERE {predicates} LIMIT 1",
        tuple(values[column] for column in primary_columns),
    )
    return cursor.fetchone() is not None


def _copy_table(
    mysql_connection,
    source: sqlite3.Connection,
    database: str,
    table: str,
    source_columns: Sequence[str],
    batch_size: int,
    derived_columns: dict[str, str] | None = None,
) -> TableStats:
    stats = TableStats(source=_source_count(source, table))
    if not source_columns or stats.source == 0:
        return stats

    derived_columns = derived_columns or {}
    columns = [*source_columns, *derived_columns]

    primary = _primary_columns(mysql_connection, database, table)
    if not primary:
        raise MigrationError(f"目标表缺少主键，无法做幂等迁移: {table}")
    if any(column not in columns for column in primary):
        raise MigrationError(f"源表缺少目标主键列: {table} {primary}")

    quoted_columns = ", ".join(_quote_mysql(column) for column in columns)
    placeholders = ", ".join(["%s"] * len(columns))
    no_op_column = _quote_mysql(primary[0])
    insert_sql = (
        f"INSERT INTO {_quote_mysql(table)} ({quoted_columns}) "
        f"VALUES ({placeholders}) "
        f"ON DUPLICATE KEY UPDATE {no_op_column}={no_op_column}"
    )

    with mysql_connection.cursor() as cursor:
        for row in _iter_source_rows(source, table, source_columns, batch_size):
            values = {
                column: _normalize_source_value(table, column, row[column])
                for column in source_columns
            }
            for target_column, source_column in derived_columns.items():
                values[target_column] = row[source_column]
            cursor.execute(insert_sql, tuple(values[column] for column in columns))
            if cursor.rowcount == 1:
                stats.inserted += 1
                continue
            if _primary_exists(cursor, table, primary, values):
                stats.skipped += 1
                continue
            raise MigrationError(
                f"表 {table} 出现非主键唯一约束冲突；"
                "为避免将子表关联到错误记录，本次数据写入已回滚"
            )
    return stats


def _validate_foreign_keys(
    connection, foreign_keys: Sequence[ForeignKey]
) -> None:
    with connection.cursor() as cursor:
        for fk in foreign_keys:
            join = " AND ".join(
                f"child.{_quote_mysql(child)}=parent.{_quote_mysql(parent)}"
                for child, parent in zip(fk.columns, fk.referenced_columns)
            )
            present = " AND ".join(
                f"child.{_quote_mysql(column)} IS NOT NULL" for column in fk.columns
            )
            missing = f"parent.{_quote_mysql(fk.referenced_columns[0])} IS NULL"
            cursor.execute(
                f"SELECT COUNT(*) AS total FROM {_quote_mysql(fk.table)} child "
                f"LEFT JOIN {_quote_mysql(fk.referenced_table)} parent ON {join} "
                f"WHERE {present} AND {missing}"
            )
            total = int(cursor.fetchone()["total"])
            if total:
                raise MigrationError(
                    f"外键复核失败 {fk.constraint_name}: "
                    f"{fk.table} -> {fk.referenced_table}, 孤立行数={total}"
                )


def _sync_auto_increment(connection, database: str, tables: Sequence[str]) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT TABLE_NAME, COLUMN_NAME
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA=%s AND EXTRA LIKE '%%auto_increment%%'
            ORDER BY TABLE_NAME
            """,
            (database,),
        )
        auto_columns = cursor.fetchall()
        for row in auto_columns:
            table = str(row["TABLE_NAME"])
            if table not in tables:
                continue
            column = str(row["COLUMN_NAME"])
            cursor.execute(
                f"SELECT MAX({_quote_mysql(column)}) AS maximum "
                f"FROM {_quote_mysql(table)}"
            )
            maximum = cursor.fetchone()["maximum"]
            if maximum is None:
                continue
            next_value = int(maximum) + 1
            cursor.execute(
                f"ALTER TABLE {_quote_mysql(table)} AUTO_INCREMENT={next_value}"
            )


def _dry_run(
    source: sqlite3.Connection,
    target: MySQLTarget,
    source_tables: Sequence[str],
) -> None:
    connection = _open_mysql_server(target)
    try:
        database_exists = _database_exists(connection, target.database)
        print("模式: DRY-RUN（不写入）")
        print(f"目标: {target.user}@{target.host}:{target.port}/{target.database}")
        if not database_exists:
            print(
                "目标库尚未存在；--apply 时 Database 会尝试创建，"
                "目标用户需有建库权限（本地 Compose 会预先创建）。"
            )
            target_tables: set[str] = set()
        else:
            target_tables = _target_tables(connection, target.database)

        total = 0
        for table in source_tables:
            count = _source_count(source, table)
            total += count
            state = "目标表已存在" if table in target_tables else "将创建目标表"
            print(f"  - {table}: {count} 行，{state}")
        print(f"合计: {len(source_tables)} 张表，{total} 行源数据。")
        print("未进行任何 MySQL 建表、插入、更新或删除操作。")
    finally:
        connection.close()


def _apply(
    source: sqlite3.Connection,
    target: MySQLTarget,
    source_tables: Sequence[str],
    batch_size: int,
) -> None:
    database = Database(target.database_config())
    if database.db_type != "mysql":
        database.close()
        raise MigrationError(
            "MySQL 连接失败，EasyAgent Database 尝试降级到 SQLite；"
            "迁移脚本已在建表前中止"
        )

    connection = None
    try:
        connection = database._get_mysql_connection()
        target_tables = _target_tables(connection, target.database)
        if target_tables:
            # init_tables() also contains application-level data repair.  It
            # must never run against a non-empty target because that could
            # update users or rebuild session_messages before the migration
            # transaction starts.
            missing_tables = sorted(set(source_tables) - target_tables)
            if missing_tables:
                raise MigrationError(
                    "目标库已存在业务表，不会调用 Database.init_tables() 补表；"
                    "下列源表在目标库中缺失: "
                    + ", ".join(missing_tables)
                )
            print("目标库已有业务表：跳过 Database.init_tables()")
        else:
            # Release the probe connection before schema initialization.  A
            # completely empty target is the only state in which calling the
            # application's initializer is safe.
            connection.close()
            connection = None
            print("目标库无业务表：通过 EasyAgent Database.init_tables() 初始化表结构…")
            database.init_tables()
            connection = database._get_mysql_connection()
            target_tables = _target_tables(connection, target.database)
            missing_tables = sorted(set(source_tables) - target_tables)
            if missing_tables:
                raise MigrationError(
                    "Database.init_tables() 未创建下列源表对应的目标表，"
                    "为避免数据丢失已停止: "
                    + ", ".join(missing_tables)
                )

        # Schema reconciliation is completed before data DML.  MySQL DDL commits
        # implicitly, but it only adds source columns and never alters existing data.
        columns_by_table: dict[str, list[str]] = {}
        derived_by_table: dict[str, dict[str, str]] = {}
        for table in source_tables:
            columns_by_table[table] = _ensure_source_columns(
                connection, source, target.database, table
            )
            target_column_names = {
                str(row["COLUMN_NAME"])
                for row in _target_columns(connection, target.database, table)
            }
            # Older SQLite databases predate the explicit department_id field.
            # Populate it on INSERT from the compatible organization_id value;
            # this never updates a row that already exists in MySQL.
            if (
                table == "users"
                and "organization_id" in columns_by_table[table]
                and "department_id" not in columns_by_table[table]
                and "department_id" in target_column_names
            ):
                derived_by_table[table] = {
                    "department_id": "organization_id"
                }
                print(
                    "  + users.department_id 将在新增行中沿用 "
                    "organization_id"
                )

        foreign_keys = _foreign_keys(connection, target.database)
        ordered_tables = _table_order(source_tables, foreign_keys)
        print("数据迁移顺序: " + " -> ".join(ordered_tables))

        stats: dict[str, TableStats] = {}
        try:
            for table in ordered_tables:
                table_stats = _copy_table(
                    connection,
                    source,
                    target.database,
                    table,
                    columns_by_table[table],
                    batch_size,
                    derived_by_table.get(table),
                )
                stats[table] = table_stats
                print(
                    f"  ✓ {table}: 源 {table_stats.source}，"
                    f"新增 {table_stats.inserted}，跳过 {table_stats.skipped}"
                )
            _validate_foreign_keys(connection, foreign_keys)
            connection.commit()
        except Exception:
            connection.rollback()
            raise

        # Explicit IDs are preserved.  Align AUTO_INCREMENT after the data
        # transaction so subsequent EasyAgent inserts always allocate a new ID.
        _sync_auto_increment(connection, target.database, ordered_tables)
        connection.commit()

        inserted = sum(item.inserted for item in stats.values())
        skipped = sum(item.skipped for item in stats.values())
        total = sum(item.source for item in stats.values())
        print(
            f"迁移完成: {len(stats)} 张表，源 {total} 行，"
            f"新增 {inserted} 行，幂等跳过 {skipped} 行。"
        )
        print("SQLite 源文件保持不变。")
    finally:
        if connection is not None:
            connection.close()
        database.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将 EasyAgent SQLite 数据幂等、非覆盖地迁移到 MySQL"
    )
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=Path("./data/easy_agent.db"),
        help="SQLite 源文件（只读）",
    )
    parser.add_argument(
        "--config",
        type=Path,
        help="可选：从 YAML 的 database.mysql 读取 MySQL 参数",
    )
    parser.add_argument("--host", help="MySQL 主机")
    parser.add_argument("--port", type=int, help="MySQL 端口")
    parser.add_argument("--user", help="MySQL 用户")
    parser.add_argument("--password", help="MySQL 密码（优先使用环境变量）")
    parser.add_argument(
        "--password-env",
        default="EASYAGENT_MYSQL_PASSWORD",
        help="存放 MySQL 密码的环境变量名",
    )
    parser.add_argument("--database", help="MySQL 库名")
    parser.add_argument("--charset", help="MySQL 字符集")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="SQLite 每次读取的行数（默认 500）",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="只显示迁移计划（默认）",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
        help="实际建表并补录数据",
    )
    parser.add_argument(
        "--confirm-database",
        help="执行 --apply 时必须再次准确填写目标库名",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.batch_size < 1 or args.batch_size > 10000:
            raise MigrationError("--batch-size 必须介于 1 和 10000 之间")
        target = _target_from_args(args)
        if args.apply and args.confirm_database != target.database:
            raise MigrationError(
                "--apply 需要 --confirm-database，且其值必须与目标库名完全一致"
            )

        source_path = args.sqlite.expanduser().resolve()
        source = _open_sqlite_read_only(source_path)
        try:
            tables = _source_tables(source)
            if not tables:
                raise MigrationError(f"SQLite 中没有可迁移的业务表: {source_path}")
            print(f"源: {source_path}")
            if args.apply:
                print(
                    f"模式: APPLY | 目标: {target.user}@{target.host}:"
                    f"{target.port}/{target.database}"
                )
                _apply(source, target, tables, args.batch_size)
            else:
                _dry_run(source, target, tables)
        finally:
            source.rollback()
            source.close()
        return 0
    except (MigrationError, pymysql.MySQLError, sqlite3.Error, OSError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
