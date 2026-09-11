#!/usr/bin/env python3
"""Create, verify and safely restore a knowledge metadata + originals backup."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import pymysql
import yaml

from easy_agent.config import Config, DatabaseConfig
from easy_agent.knowledge.config import OriginalStorageConfig


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
_INT = re.compile(r"^[+-]?(?:0|[1-9][0-9]*)$")
_FLOAT = re.compile(
    r"^[+-]?(?:(?:[0-9]+\.[0-9]*)|(?:[0-9]*\.[0-9]+)|(?:[0-9]+[eE][+-]?[0-9]+))$"
)


def _parse_env_scalar(value: str):
    """Match the application's environment-scalar semantics for strict models."""

    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none", "~"}:
        return None
    if _INT.fullmatch(value):
        return int(value)
    if _FLOAT.fullmatch(value):
        return float(value)
    return value


def _expand_relevant(value):
    """Expand only the DB/NAS subtrees required by an offline backup.

    Backup operators must not need live LLM or RAGFlow credentials. Parsing the
    complete application config made a valid metadata backup fail when either
    unrelated secret was intentionally absent from the backup environment.
    """

    if isinstance(value, dict):
        return {key: _expand_relevant(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_relevant(item) for item in value]
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

    full_match = _ENV.fullmatch(value)
    result = _ENV.sub(replace, value)
    if unresolved:
        raise RuntimeError(
            "backup configuration variable is missing: "
            + ", ".join(sorted(set(unresolved)))
        )
    if full_match and not _ENV.search(result):
        return _parse_env_scalar(result)
    return result


def _runtime(config_path: Path) -> tuple[DatabaseConfig, OriginalStorageConfig]:
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("database"), dict):
        raise RuntimeError("database configuration is missing")
    knowledge = raw.get("knowledge")
    if not isinstance(knowledge, dict) or not isinstance(
        knowledge.get("original_storage"), dict
    ):
        raise RuntimeError("knowledge original-storage configuration is missing")
    return (
        DatabaseConfig.model_validate(_expand_relevant(raw["database"])),
        OriginalStorageConfig.model_validate(
            _expand_relevant(knowledge["original_storage"])
        ),
    )


def _mysql_env(password: str) -> dict[str, str]:
    env = os.environ.copy()
    env["MYSQL_PWD"] = password
    return env


