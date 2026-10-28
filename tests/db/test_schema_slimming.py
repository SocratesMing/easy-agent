"""表与索引精简（schema 11 起）：全新库只建在用的表；老库升级时删掉退役的表和索引，数据先搬走。"""

import json
import sqlite3

import pytest

import easy_agent.knowledges.core.schema  # noqa: F401 - 注册知识库建表（应用启动时同样会 import）
from easy_agent.db import migrations as mig
from easy_agent.db.database import Database
from easy_agent.db.schema import SCHEMA_VERSION

# 全新库建出来的全部表。新增表要同时改这里，让表的数量变化在评审里看得见。
FRESH_TABLES = {
    "agent_runs",
    "distributed_locks",
    "expert_agents",
    "generated_files",
    "knowledge_audit_events",
    "knowledge_bases",
    "knowledge_document_objects",
    "knowledge_documents",
    "knowledge_folders",
    "knowledge_operations",
    "knowledge_permissions",
    "knowledge_source_sync_items",
    "knowledge_team_space_managers",
    "langgraph_store",
    "mcp_api_keys",
    "rate_limit_counters",
    "scheduled_task_runs",
    "scheduled_tasks",
    "schema_meta",
    "session_files",
    "session_knowledge_scopes",
    "session_messages",
    "session_runtime",
    "sessions",
    "stream_events",
    "users",
}
RETIRED_TABLES = {
    "fmqt_lock",
    "fmqt_bloom",
    "fmqt_bloom_analysis",
    "tool_call_records",
    "thinking_records",
    "agent_checkpoints",
    "uploaded_file",
    "user_ip",
    "tool_call_record",
    "market_mcp_api_keys",
    "forex_rates",
    "precious_metals",
    "schema_migrations",
    "knowledge_original_access_audit",
    "knowledge_team_space_viewers",
    "knowledge_alerts",
    "knowledge_reconciliation_runs",
    "knowledge_reconciliation_issues",
    "knowledge_runtime_heartbeats",
    "knowledge_schema_migrations",
}
RETIRED_INDEXES = {
    "idx_updated_at": ("sessions", "updated_at"),
    "idx_sessions_username": ("sessions", "username"),
    "idx_session_messages_session_role": ("session_messages", "session_id, role"),
    "idx_generated_files_message": ("generated_files", "session_id, message_id"),
    "idx_mcp_api_keys_user": ("mcp_api_keys", "username"),
    "idx_expert_agents_status": ("expert_agents", "agent_status"),
    "idx_knowledge_folders_base": ("knowledge_folders", "base_id"),
    "idx_knowledge_documents_base": ("knowledge_documents", "base_id"),
}


def _init(path) -> Database:
    db = Database({"type": "sqlite", "sqlite": {"path": str(path)}})
    db.init_tables()
    return db


def _names(path, kind: str) -> set[str]:
    con = sqlite3.connect(path)
    try:
        rows = con.execute(
            "SELECT name FROM sqlite_master WHERE type=? AND name NOT LIKE 'sqlite_%'", (kind,)
        )
        return {r[0] for r in rows}
    finally:
        con.close()


def test_fresh_database_creates_only_the_tables_in_use(tmp_path):
    path = tmp_path / "fresh.db"
    _init(path)

    assert _names(path, "table") == FRESH_TABLES
    assert not _names(path, "index") & set(RETIRED_INDEXES)


def _make_old_database(path, version=10):
    """模拟老版本的库：退役的表 / 索引还在，迁移账本在旧表里，原文访问审计有一行。"""
    _init(path)
    con = sqlite3.connect(path)
    with con:
        con.execute(
            "UPDATE schema_meta SET meta_value=? WHERE meta_key='schema_version'", (str(version),)
        )
        con.execute("DELETE FROM schema_meta WHERE meta_key LIKE 'migration:%'")
        con.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, "
            "description VARCHAR(255) NOT NULL DEFAULT '', applied_at VARCHAR(50) NOT NULL)"
        )
        con.executemany(
            "INSERT INTO schema_migrations VALUES (?, ?, ?)",
            [(5, "baseline", "2026-10-01T00:00:00"), (6, "clear blobs", "2026-10-27T00:00:00")],
        )
        for table in RETIRED_TABLES - {"schema_migrations", "knowledge_original_access_audit"}:
            con.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        for index, (table, columns) in RETIRED_INDEXES.items():
            con.execute(f"CREATE INDEX {index} ON {table}({columns})")

        con.execute(
            "INSERT INTO users (user_id, username, created_at, updated_at) VALUES ('u1', 'alice', 't', 't')"
        )
        con.execute(
            "INSERT INTO knowledge_bases (id, name, description, space_type, owner_user_id, "
            "embedding_model, status, created_at, updated_at) "
            "VALUES ('b1', 'kb', '', 'personal', 'u1', 'm', 'active', 't', 't')"
        )
        con.execute(
            "INSERT INTO knowledge_documents (id, base_id, name, content_type, status, created_by, "
            "created_at, updated_at) VALUES ('d1', 'b1', 'a.pdf', 'application/pdf', 'done', 'u1', 't', 't')"
        )
        con.execute(
            "CREATE TABLE knowledge_original_access_audit (id VARCHAR(255) PRIMARY KEY, "
            "user_id VARCHAR(255) NOT NULL, session_id VARCHAR(255) NOT NULL DEFAULT '', "
            "agent_id VARCHAR(255) NOT NULL DEFAULT '', document_id VARCHAR(255) NOT NULL, "
            "action VARCHAR(40) NOT NULL, result VARCHAR(40) NOT NULL, "
            "request_id VARCHAR(255) NOT NULL DEFAULT '', created_at VARCHAR(50) NOT NULL)"
        )
        con.execute(
            "INSERT INTO knowledge_original_access_audit VALUES "
            "('a1', 'u1', 's1', 'api', 'd1', 'download', 'opened', 'req-1', '2026-10-20T08:00:00')"
        )
    con.close()


