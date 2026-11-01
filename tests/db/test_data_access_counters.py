"""T3 数据访问层收敛：``check_double_write`` 的四个只减不增计数。

对应 docs/design/2026-11-01-backend-improvement-backlog.md 的 T3 与
``scripts/quality/double_write_allowlists.py`` 里的 ``MAX_*`` 上限：

- ``Database`` 方法总数 / 纯委托方法数；
- ``easy_agent/db/`` 之外 ``get_database()`` 调用数；
- ``easy_agent/db/`` 之外 ``repo.database`` / ``repository.database`` /
  ``db.database`` 逃生口引用数。

用例分两类：扫描器语义（tmp 目录样本，不依赖真实仓库）与真实仓库门禁（防止悄悄
回退）。上限只允许下调，见 allowlist 的注释。
"""

from __future__ import annotations

from pathlib import Path

from easy_agent.db.database import Database
from scripts.quality import double_write_allowlists as allowlists
from scripts.quality import double_write_scanner as scanner

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ── 扫描器语义 ────────────────────────────────────────────────────────


def test_get_database_counter_ignores_db_package_comments_and_strings(tmp_path):
    _write(
        tmp_path,
        "easy_agent/foo.py",
        "from easy_agent.db import get_database\n"
        "\n"
        "\n"
        "def build():\n"
        "    # get_database()\n"
        '    text = "get_database()"\n'
        "    return get_database(), text\n",
    )
    # db/ 内部的调用不计入（那是数据库层自己的接缝）
    _write(
        tmp_path,
        "easy_agent/db/factory.py",
        "from easy_agent.db.database import get_database\n"
        "\n"
        "def current():\n"
        "    return get_database()\n",
    )
    assert scanner.count_get_database_calls(tmp_path) == 1


def test_escape_hatch_counter_only_counts_attribute_access(tmp_path):
    _write(
        tmp_path,
        "easy_agent/foo.py",
        "from easy_agent.db.database import Database\n"
        "\n"
        "\n"
        "def wire(repo, repository, db):\n"
        "    # repo.database 注释不算\n"
        "    from_import = Database\n"
        "    return repo.database, repository.database, db.database, from_import\n",
    )
    _write(
        tmp_path,
        "easy_agent/db/bar.py",
        "def f(repo):\n    return repo.database\n",
    )
    assert scanner.count_database_escape_hatches(tmp_path) == 3


def test_limit_violations_are_reported_for_every_counter():
    stats = {
        "database_methods": allowlists.MAX_DATABASE_METHODS + 1,
        "database_delegations": allowlists.MAX_DATABASE_PURE_DELEGATIONS + 1,
        "get_database_calls_outside_db": (
            allowlists.MAX_GET_DATABASE_CALLS_OUTSIDE_DB + 1
        ),
        "database_escape_hatch_refs": (
            allowlists.MAX_DATABASE_ESCAPE_HATCH_REFS + 1
        ),
    }
    codes = {violation.code for violation in scanner._counter_violations(stats)}
    assert codes == {"DW210", "DW211", "DW212", "DW213"}


def test_at_limit_is_clean():
    stats = {
        "database_methods": allowlists.MAX_DATABASE_METHODS,
        "database_delegations": allowlists.MAX_DATABASE_PURE_DELEGATIONS,
        "get_database_calls_outside_db": (
            allowlists.MAX_GET_DATABASE_CALLS_OUTSIDE_DB
        ),
        "database_escape_hatch_refs": allowlists.MAX_DATABASE_ESCAPE_HATCH_REFS,
    }
    assert scanner._counter_violations(stats) == []


# ── 真实仓库门禁 ──────────────────────────────────────────────────────


def test_repository_data_access_counters_are_at_or_under_the_limits():
    result = scanner.check_repository(REPO_ROOT)
    assert result.ok, "\n".join(violation.format() for violation in result.violations)
    stats = result.stats
    # 上限（只减不增）：超过会被 check_repository 报 DW210 ~ DW213
    assert stats["database_methods"] <= allowlists.MAX_DATABASE_METHODS
    assert stats["database_delegations"] <= allowlists.MAX_DATABASE_PURE_DELEGATIONS
    assert (
        stats["get_database_calls_outside_db"]
        <= allowlists.MAX_GET_DATABASE_CALLS_OUTSIDE_DB
    )
    assert (
        stats["database_escape_hatch_refs"]
        <= allowlists.MAX_DATABASE_ESCAPE_HATCH_REFS
    )
    # 下限：防止扫描器静默失效（什么都数不到时上面的「超上限」断言会假通过）
    assert stats["database_methods"] >= 90
    assert stats["database_delegations"] >= 55
    assert stats["get_database_calls_outside_db"] >= 10
    assert stats["database_escape_hatch_refs"] >= 5


def test_raising_a_limit_immediately_fails_the_gate(monkeypatch):
    """当前实际计数减 1 当上限：门禁必须报 DW212（防止新增调用悄悄通过）。"""
    monkeypatch.setattr(
        scanner,
        "MAX_GET_DATABASE_CALLS_OUTSIDE_DB",
        scanner.count_get_database_calls(REPO_ROOT) - 1,
    )
    result = scanner.check_repository(REPO_ROOT)
    assert not result.ok
    assert any(violation.code == "DW212" for violation in result.violations)


def test_deleted_session_domain_delegations_stay_deleted():
    """T3a 删掉的 Database 转发不能被悄悄加回来（加回来会被 DW201/DW202 拦）。"""
    deleted = [
        "get_messages",
        "get_messages_from_rows",
        "start_run",
        "renew_run",
        "finish_run",
        "get_run",
        "list_stale_running",
        "mark_interrupted",
        "get_session_owner",
        "list_sessions",
        "count_sessions",
        "delete_session",
        "update_session_todos",
        "delete_stream_events_before",
    ]
    for name in deleted:
        assert not hasattr(Database, name), name
        assert name not in allowlists.FROZEN_DATABASE_METHODS, name