def _run_database_client(command: list[str], *, password: str, stdin=None) -> None:
    """Run a database client and surface a concise, credential-safe error."""

    completed = subprocess.run(
        command,
        env=_mysql_env(password),
        stdin=stdin,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if completed.returncode == 0:
        return
    detail = completed.stderr.decode("utf-8", errors="replace").strip()
    if password:
        detail = detail.replace(password, "<redacted>")
    if len(detail) > 2000:
        detail = detail[-2000:]
    raise RuntimeError(
        f"database client failed with exit code {completed.returncode}: "
        f"{detail or 'no diagnostic output'}"
    )


def create_backup(config_path: Path, output: Path, release_tag: str) -> Path:
    database, original_storage = _runtime(config_path)
    output.mkdir(parents=True, exist_ok=False)
    db_dump = output / ("database.sql" if database.type == "mysql" else "database.sqlite")
    if database.type == "mysql":
        mysql = database.mysql
        executable = shutil.which("mysqldump")
        if not executable:
            raise RuntimeError("mysqldump is required for a MySQL backup")
        _run_database_client(
            [
                # EasyAgent owns no stored routines. Skipping them also keeps a
                # MySQL 9 client compatible with an 8.4 server (9.x otherwise
                # probes INFORMATION_SCHEMA.LIBRARIES, absent in 8.4).
                executable, "--single-transaction", "--skip-routines", "--triggers",
                "--no-tablespaces",
                "--set-gtid-purged=OFF", "--host", mysql.host, "--port", str(mysql.port),
                "--user", mysql.user, f"--result-file={db_dump}", mysql.database,
            ],
            password=mysql.password,
        )
    else:
        source = sqlite3.connect(database.sqlite.path)
        target = sqlite3.connect(db_dump)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()

    originals = output / "originals.tar.gz"
    root = Path(original_storage.filesystem.root_path).expanduser().resolve()
    prefix = original_storage.filesystem.path_prefix
    source_root = root / prefix
    with tarfile.open(originals, "w:gz") as archive:
        if source_root.exists():
            archive.add(source_root, arcname=prefix, recursive=True)
    manifest = {
        "format": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "release_tag": release_tag,
        "database_type": database.type,
        "files": {
            db_dump.name: {"sha256": sha256(db_dump), "size_bytes": db_dump.stat().st_size},
            originals.name: {"sha256": sha256(originals), "size_bytes": originals.stat().st_size},
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return output


def verify_backup(directory: Path) -> dict:
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != 1:
        raise RuntimeError("unsupported backup format")
    for name, expected in manifest["files"].items():
        path = directory / name
        if not path.is_file() or path.stat().st_size != expected["size_bytes"]:
            raise RuntimeError(f"backup file is missing or truncated: {name}")
        if sha256(path) != expected["sha256"]:
            raise RuntimeError(f"backup checksum mismatch: {name}")
    with tarfile.open(directory / "originals.tar.gz", "r:gz") as archive:
        for member in archive.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts or member.issym() or member.islnk():
                raise RuntimeError("unsafe path in originals archive")
    return manifest


def restore_backup(
    config_path: Path,
    directory: Path,
    *,
    target_database: str,
    target_nas_root: Path,
    confirmation: str,
) -> None:
    manifest = verify_backup(directory)
    database, original_storage = _runtime(config_path)
    expected = f"RESTORE:{target_database}:{target_nas_root.resolve()}"
    if confirmation != expected:
        raise RuntimeError(f"confirmation must equal {expected}")
    if target_nas_root.exists() and any(target_nas_root.iterdir()):
        raise RuntimeError("target NAS directory must be empty")
    target_nas_root.mkdir(parents=True, exist_ok=True)
    if database.type == "mysql":
        mysql = database.mysql
        restore_user = os.environ.get("EASYAGENT_RESTORE_MYSQL_USER", mysql.user)
        restore_password = os.environ.get(
            "EASYAGENT_RESTORE_MYSQL_PASSWORD", mysql.password
        )
        if target_database == mysql.database or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", target_database):
            raise RuntimeError("restore target must be a different safe database name")
        connection = pymysql.connect(
            host=mysql.host, port=mysql.port, user=restore_user, password=restore_password,
            charset=mysql.charset,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{target_database}` CHARACTER SET utf8mb4")
                cursor.execute(
                    "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s",
                    (target_database,),
                )
                if int(cursor.fetchone()[0]) != 0:
                    raise RuntimeError("target database must be empty")
        finally:
            connection.close()
        executable = shutil.which("mysql")
        if not executable:
            raise RuntimeError("mysql client is required for restore")
        with (directory / "database.sql").open("rb") as dump:
            _run_database_client(
                [executable, "--host", mysql.host, "--port", str(mysql.port),
                 "--user", restore_user, target_database],
                password=restore_password,
                stdin=dump,
            )
    else:
        target = Path(target_database)
        if target.exists():
            raise RuntimeError("target SQLite file must not exist")
        shutil.copy2(directory / "database.sqlite", target)
    with tarfile.open(directory / "originals.tar.gz", "r:gz") as archive:
        archive.extractall(target_nas_root, filter="data")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Config.resolve_config_path())
    sub = parser.add_subparsers(dest="command", required=True)
    backup = sub.add_parser("backup")
    backup.add_argument("--output", type=Path, required=True)
    backup.add_argument("--release-tag", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--backup", type=Path, required=True)
    restore = sub.add_parser("restore")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--target-database", required=True)
    restore.add_argument("--target-nas-root", type=Path, required=True)
    restore.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.command == "backup":
        create_backup(args.config, args.output, args.release_tag)
    elif args.command == "verify":
        verify_backup(args.backup)
    else:
        restore_backup(
            args.config, args.backup, target_database=args.target_database,
            target_nas_root=args.target_nas_root, confirmation=args.confirm,
        )


if __name__ == "__main__":
    main()
