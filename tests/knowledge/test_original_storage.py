from __future__ import annotations

import hashlib
import io

import pytest

from easy_agent.knowledge.repository import KnowledgeRepository
from easy_agent.knowledge.config import KnowledgeConfig, OriginalStorageConfig
from easy_agent.models.db import UserModel
from easy_agent.knowledge.original_tool import KnowledgeOriginalTool
from easy_agent.knowledge.storage import FileSystemOriginalStore, OriginalStorageError


def _store(tmp_path):
    config = OriginalStorageConfig.model_validate(
        {
            "enabled": True,
            "driver": "local_fs",
            "filesystem": {"root_path": str(tmp_path), "path_prefix": "originals"},
        }
    )
    return FileSystemOriginalStore(config)


def test_filesystem_store_atomic_round_trip_range_and_quarantine(tmp_path):
    store = _store(tmp_path)
    payload = b"0123456789"
    key = store.build_key("base-1", "doc-1", "report.pdf")
    stored = store.put_atomic(key, io.BytesIO(payload), len(payload))
    assert stored.sha256 == hashlib.sha256(payload).hexdigest()
    assert b"".join(store.open(key).iter_bytes(2, 5)) == b"2345"

    quarantine_key = store.quarantine(key)
    with pytest.raises(OriginalStorageError):
        store.open(key)
    assert b"".join(store.open(quarantine_key).iter_bytes()) == payload


def test_filesystem_store_rejects_escape_and_conflicting_overwrite(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(OriginalStorageError) as escape:
        store.open("../secret")
    assert escape.value.code == "ORIGINAL_STORAGE_INVALID_KEY"

    key = store.build_key("base-1", "doc-1", "report.pdf")
    store.put_atomic(key, io.BytesIO(b"first"), 5)
    with pytest.raises(OriginalStorageError) as conflict:
        store.put_atomic(key, io.BytesIO(b"other"), 5)
    assert conflict.value.code == "ORIGINAL_STORAGE_CONFLICT"


def test_agent_tool_materializes_only_an_authorized_original(db, tmp_path):
    db.create_user(UserModel("user-alice", "alice", "", "dept-market"))
    db.create_user(UserModel("user-bob", "bob", "", "dept-risk"))
    repository = KnowledgeRepository(db)
    base = repository.create_base(
        name="私有原文库",
        description="",
        space_type="personal",
        owner_user_id="user-alice",
        department_id="dept-market",
        embedding_model="embedding-test",
    )
    document = repository.create_document(
        base_id=base["id"],
        folder_id=None,
        name="report.txt",
        content_type="text/plain",
        size_bytes=13,
        created_by="user-alice",
    )
    store = _store(tmp_path / "nas")
    key = store.build_key(base["id"], document["id"], document["name"])
    repository.create_original_object(
        document_id=document["id"],
        provider=store.provider,
        storage_key=key,
        original_name=document["name"],
        content_type="text/plain",
        size_bytes=13,
        created_by="user-alice",
    )
    stored = store.put_atomic(key, io.BytesIO(b"complete file"), 13)
    repository.update_original_object(
        document["id"], status="available", sha256=stored.sha256
    )
    config = KnowledgeConfig(
        original_storage=OriginalStorageConfig.model_validate(
            {
                "enabled": True,
                "filesystem": {"root_path": str(tmp_path / "nas")},
            }
        )
    )

    alice_workspace = tmp_path / "workspace" / "alice"
    alice_tool = KnowledgeOriginalTool(
        username="alice",
        session_id="session-a",
        workspace_dir=str(alice_workspace),
        knowledge_config=config,
        original_store=store,
    )
    result = alice_tool._run(action="materialize", document_id=document["id"])
    assert "/workspace/knowledge-originals/" in result
    assert (
        alice_workspace / "knowledge-originals" / document["id"] / "report.txt"
    ).read_bytes() == b"complete file"

    bob_workspace = tmp_path / "workspace" / "bob"
    bob_tool = KnowledgeOriginalTool(
        username="bob",
        session_id="session-b",
        workspace_dir=str(bob_workspace),
        knowledge_config=config,
        original_store=store,
    )
    denied = bob_tool._run(action="materialize", document_id=document["id"])
    assert "知识资源不存在" in denied
    assert not bob_workspace.exists()

    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT user_id, result FROM knowledge_original_access_audit "
            "WHERE document_id=? ORDER BY created_at",
            (document["id"],),
        )
        audits = [dict(row) for row in cursor.fetchall()]
    assert {item["user_id"] for item in audits} == {"user-alice", "user-bob"}
    assert {item["result"] for item in audits} == {"success", "denied_or_failed"}
