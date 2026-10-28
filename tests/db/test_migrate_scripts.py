"""scripts/db 下的迁移脚本在独立进程里跑：只靠脚本自己的 import，表要建全、默认管理员要能建。

回归：知识库建表函数和默认管理员策略都在 import 时注册进 db，应用启动时由 app.py 导入，
这几个脚本原来都没导入（pytest 进程里 conftest 早就导入过了，所以只能在子进程里测）：
- migrate_db.py：新库只建核心表就写入最新版本号，老库的知识库部分不迁移，最后报
  「默认管理员策略未注册」退出；
- migrate_market_mcp_keys.py：不读配置，总去初始化 ./data/easy_agent.db，同样报错退出；
- migrate_sqlite_to_mysql.py：给空目标库建表时没有 knowledge_* 表，迁移中止。
"""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import yaml

from tests.db.test_schema_slimming import FRESH_TABLES, _make_old_database, _names

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts" / "db"


def _run(tmp_path, *argv, db_path):
    """按生产用法在独立进程里跑：配置指向临时 SQLite 库，不读开发机的 .env。"""
    with open(os.environ["EASY_CONFIG"], encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config["database"]["sqlite"]["path"] = str(db_path)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    env = {
        **os.environ,
        "EASY_CONFIG": str(config_path),
        "EASY_ENV_FILE": str(tmp_path / "absent.env"),
        "PYTHONPATH": os.pathsep.join(filter(None, [str(REPO_ROOT), os.environ.get("PYTHONPATH")])),
    }
    env.pop("EASY_AUTO_MIGRATE", None)
    return subprocess.run(
        [sys.executable, *argv],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )


def test_migrate_db_creates_every_table_on_a_fresh_database(tmp_path):
    db_path = tmp_path / "fresh.db"

    migrate = _run(tmp_path, str(SCRIPTS / "migrate_db.py"), db_path=db_path)
    assert migrate.returncode == 0, migrate.stderr[-2000:]
    assert _names(db_path, "table") == FRESH_TABLES

    # Pod 设 EASY_AUTO_MIGRATE=0 时只做这个校验
    check = _run(tmp_path, str(SCRIPTS / "migrate_db.py"), "--check", db_path=db_path)
    assert check.returncode == 0, check.stderr[-2000:]


def test_migrate_db_also_migrates_the_knowledge_tables(tmp_path):
    db_path = tmp_path / "old.db"
    _make_old_database(db_path)

    migrate = _run(tmp_path, str(SCRIPTS / "migrate_db.py"), db_path=db_path)

    assert migrate.returncode == 0, migrate.stderr[-2000:]
    assert _names(db_path, "table") == FRESH_TABLES
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("SELECT action FROM knowledge_audit_events WHERE id='a1'").fetchall()
    finally:
        con.close()
    assert rows == [("knowledge.original.download",)]


def test_migrate_market_keys_uses_the_configured_database(tmp_path):
    db_path = tmp_path / "configured.db"

    result = _run(tmp_path, str(SCRIPTS / "migrate_market_mcp_keys.py"), db_path=db_path)

    assert result.returncode == 0, result.stderr[-2000:]
    assert "不是 MySQL" in result.stdout
    assert _names(db_path, "table") == FRESH_TABLES
    assert not (tmp_path / "data").exists()


def test_migrate_sqlite_to_mysql_creates_the_knowledge_tables(tmp_path):
    """目标库得是 MySQL，这里只看脚本自己的 import 下 init_tables() 建不建知识库表。"""
    db_path = tmp_path / "target.db"
    code = (
        "import runpy, sys\n"
        "runpy.run_path(sys.argv[1])\n"
        "from easy_agent.db.database import Database\n"
        "Database({'type': 'sqlite', 'sqlite': {'path': sys.argv[2]}}).init_tables()\n"
    )

    result = _run(
        tmp_path,
        "-c",
        code,
        str(SCRIPTS / "migrate_sqlite_to_mysql.py"),
        str(db_path),
        db_path=db_path,
    )

    assert result.returncode == 0, result.stderr[-2000:]
    assert _names(db_path, "table") == FRESH_TABLES
