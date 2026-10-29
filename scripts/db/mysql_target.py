"""MySQL target/config parsing for migrate_sqlite_to_mysql.

Kept separate from the migration orchestration so target/env/YAML parsing can be
reviewed and tested without the data-copy code.
"""

from __future__ import annotations

import argparse
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


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


