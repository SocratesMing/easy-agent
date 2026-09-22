"""SQLite CRUD and constraints for the six-table MVP repository."""

from __future__ import annotations

import sqlite3

import pytest

from easy_agent.knowledge.repository import KnowledgeRepository
from easy_agent.models.db import SessionModel


def test_base_folder_document_permission_and_operation_crud(db):
    repository = KnowledgeRepository(db)
    base = repository.create_base(
        name="市场研报",
        description="公开测试资料",
        space_type="team",
        owner_user_id="owner",
        department_id="dept-market",
        embedding_model="embedding-test",
    )
    repository.update_base(
        base["id"], remote_dataset_id="remote-dataset", status="active"
    )

    folder = repository.create_folder(base_id=base["id"], name="宏观")
    child_folder = repository.create_folder(
        base_id=base["id"], name="利率", parent_id=folder["id"]
    )
    document = repository.create_document(
        base_id=base["id"],
        folder_id=folder["id"],
        name="report.pdf",
        content_type="application/pdf",
        size_bytes=123,
        created_by="owner",
    )
    repository.update_document(
        document["id"],
        remote_document_id="remote-document",
        status="processing",
        progress=0.25,
    )

    permissions = repository.replace_permissions(
        base_id=base["id"],
        permissions=[
            {"subject_type": "department", "subject_id": "dept-risk", "role": "viewer"},
            {"subject_type": "user", "subject_id": "maintainer", "role": "maintainer"},
        ],
        created_by="owner",
    )
    operation = repository.create_operation(
        operation_type="document_parse",
        resource_type="document",
        resource_id=document["id"],
        phase="parsing",
        status="running",
        created_by="owner",
    )

    assert repository.count_documents(base["id"]) == 1
    folders = {item["id"]: item for item in repository.list_folders(base["id"])}
    assert folders[folder["id"]]["document_count"] == 1
    assert folders[folder["id"]]["child_count"] == 1
    assert folders[child_folder["id"]]["parent_id"] == folder["id"]
    assert repository.list_documents(
        base["id"], folder_id=folder["id"], direct_only=True
    )[0]["id"] == document["id"]
    assert repository.list_documents(base["id"], direct_only=True) == []
    assert repository.delete_empty_folder(folder["id"]) is False
    assert {item["role"] for item in permissions} == {"viewer", "maintainer"}
    assert (
        repository.get_active_document_operation(document["id"])["id"]
        == operation["id"]
    )
    assert repository.update_operation(operation["id"], progress=0.5)["progress"] == 0.5
    repository.update_operation(operation["id"], status="succeeded")
    assert repository.get_active_document_operation(document["id"]) is None


def test_uniqueness_and_candidate_visibility(db):
    repository = KnowledgeRepository(db)
    base = repository.create_base(
        name="团队库",
        description="",
        space_type="team",
        owner_user_id="owner",
        department_id="dept-market",
        embedding_model="embedding-test",
    )
    repository.create_folder(base_id=base["id"], name="同名")

    with pytest.raises(sqlite3.IntegrityError):
        repository.create_folder(base_id=base["id"], name="同名")

    assert repository.list_candidate_bases(
        user_id="member", department_id="dept-market"
    )[0]["id"] == base["id"]
    assert repository.list_candidate_bases(
        user_id="outsider", department_id="dept-other"
    ) == []


def test_session_scope_is_replaced_atomically(db):
    repository = KnowledgeRepository(db)
    base_a = repository.create_base(
        name="A",
        description="",
        space_type="personal",
        owner_user_id="owner",
        department_id=None,
        embedding_model="embedding-test",
    )
    base_b = repository.create_base(
        name="B",
        description="",
        space_type="shared",
        owner_user_id="owner",
        department_id=None,
        embedding_model="embedding-test",
    )
    db.create_session(
        SessionModel(session_id="session-1", title="test", username="alice")
    )

    assert repository.replace_session_scope(
        session_id="session-1",
        user_id="user-1",
        base_ids=[base_a["id"], base_a["id"], base_b["id"]],
    ) == [base_a["id"], base_b["id"]]
    assert repository.has_session_scope(session_id="session-1") is True
    assert repository.replace_session_scope(
        session_id="session-1", user_id="user-1", base_ids=[base_b["id"]]
    ) == [base_b["id"]]
    assert repository.has_session_scope(session_id="missing-session") is False
