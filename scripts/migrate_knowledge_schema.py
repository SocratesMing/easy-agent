#!/usr/bin/env python3
"""Apply or verify EasyAgent's numbered knowledge-schema migrations."""

from __future__ import annotations

import argparse
import os
import re
from pathlib import Path

import yaml

from easy_agent.config import DatabaseConfig
from easy_agent.db.database import Database
from easy_agent.knowledge.schema import (
    initialize_knowledge_schema,
    validate_knowledge_schema,
)


_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand(value):
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item) for item in value]
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

    result = _ENV.sub(replace, value)
    if unresolved:
        raise RuntimeError(
            "database configuration environment variable is missing: "
            + ", ".join(sorted(set(unresolved)))
        )
    return result


def _load_database_config(path: Path) -> DatabaseConfig:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("database"), dict):
        raise RuntimeError("database configuration is missing")
    return DatabaseConfig.model_validate(_expand(data["database"]))


def _target(config: DatabaseConfig) -> str:
    if config.type == "mysql":
        return config.mysql.database
    return str(Path(config.sqlite.path).expanduser().resolve())


def _require_core_schema(db: Database) -> None:
    """Avoid partially creating knowledge tables against an unbootstrapped app DB."""

    with db.get_connection() as connection:
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT session_id FROM sessions WHERE 1=0")
        except Exception as exc:
            raise RuntimeError(
                "EasyAgent core schema is missing; bootstrap an empty application "
                "database before applying knowledge migrations"
            ) from exc


def _database_is_empty(db: Database) -> bool:
    with db.get_connection() as connection:
        cursor = connection.cursor()
        if db.db_type == "mysql":
            cursor.execute("SHOW TABLES")
        else:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        return cursor.fetchone() is None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--bootstrap-empty-database",
        action="store_true",
        help="Create the core schema only for a new empty application database",
    )
    parser.add_argument(
        "--confirm-target",
        default="",
        help="Required with --apply and must exactly equal the configured database target",
    )
    args = parser.parse_args()

    config = _load_database_config(args.config)
    target = _target(config)
    if args.apply and args.confirm_target != target:
        raise SystemExit(f"--confirm-target must exactly equal: {target}")

    db = Database(config.model_dump())
    try:
        if args.apply:
            try:
                _require_core_schema(db)
            except RuntimeError:
                if not args.bootstrap_empty_database:
                    raise
                if not _database_is_empty(db):
                    raise RuntimeError(
                        "--bootstrap-empty-database refused because the target contains tables"
                    )
                previous_environment = os.environ.get("AGENT_ENV")
                os.environ["AGENT_ENV"] = "migration"
                try:
                    db.init_tables()
                finally:
                    if previous_environment is None:
                        os.environ.pop("AGENT_ENV", None)
                    else:
                        os.environ["AGENT_ENV"] = previous_environment
            with db.get_connection() as connection:
                initialize_knowledge_schema(db, connection.cursor())
        migrations = validate_knowledge_schema(db)
    finally:
        db.close()

    mode = "applied" if args.apply else "verified"
    versions = ", ".join(str(item["version"]) for item in migrations)
    print(f"knowledge schema {mode}: target={target} versions={versions}")


if __name__ == "__main__":
    main()
