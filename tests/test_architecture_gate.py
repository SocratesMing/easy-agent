"""阶段 0 架构门禁测试：import 图 SCC + 双写冻结。

所有用例都用 ``tmp_path`` 注入坏/好样本，不依赖网络、真实数据库或
``tests/conftest.py`` 里的 app fixture。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"


def _load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"arch_gate_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


import_graph = _load_script("import_graph")
double_write = _load_script("check_double_write")


def _write(root: Path, relative: str, content: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_cycle_package(root: Path) -> None:
    _write(root, "pkg/__init__.py", "")
    _write(root, "pkg/a.py", "from . import b\nfrom .sub import helper\n")
    _write(root, "pkg/b.py", "from pkg.a import thing\n")
    _write(root, "pkg/sub/__init__.py", "from ..util import thing\n")
    _write(root, "pkg/util.py", "")


def _write_whitelist(path: Path, entries: list[dict]) -> Path:
    path.write_text(
        json.dumps({"schema_version": 1, "sccs": entries}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


# --- import graph ------------------------------------------------------------


def test_import_graph_resolves_relative_and_absolute_imports(tmp_path):
    _make_cycle_package(tmp_path)
    graph = import_graph.build_graph(tmp_path, "pkg")
    adjacency = graph.adjacency()

    assert set(graph.modules) == {"pkg", "pkg.a", "pkg.b", "pkg.sub", "pkg.util"}
    assert adjacency["pkg.a"] == {"pkg.b", "pkg.sub"}
    assert adjacency["pkg.b"] == {"pkg.a"}
    assert adjacency["pkg.sub"] == {"pkg.util"}
    assert graph.cycles() == [["pkg.a", "pkg.b"]]


def test_import_graph_whitelist_comparison(tmp_path):
    _make_cycle_package(tmp_path)
    cycles = import_graph.build_graph(tmp_path, "pkg").cycles()

    # 没有环时，空白名单就是通过（SCC=0 的目标态）。
    assert import_graph.compare_sccs([], [], "pkg") == []

    empty_errors = import_graph.compare_sccs(cycles, [], "pkg")
    assert any("白名单为空" in error for error in empty_errors)
    assert any("新增未白名单环" in error for error in empty_errors)

    known = [{"id": "known", "reason": "fixture cycle", "modules": ["pkg.a", "pkg.b"]}]
    assert import_graph.compare_sccs(cycles, known, "pkg") == []

    changed = [
        {
            "id": "known",
            "reason": "fixture cycle",
            "modules": ["pkg.a", "pkg.b", "pkg.c"],
        }
    ]
    changed_errors = import_graph.compare_sccs(cycles, changed, "pkg")
    assert any("模块集合变化" in error for error in changed_errors)
    assert any("pkg.c" in error for error in changed_errors)

    stale_errors = import_graph.compare_sccs([], known, "pkg")
    assert any("白名单过期环" in error for error in stale_errors)


def test_whitelist_requires_reasons_and_unique_modules(tmp_path):
    whitelist = tmp_path / "whitelist.json"
    whitelist.write_text(
        json.dumps({"sccs": [{"modules": ["pkg.a", "pkg.b"]}]}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="reason"):
        import_graph.load_whitelist(whitelist, "pkg")

    _write_whitelist(
        whitelist, [{"reason": "duplicate", "modules": ["pkg.a", "pkg.a"]}]
    )
    with pytest.raises(ValueError, match="duplicates"):
        import_graph.load_whitelist(whitelist, "pkg")

    _write_whitelist(
        whitelist,
        [
            {"reason": "one", "modules": ["pkg.a"]},
            {"reason": "two", "modules": ["pkg.a"]},
        ],
    )
    with pytest.raises(ValueError, match="both"):
        import_graph.load_whitelist(whitelist, "pkg")


def test_import_graph_cli_exit_codes_and_json(tmp_path):
    _make_cycle_package(tmp_path)
    script = SCRIPTS / "import_graph.py"
    base = [
        sys.executable,
        str(script),
        "--root",
        str(tmp_path),
        "--package",
        "pkg",
    ]

    empty = _write_whitelist(tmp_path / "empty.json", [])
    failed = subprocess.run(
        base + ["--check-scc", "--whitelist", str(empty)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert failed.returncode == 1
    assert "[check-scc] FAIL" in failed.stdout

    known = _write_whitelist(
        tmp_path / "known.json",
        [{"id": "known", "reason": "fixture cycle", "modules": ["pkg.a", "pkg.b"]}],
    )
    passed = subprocess.run(
        base + ["--check-scc", "--whitelist", str(known)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert passed.returncode == 0, passed.stderr
    assert "[check-scc] OK" in passed.stdout

    report = subprocess.run(
        base + ["--json"], capture_output=True, text=True, check=False
    )
    assert report.returncode == 0
    data = json.loads(report.stdout)
    assert data["n_modules"] == 5
    assert data["sccs"][0]["size"] == 2
    assert data["sccs"][0]["modules"] == ["pkg.a", "pkg.b"]
    assert any(edge["from"] == "pkg.a" and edge["to"] == "pkg.b" for edge in data["edges"])


def test_repository_import_graph_matches_whitelist():
    graph = import_graph.build_graph(REPO_ROOT, "easy_agent")
    entries = import_graph.load_whitelist(
        SCRIPTS / "import_graph_whitelist.json", "easy_agent"
    )

    # 阶段 A1 断环前，现有知识库/聊天/调度环必须被显式记录（带原因）。
    assert entries, "白名单不能为空：现有 SCC 需要逐项记录原因"
    assert import_graph.compare_sccs(graph.cycles(), entries, "easy_agent") == []


# --- double-write freeze -----------------------------------------------------

BAD_SESSIONS = """
import json