@pytest.mark.parametrize("old_version", [10, 12])
def test_upgrade_drops_retired_tables_and_indexes(tmp_path, old_version):
    """10：表精简之前的库；12：上一版。两种都要走一次完整初始化，删掉退役的表和索引。"""
    path = tmp_path / "old.db"
    _make_old_database(path, old_version)

    db = _init(path)

    tables = _names(path, "table")
    assert not tables & RETIRED_TABLES
    assert tables == FRESH_TABLES
    assert not _names(path, "index") & set(RETIRED_INDEXES)
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT meta_value FROM schema_meta WHERE meta_key='schema_version'")
        assert cursor.fetchone()[0] == str(SCHEMA_VERSION)


def test_upgrade_drops_tables_that_others_reference_by_foreign_key(tmp_path, monkeypatch):
    """MySQL 会拦下删除被外键引用的表；被拦的表之后启动走快速路径，再也删不掉。

    业务库的 SQLite 连接不开外键检查，这里打开它来模拟 MySQL：
    knowledge_reconciliation_issues.run_id 引用 knowledge_reconciliation_runs.id。
    """
    path = tmp_path / "fk.db"
    _init(path)
    con = sqlite3.connect(path)
    with con:
        con.execute("UPDATE schema_meta SET meta_value='12' WHERE meta_key='schema_version'")
        con.execute("CREATE TABLE knowledge_reconciliation_runs (id VARCHAR(255) PRIMARY KEY)")
        con.execute(
            "CREATE TABLE knowledge_reconciliation_issues (id VARCHAR(255) PRIMARY KEY, "
            "run_id VARCHAR(255) NOT NULL REFERENCES knowledge_reconciliation_runs(id))"
        )
        con.execute("INSERT INTO knowledge_reconciliation_runs VALUES ('r1')")
        con.execute("INSERT INTO knowledge_reconciliation_issues VALUES ('i1', 'r1')")
    con.close()

    connect = sqlite3.connect

    def connect_with_foreign_keys(*args, **kwargs):
        conn = connect(*args, **kwargs)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    monkeypatch.setattr(sqlite3, "connect", connect_with_foreign_keys)
    _init(path)
    monkeypatch.undo()

    assert not _names(path, "table") & {
        "knowledge_reconciliation_runs",
        "knowledge_reconciliation_issues",
    }


def test_upgrade_moves_the_migration_ledger_into_schema_meta(tmp_path):
    path = tmp_path / "ledger.db"
    _make_old_database(path)

    db = _init(path)
    _init(path)  # 再启动一次（快速路径）：不重复导入、不重复执行

    with db.get_connection() as conn:
        assert mig.applied_versions(db, conn.cursor()) == [5, 6]


def test_upgrade_moves_original_access_audit_into_audit_events(tmp_path):
    path = tmp_path / "audit.db"
    _make_old_database(path)

    db = _init(path)

    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM knowledge_audit_events WHERE id='a1'")
        row = dict(cursor.fetchone())
    assert row["action"] == "knowledge.original.download"
    assert row["object_type"] == "document"
    assert row["object_id"] == "d1"
    assert row["base_id"] == "b1"
    assert row["actor_user_id"] == "u1"
    assert row["actor_username"] == "alice"
    assert row["outcome"] == "opened"
    assert row["request_id"] == "req-1"
    assert row["created_at"] == "2026-10-20T08:00:00"
    assert json.loads(row["details_json"]) == {"session_id": "s1", "agent_id": "api"}