def create_session(db, session_data):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            \"\"\"
            INSERT INTO sessions (session_id, title, messages, created_at)
            VALUES (?, ?, ?, ?)
            \"\"\",
            (
                session_data.session_id,
                session_data.title,
                json.dumps(session_data.messages, ensure_ascii=False),
                session_data.created_at,
            ),
        )
"""

GOOD_SESSIONS = """
import json


def create_session(db, session_data):
    db._execute(
        cursor,
        "INSERT INTO sessions (session_id, title, messages, created_at) "
        "VALUES (?, ?, ?, ?)",
        (session_data.session_id, session_data.title, "", session_data.created_at),
    )


def update_session(db, session_data):
    db._execute(
        cursor,
        "UPDATE sessions SET title=?, updated_at=? WHERE session_id=?",
        (session_data.title, session_data.updated_at, session_data.session_id),
    )


def read_legacy(db, session_id):
    db._execute(cursor, "SELECT messages FROM sessions WHERE session_id=?", (session_id,))
    raw = row["messages"]
    return json.loads(raw) if raw else []


def update_todos(db, session_id, todos):
    db._execute(
        cursor,
        "UPDATE sessions SET todos=? WHERE session_id=?",
        (json.dumps(todos), session_id),
    )
"""

BAD_DATABASE = """
from .repositories import sessions as sessions_repo


class Database:
    def get_connection(self):
        return None

    def get_session(self, *args, **kwargs):
        return sessions_repo.get_session(self, *args, **kwargs)

    def create_knowledge_base(self, name):
        return {"name": name}
"""

DIRECT_SQL_DATABASE = """
from .repositories import sessions as sessions_repo


class Database:
    def get_session(self, session_id):
        with self.get_connection() as conn:
            return conn.execute("SELECT * FROM sessions").fetchone()
"""


def test_sessions_checker_flags_dynamic_message_writes(tmp_path):
    path = _write(
        tmp_path, "easy_agent/db/repositories/sessions.py", BAD_SESSIONS
    )
    violations = double_write.check_sessions_file(path)
    codes = {violation.code for violation in violations}

    assert "DW101" in codes
    assert "DW102" in codes
    assert all(violation.line > 0 for violation in violations)
    assert all(violation.function for violation in violations)


def test_sessions_checker_allows_fixed_empty_and_legacy_reads(tmp_path):
    path = _write(
        tmp_path, "easy_agent/db/repositories/sessions.py", GOOD_SESSIONS
    )
    assert double_write.check_sessions_file(path) == []


def test_database_checker_rejects_unknown_and_non_delegating_methods(tmp_path):
    bad = _write(tmp_path, "easy_agent/db/database.py", BAD_DATABASE)
    codes = {violation.code for violation in double_write.check_database_file(bad)}
    assert "DW201" in codes

    direct = _write(tmp_path, "easy_agent/db/direct_database.py", DIRECT_SQL_DATABASE)
    codes = {violation.code for violation in double_write.check_database_file(direct)}
    assert "DW202" in codes


def test_double_write_cli_fails_on_bad_sample(tmp_path):
    _write(tmp_path, "easy_agent/db/repositories/sessions.py", BAD_SESSIONS)
    _write(tmp_path, "easy_agent/db/database.py", BAD_DATABASE)

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "check_double_write.py"),
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 1
    assert "easy_agent/db/repositories/sessions.py:" in completed.stdout
    assert "DW101" in completed.stdout
    assert "DW201" in completed.stdout


def test_repository_double_write_gate_is_green():
    result = double_write.check_repository(REPO_ROOT)
    assert result.ok, "\n".join(violation.format() for violation in result.violations)
    assert result.stats.get("database_methods", 0) >= 100
    assert result.stats.get("database_delegations", 0) >= 100
