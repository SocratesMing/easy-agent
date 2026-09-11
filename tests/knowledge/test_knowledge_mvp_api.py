"""API-level MVP flow with a deterministic in-memory RAGFlow double."""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from easy_agent.app import app
from easy_agent.knowledge.config import KnowledgeConfig
from easy_agent.knowledge.ragflow import RagflowBinary
from easy_agent.knowledge.repository import KnowledgeRepository
from easy_agent.models.db import SessionModel, UserModel
from easy_agent.knowledge.storage import OriginalStorageError, create_original_store
from easy_agent.utils.auth import create_access_token


TEMPLATE = Path(__file__).parents[1] / "fixtures" / "knowledge-config.yaml"


class FakeRagflow:
    def __init__(self):
        self.datasets: dict[str, dict] = {}
        self.documents: dict[str, dict[str, dict]] = {}
        self.next_dataset = 1
        self.next_document = 1

    async def close(self):
        pass

    async def health(self, **kwargs):
        return True

    async def create_dataset(self, *, name, description, **kwargs):
        dataset_id = f"remote-dataset-{self.next_dataset}"
        self.next_dataset += 1
        self.datasets[dataset_id] = {"id": dataset_id, "name": name, "description": description}
        self.documents[dataset_id] = {}
        return self.datasets[dataset_id]

    async def update_dataset(self, dataset_id, values, **kwargs):
        self.datasets[dataset_id].update(values)

    async def delete_datasets(self, dataset_ids, **kwargs):
        for dataset_id in dataset_ids:
            self.datasets.pop(dataset_id, None)
            self.documents.pop(dataset_id, None)

    async def upload_document(self, *, dataset_id, filename, content, content_type, **kwargs):
        document_id = f"remote-document-{self.next_document}"
        self.next_document += 1
        raw = content.read()
        content.seek(0)
        document = {
            "id": document_id,
            "name": filename,
            "content_type": content_type,
            "content": raw,
            "run": "0",
            "progress": 0.0,
        }
        self.documents[dataset_id][document_id] = document
        return document

    async def configure_document(self, **kwargs):
        pass

    async def start_parsing(self, *, dataset_id, document_ids, **kwargs):
        for document_id in document_ids:
            self.documents[dataset_id][document_id].update({"run": "3", "progress": 1.0})

    async def list_documents(self, *, dataset_id, **kwargs):
        docs = [
            {key: value for key, value in document.items() if key != "content"}
            for document in self.documents[dataset_id].values()
        ]
        return {"docs": docs, "total": len(docs)}

    async def download_document(self, *, dataset_id, document_id, **kwargs):
        document = self.documents[dataset_id][document_id]
        return RagflowBinary(document["content"], document["content_type"])

    async def delete_documents(self, *, dataset_id, document_ids, **kwargs):
        for document_id in document_ids:
            self.documents[dataset_id].pop(document_id, None)

    async def retrieve(self, *, dataset_ids, document_ids, **kwargs):
        dataset_id = dataset_ids[0]
        document_id = document_ids[0]
        document = self.documents[dataset_id][document_id]
        return {
            "chunks": [
                {
                    "document_id": document_id,
                    "document_keyword": document["name"],
                    "content": "报告显示测试证据成立。",
                    "similarity": 0.91,
                    "positions": [[1, 2, 3, 4, 5]],
                }
            ],
            "total": 1,
        }


def _enabled_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeConfig:
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    data["enabled"] = True
    data["original_storage"]["enabled"] = True
    data["original_storage"]["filesystem"]["root_path"] = str(
        tmp_path / "nas-sim"
    )
    path = tmp_path / "knowledge-enabled.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("RAGFLOW_BASE_URL", "https://ragflow.example.test")
    monkeypatch.setenv("RAGFLOW_API_KEY", "secret")
    monkeypatch.setenv("RAGFLOW_EMBEDDING_MODEL", "embedding-test")
    return KnowledgeConfig.from_yaml(path)


@pytest.fixture
def knowledge_api(client, db, tmp_path, monkeypatch):
    old_config = app.state.knowledge_config
    old_client = getattr(app.state, "ragflow_client", None)
    old_original_store = getattr(app.state, "original_store", None)
    fake = FakeRagflow()
    app.state.knowledge_config = _enabled_config(tmp_path, monkeypatch)
    app.state.ragflow_client = fake
    app.state.original_store = create_original_store(app.state.knowledge_config)
    users = [
        UserModel("user-alice", "alice", "", "dept-market"),
        UserModel("user-bob", "bob", "", "dept-market"),
        UserModel("user-carol", "carol", "", "dept-risk"),
    ]
    for user in users:
        db.create_user(user)
    repository = KnowledgeRepository(db)
    repository.grant_team_space_manager(
        user_id="user-alice", granted_by="test-bootstrap"
    )
    tokens = {
        user.username: create_access_token(
            {"sub": user.username, "v": user.token_version}
        )
        for user in users
    }
    admin = db.get_user_by_username("admin")
    assert admin is not None
    tokens["admin"] = create_access_token(
        {"sub": admin.username, "v": admin.token_version}
    )
    yield client, db, fake, tokens
    app.state.knowledge_config = old_config
    app.state.ragflow_client = old_client
    app.state.original_store = old_original_store


def _auth(tokens, username):
    return {"Authorization": f"Bearer {tokens[username]}"}


def test_knowledge_routes_are_fail_closed_without_bearer(knowledge_api):
    client, _, _, _ = knowledge_api

    response = client.get("/api/knowledge/v1/status", headers={"X-Username": "alice"})

    assert response.status_code == 401


def test_stalled_ragflow_parse_becomes_retryable_timeout(knowledge_api):
    client, db, fake, tokens = knowledge_api
    alice = _auth(tokens, "alice")
    created = client.post(
        "/api/knowledge/v1/bases",
        headers=alice,
        json={"name": "超时测试库", "visibility": "personal"},
    )
    base_id = created.json()["id"]
    uploaded = client.post(
        f"/api/knowledge/v1/bases/{base_id}/documents",
        headers=alice,
        files={"file": ("large.xlsx", b"workbook", "application/vnd.ms-excel")},
    )
    document_id = uploaded.json()["document"]["id"]
    dataset_id = next(reversed(fake.datasets))
    remote_document = next(iter(fake.documents[dataset_id].values()))
    remote_document.update({"run": "1", "progress": 0.1})
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE knowledge_operations SET created_at=? WHERE resource_id=?",
            ("2000-01-01T00:00:00+00:00", document_id),
        )

    response = client.get(
        f"/api/knowledge/v1/bases/{base_id}/documents", headers=alice
    )

    document = response.json()["items"][0]
    assert document["status"] == "failed"
    assert document["error"]["code"] == "KNOWLEDGE_PARSE_TIMEOUT"
    assert "retry_document" in document["allowed_actions"]


def test_completed_index_probe_recovers_running_and_timed_out_document(knowledge_api):
    client, db, fake, tokens = knowledge_api
    alice = _auth(tokens, "alice")
    base_id = client.post(
        "/api/knowledge/v1/bases", headers=alice,
        json={"name": "完成态探针库", "visibility": "personal"},
    ).json()["id"]
    uploaded = client.post(
        f"/api/knowledge/v1/bases/{base_id}/documents", headers=alice,
        files={"file": ("probe.pdf", b"indexed", "application/pdf")},
    )
    document_id = uploaded.json()["document"]["id"]
    operation_id = uploaded.json()["operation"]["id"]
    dataset_id = next(reversed(fake.datasets))
    remote = next(iter(fake.documents[dataset_id].values()))
    remote.update({"run": "1", "progress": 0.01, "chunk_count": 0, "token_count": 0})
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE knowledge_operations SET created_at=? WHERE resource_id=?",
            ("2000-01-01T00:00:00+00:00", document_id),
        )

    timed_out = client.get(
        f"/api/knowledge/v1/bases/{base_id}/documents", headers=alice
    ).json()["items"][0]
    assert timed_out["error"]["code"] == "KNOWLEDGE_PARSE_TIMEOUT"

    remote.update({"chunk_count": 2, "token_count": 20})
    recovered = client.get(
        f"/api/knowledge/v1/bases/{base_id}/documents", headers=alice
    ).json()["items"][0]
    assert recovered["status"] == "ready"
    assert recovered["progress"] == 1.0
    operation = client.get(
        f"/api/knowledge/v1/operations/{operation_id}", headers=alice
    ).json()
    assert operation["status"] == "succeeded"
    assert operation["resource_id"] == document_id


def test_completed_index_probe_rejects_wrong_document_chunk(knowledge_api, monkeypatch):
    client, db, fake, tokens = knowledge_api
    alice = _auth(tokens, "alice")
    base_id = client.post(
        "/api/knowledge/v1/bases", headers=alice,
        json={"name": "探针隔离库", "visibility": "personal"},
    ).json()["id"]
    uploaded = client.post(
        f"/api/knowledge/v1/bases/{base_id}/documents", headers=alice,
        files={"file": ("probe.pdf", b"indexed", "application/pdf")},
    )
    document_id = uploaded.json()["document"]["id"]
    dataset_id = next(reversed(fake.datasets))
    remote = next(iter(fake.documents[dataset_id].values()))
    remote.update({"run": "1", "progress": 0.01, "chunk_count": 2, "token_count": 20})
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE knowledge_operations SET created_at=? WHERE resource_id=?",
            ("2000-01-01T00:00:00+00:00", document_id),
        )

    async def wrong_document(**kwargs):
        return {"chunks": [{"document_id": "another-document", "content": "wrong"}]}

    monkeypatch.setattr(fake, "retrieve", wrong_document)
    document = client.get(
        f"/api/knowledge/v1/bases/{base_id}/documents", headers=alice
    ).json()["items"][0]
    assert document["status"] == "failed"
    assert document["error"]["code"] == "KNOWLEDGE_PARSE_TIMEOUT"


def test_duplicate_multipart_files_are_rejected_without_side_effects(knowledge_api):
    client, _, fake, tokens = knowledge_api
    alice = _auth(tokens, "alice")
    base_id = client.post(
        "/api/knowledge/v1/bases", headers=alice,
        json={"name": "上传校验库", "visibility": "personal"},
    ).json()["id"]
    response = client.post(
        f"/api/knowledge/v1/bases/{base_id}/documents",
        headers=alice,
        files=[
            ("file", ("first.txt", b"first", "text/plain")),
            ("file", ("second.txt", b"second", "text/plain")),
        ],
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "KNOWLEDGE_FILE_COUNT_LIMIT"
    assert all(not documents for documents in fake.documents.values())
    listed = client.get(
        f"/api/knowledge/v1/bases/{base_id}/documents", headers=alice
    ).json()
    assert listed["page"]["total"] == 0


def test_team_space_document_permission_retrieval_and_download_flow(
    knowledge_api, monkeypatch
):
    client, _, fake, tokens = knowledge_api
    admin = _auth(tokens, "admin")
    alice = _auth(tokens, "alice")
    bob = _auth(tokens, "bob")
    carol = _auth(tokens, "carol")

    response = client.post(
        "/api/knowledge/v1/bases",
        headers=alice,
        json={"name": "市场研报", "description": "test", "visibility": "team"},
    )
    assert response.status_code == 201, response.text
    base = response.json()
    base_id = base["id"]
    assert base["role"] == "manager"
    assert "remote-dataset" not in response.text
    remote_dataset = next(iter(fake.datasets.values()))
    assert re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", remote_dataset["name"])
    assert "市场研报" not in remote_dataset["name"]

    folder = client.post(
        f"/api/knowledge/v1/bases/{base_id}/folders",
        headers=alice,
        json={"name": "宏观"},
    )
    assert folder.status_code == 201, folder.text
    folder_id = folder.json()["id"]

    child_folder = client.post(
        f"/api/knowledge/v1/bases/{base_id}/folders",
        headers=alice,
        json={"name": "利率", "parent_id": folder_id},
    )
    assert child_folder.status_code == 201, child_folder.text
    assert child_folder.json()["parent_id"] == folder_id
    folders = client.get(
        f"/api/knowledge/v1/bases/{base_id}/folders", headers=alice
    ).json()["items"]
    assert next(item for item in folders if item["id"] == folder_id)["child_count"] == 1

    upload = client.post(
        f"/api/knowledge/v1/bases/{base_id}/documents",
        headers=alice,
        data={"folder_id": folder_id},
        files={"file": ("report.pdf", b"public report bytes", "application/pdf")},
    )
    assert upload.status_code == 202, upload.text
    document_id = upload.json()["document"]["id"]
    operation_id = upload.json()["operation"]["id"]
    assert upload.json()["document"]["status"] == "processing"
    assert "remote-document" not in upload.text

    documents = client.get(
        f"/api/knowledge/v1/bases/{base_id}/documents", headers=alice
    )
    assert documents.status_code == 200, documents.text
    assert documents.json()["items"][0]["status"] == "ready"

    root_only = client.get(
        f"/api/knowledge/v1/bases/{base_id}/documents?direct_only=true",
        headers=alice,
    )
    assert root_only.status_code == 200
    assert root_only.json()["items"] == []
    folder_only = client.get(
        f"/api/knowledge/v1/bases/{base_id}/documents?direct_only=true&folder_id={folder_id}",
        headers=alice,
    )
    assert [item["id"] for item in folder_only.json()["items"]] == [document_id]

    operations = client.get("/api/knowledge/v1/operations", headers=alice)
    document_operation = next(
        item for item in operations.json()["items"] if item["id"] == operation_id
    )
    assert document_operation["status"] == "succeeded"
    assert document_operation["progress"] == 1.0

    retrieved = client.post(
        f"/api/knowledge/v1/bases/{base_id}/retrieve",
        headers=alice,
        json={"question": "报告说了什么？", "top_n": 5},
    )
    assert retrieved.status_code == 200, retrieved.text
    assert retrieved.json()["evidence"][0]["document_id"] == document_id
    assert "remote-document" not in retrieved.text

    download = client.get(
        f"/api/knowledge/v1/documents/{document_id}/content?disposition=attachment",
        headers=alice,
    )
    assert download.status_code == 200
    assert download.content == b"public report bytes"
    assert "attachment" in download.headers["content-disposition"]
    assert download.headers["content-type"] == "application/pdf"
    assert download.headers["accept-ranges"] == "bytes"

    byte_range = client.get(
        f"/api/knowledge/v1/documents/{document_id}/content",
        headers={**alice, "Range": "bytes=0-5"},
    )
    assert byte_range.status_code == 206
    assert byte_range.content == b"public"
    assert byte_range.headers["content-range"] == "bytes 0-5/19"

    suffix_range = client.get(
        f"/api/knowledge/v1/documents/{document_id}/content",
        headers={**alice, "Range": "bytes=-5"},
    )
    assert suffix_range.status_code == 206
    assert suffix_range.content == b"bytes"

    invalid_range = client.get(
        f"/api/knowledge/v1/documents/{document_id}/content",
        headers={**alice, "Range": "bytes=999-1000"},
    )
    assert invalid_range.status_code == 416
    assert invalid_range.headers["content-range"] == "bytes */19"
    assert invalid_range.headers["content-length"] == "0"
    assert invalid_range.content == b""

    bob_list = client.get("/api/knowledge/v1/bases", headers=bob)
    assert bob_list.status_code == 200
    assert bob_list.json()["items"][0]["role"] == "viewer"
    denied = client.post(
        f"/api/knowledge/v1/bases/{base_id}/folders",
        headers=bob,
        json={"name": "forbidden"},
    )
    assert denied.status_code == 403

    invisible = client.get(f"/api/knowledge/v1/bases/{base_id}", headers=carol)
    assert invisible.status_code == 404

    permissions = client.put(
        f"/api/knowledge/v1/bases/{base_id}/permissions",
        headers=alice,
        json={
            "items": [
                {"subject_type": "user", "subject_id": "user-bob", "role": "maintainer"}
            ]
        },
    )
    assert permissions.status_code == 403, permissions.text
    assert permissions.json()["detail"]["code"] == "KNOWLEDGE_TEAM_ROLE_ADMIN_REQUIRED"

    admin_permissions = client.put(
        f"/api/knowledge/v1/bases/{base_id}/permissions",
        headers=admin,
        json={
            "items": [
                {
                    "subject_type": "user",
                    "subject_id": "user-bob",
                    "role": "maintainer",
                },
                {
                    "subject_type": "user",
                    "subject_id": "user-carol",
                    "role": "manager",
                },
            ]
        },
    )
    assert admin_permissions.status_code == 200, admin_permissions.text
    assert {item["role"] for item in admin_permissions.json()["items"]} == {
        "maintainer",
        "manager",
    }
    upgraded = client.get(f"/api/knowledge/v1/bases/{base_id}", headers=bob)
    assert upgraded.json()["role"] == "maintainer"
    assert "create_folder" in upgraded.json()["allowed_actions"]
    assert "manage_permissions" not in upgraded.json()["allowed_actions"]
    assert client.get(
        f"/api/knowledge/v1/bases/{base_id}/permissions", headers=bob
    ).status_code == 403

    cross_department_manager = client.get(
        f"/api/knowledge/v1/bases/{base_id}", headers=carol
    )
    assert cross_department_manager.status_code == 200
    assert cross_department_manager.json()["role"] == "manager"
    assert "manage_permissions" in cross_department_manager.json()["allowed_actions"]
    # A single-base manager role does not grant global team-space creation.
    assert client.post(
        "/api/knowledge/v1/bases",
        headers=carol,
        json={"name": "不应获得创建权", "visibility": "team"},
    ).status_code == 403

    changed_by_non_admin = client.put(
        f"/api/knowledge/v1/bases/{base_id}/permissions",
        headers=alice,
        json={
            "items": [
                {
                    "subject_type": "user",
                    "subject_id": "user-bob",
                    "role": "manager",
                },
                {
                    "subject_type": "user",
                    "subject_id": "user-carol",
                    "role": "manager",
                },
            ]
        },
    )
    assert changed_by_non_admin.status_code == 403
    assert (
        changed_by_non_admin.json()["detail"]["code"]
        == "KNOWLEDGE_TEAM_ROLE_ADMIN_REQUIRED"
    )

    class FakeModel:
        async def ainvoke(self, messages):
            return SimpleNamespace(content="根据证据，测试结论成立。[知识依据1]")

    import easy_agent.knowledge.api as api_module

    monkeypatch.setattr(api_module, "get_agent_config", lambda: {"config": object()})
    monkeypatch.setattr(api_module, "create_model", lambda config: FakeModel())
    asked = client.post(
        f"/api/knowledge/v1/bases/{base_id}/ask",
        headers=alice,
        json={"question": "报告结论是什么？", "top_n": 5},
    )
    assert asked.status_code == 200, asked.text
    assert asked.json()["answer"].endswith("[知识依据1]")
    assert asked.json()["evidence"][0]["document_id"] == document_id


def test_admin_allowlist_controls_team_space_create_and_management_immediately(
    knowledge_api,
):
    client, db, _, tokens = knowledge_api
    admin = _auth(tokens, "admin")
    alice = _auth(tokens, "alice")
    bob = _auth(tokens, "bob")
    carol = _auth(tokens, "carol")

    non_admin_change = client.put(
        "/api/knowledge/v1/admin/team-space-managers/user-bob",
        headers=bob,
        json={"enabled": True},
    )
    assert non_admin_change.status_code == 403

    revoked = client.put(
        "/api/knowledge/v1/admin/team-space-managers/user-alice",
        headers=admin,
        json={"enabled": False},
    )
    assert revoked.status_code == 200
    denied_create = client.post(
        "/api/knowledge/v1/bases",
        headers=alice,
        json={"name": "不应创建", "visibility": "team"},
    )
    assert denied_create.status_code == 403
    assert denied_create.json()["detail"]["code"] == "KNOWLEDGE_TEAM_SPACE_MANAGER_REQUIRED"
    alice_bases = client.get("/api/knowledge/v1/bases", headers=alice).json()
    assert alice_bases["team_space_management"] == {
        "can_create": False,
        "can_manage": False,
        "department_id": "dept-market",
        "is_admin": False,
        "departments": [],
    }

    # admin is a built-in global manager and never needs to grant itself.
    # Because the bootstrap admin has no department, it chooses an active
    # target department explicitly when creating a team knowledge base.
    admin_bases_before_create = client.get(
        "/api/knowledge/v1/bases", headers=admin
    )
    assert admin_bases_before_create.status_code == 200
    admin_management = admin_bases_before_create.json()["team_space_management"]
    assert admin_management["is_admin"] is True
    assert admin_management["can_create"] is True
    assert admin_management["can_manage"] is True
    assert admin_management["department_id"] is None
    assert {item["id"] for item in admin_management["departments"]} == {
        "dept-market",
        "dept-risk",
    }
    missing_department = client.post(
        "/api/knowledge/v1/bases",
        headers=admin,
        json={"name": "未选部门", "visibility": "team"},
    )
    assert missing_department.status_code == 422
    assert missing_department.json()["detail"]["code"] == "KNOWLEDGE_DEPARTMENT_REQUIRED"
    invalid_department = client.post(
        "/api/knowledge/v1/bases",
        headers=admin,
        json={
            "name": "无效部门",
            "visibility": "team",
            "department_id": "dept-does-not-exist",
        },
    )
    assert invalid_department.status_code == 422
    assert invalid_department.json()["detail"]["code"] == "KNOWLEDGE_DEPARTMENT_INVALID"
    admin_created = client.post(
        "/api/knowledge/v1/bases",
        headers=admin,
        json={
            "name": "admin 直接创建的风险部团队库",
            "visibility": "team",
            "department_id": "dept-risk",
        },
    )
    assert admin_created.status_code == 201, admin_created.text
    assert client.get(
        f"/api/knowledge/v1/bases/{admin_created.json()['id']}", headers=carol
    ).json()["role"] == "viewer"

    granted = client.put(
        "/api/knowledge/v1/admin/team-space-managers/user-alice",
        headers=admin,
        json={"enabled": True},
    )
    assert granted.status_code == 200, granted.text
    assert granted.json()["manager"]["username"] == "alice"
    cross_department_create = client.post(
        "/api/knowledge/v1/bases",
        headers=alice,
        json={
            "name": "越部门创建",
            "visibility": "team",
            "department_id": "dept-risk",
        },
    )
    assert cross_department_create.status_code == 403
    assert (
        cross_department_create.json()["detail"]["code"]
        == "KNOWLEDGE_DEPARTMENT_FORBIDDEN"
    )
    created = client.post(
        "/api/knowledge/v1/bases",
        headers=alice,
        json={"name": "受控团队库", "visibility": "team"},
    )
    assert created.status_code == 201, created.text
    base_id = created.json()["id"]

    bob_view = client.get(f"/api/knowledge/v1/bases/{base_id}", headers=bob)
    assert bob_view.status_code == 200
    assert bob_view.json()["role"] == "viewer"
    assert "edit_base" not in bob_view.json()["allowed_actions"]
    assert client.patch(
        f"/api/knowledge/v1/bases/{base_id}",
        headers=bob,
        json={"description": "越权修改"},
    ).status_code == 403

    assert client.put(
        "/api/knowledge/v1/admin/team-space-managers/user-bob",
        headers=admin,
        json={"enabled": True},
    ).status_code == 200
    bob_manager = client.get(f"/api/knowledge/v1/bases/{base_id}", headers=bob)
    assert bob_manager.json()["role"] == "manager"
    assert client.patch(
        f"/api/knowledge/v1/bases/{base_id}",
        headers=bob,
        json={"description": "合规修改"},
    ).status_code == 200

    # A manager remains bounded to the department recorded by the server.
    assert client.put(
        "/api/knowledge/v1/admin/team-space-managers/user-carol",
        headers=admin,
        json={"enabled": True},
    ).status_code == 200
    assert client.get(f"/api/knowledge/v1/bases/{base_id}", headers=carol).status_code == 404

    # Revocation is checked from the database on every request; no new token is
    # needed for the role downgrade to take effect.
    assert client.put(
        "/api/knowledge/v1/admin/team-space-managers/user-bob",
        headers=admin,
        json={"enabled": False},
    ).status_code == 200
    bob_after_revoke = client.get(
        f"/api/knowledge/v1/bases/{base_id}", headers=bob
    ).json()
    assert bob_after_revoke["role"] == "viewer"
    assert client.patch(
        f"/api/knowledge/v1/bases/{base_id}",
        headers=bob,
        json={"description": "撤销后越权"},
    ).status_code == 403

    admin_bases = client.get("/api/knowledge/v1/bases", headers=admin).json()
    admin_team = next(item for item in admin_bases["items"] if item["id"] == base_id)
    assert admin_team["role"] == "manager"
    assert "manage_permissions" in admin_team["allowed_actions"]
    manager_list = client.get(
        "/api/knowledge/v1/admin/team-space-managers", headers=admin
    )
    assert manager_list.status_code == 200
    assert {item["username"] for item in manager_list.json()["items"]} == {
        "alice",
        "carol",
    }

    with db.get_connection() as connection:
        cursor = connection.cursor()
        db._execute(
            cursor,
            "SELECT action FROM knowledge_audit_events "
            "WHERE object_type='team_space_manager' ORDER BY created_at",
        )
        actions = {
            (dict(row) if not isinstance(row, dict) else row)["action"]
            for row in cursor.fetchall()
        }
    assert "team_space_manager.grant" in actions
    assert "team_space_manager.revoke" in actions


def test_team_manager_grant_rejects_disabled_or_departmentless_accounts(
    knowledge_api,
):
    client, db, _, tokens = knowledge_api
    admin = _auth(tokens, "admin")
    db.create_user(
        UserModel(
            "user-disabled-manager",
            "disabled_manager",
            "",
            "dept-market",
            account_status="disabled",
        )
    )
    db.create_user(UserModel("user-no-department", "no_department", ""))

    disabled = client.put(
        "/api/knowledge/v1/admin/team-space-managers/user-disabled-manager",
        headers=admin,
        json={"enabled": True},
    )
    missing_department = client.put(
        "/api/knowledge/v1/admin/team-space-managers/user-no-department",
        headers=admin,
        json={"enabled": True},
    )

    assert disabled.status_code == 409
    assert "停用" in disabled.json()["detail"]
    assert missing_department.status_code == 409
    assert "缺少部门" in missing_department.json()["detail"]


def test_upload_persists_original_and_download_does_not_depend_on_ragflow(
    knowledge_api,
):
    client, db, fake, tokens = knowledge_api
    alice = _auth(tokens, "alice")
    base_id = client.post(
        "/api/knowledge/v1/bases",
        headers=alice,
        json={"name": "原文权威源", "visibility": "personal"},
    ).json()["id"]
    payload = b"authoritative original"
    uploaded = client.post(
        f"/api/knowledge/v1/bases/{base_id}/documents",
        headers=alice,
        files={"file": ("original.txt", payload, "text/plain")},
    )
    assert uploaded.status_code == 202, uploaded.text
    document_id = uploaded.json()["document"]["id"]
    stored = KnowledgeRepository(db).get_original_object(document_id)
    assert stored is not None
    assert stored["status"] == "available"
    assert len(stored["sha256"]) == 64

    dataset_id = next(reversed(fake.datasets))
    remote = next(iter(fake.documents[dataset_id].values()))
    remote["content"] = b"tampered ragflow copy"
    downloaded = client.get(
        f"/api/knowledge/v1/documents/{document_id}/content",
        headers={**alice, "Range": "bytes=0-12"},
    )
    assert downloaded.status_code == 206
    assert downloaded.content == payload[:13]
    assert downloaded.headers["etag"] == f'"sha256-{stored["sha256"]}"'


def test_original_storage_failure_prevents_ragflow_upload(knowledge_api):
    client, _, fake, tokens = knowledge_api
    alice = _auth(tokens, "alice")
    base_id = client.post(
        "/api/knowledge/v1/bases",
        headers=alice,
        json={"name": "原文写入失败", "visibility": "personal"},
    ).json()["id"]

    class FailingStore:
        provider = "filesystem"

        def build_key(self, base_id, document_id, filename):
            return f"v1/{base_id}/{document_id}/original.txt"

        def put_atomic(self, storage_key, source, expected_size):
            raise OriginalStorageError("ORIGINAL_STORAGE_WRITE_FAILED", "failed")

    previous = app.state.original_store
    app.state.original_store = FailingStore()
    try:
        response = client.post(
            f"/api/knowledge/v1/bases/{base_id}/documents",
            headers=alice,
            files={"file": ("failed.txt", b"never indexed", "text/plain")},
        )
    finally:
        app.state.original_store = previous
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "ORIGINAL_STORAGE_WRITE_FAILED"
    dataset_id = next(reversed(fake.datasets))
    assert fake.documents[dataset_id] == {}


def test_retry_reads_original_store_without_downloading_from_ragflow(
    knowledge_api, monkeypatch
):
    client, db, fake, tokens = knowledge_api
    alice = _auth(tokens, "alice")
    base_id = client.post(
        "/api/knowledge/v1/bases",
        headers=alice,
        json={"name": "从原文重试", "visibility": "personal"},
    ).json()["id"]
    payload = b"retry from original storage"
    document_id = client.post(
        f"/api/knowledge/v1/bases/{base_id}/documents",
        headers=alice,
        files={"file": ("retry.txt", payload, "text/plain")},
    ).json()["document"]["id"]
    KnowledgeRepository(db).update_document(document_id, status="failed")

    async def forbidden_download(**kwargs):
        raise AssertionError("retry must not download the original from RAGFlow")

    monkeypatch.setattr(fake, "download_document", forbidden_download)
    retried = client.post(
        f"/api/knowledge/v1/documents/{document_id}/retry", headers=alice
    )
    assert retried.status_code == 202, retried.text
    dataset_id = next(reversed(fake.datasets))
    remote = next(iter(fake.documents[dataset_id].values()))
    assert remote["content"] == payload


def test_shared_department_permission_and_session_ownership(knowledge_api):
    client, db, _, tokens = knowledge_api
    alice = _auth(tokens, "alice")
    carol = _auth(tokens, "carol")

    created = client.post(
        "/api/knowledge/v1/bases",
        headers=alice,
        json={"name": "共享库", "visibility": "shared"},
    )
    base_id = created.json()["id"]
    assert client.get(f"/api/knowledge/v1/bases/{base_id}", headers=carol).status_code == 404

    granted = client.put(
        f"/api/knowledge/v1/bases/{base_id}/permissions",
        headers=alice,
        json={
            "items": [
                {"subject_type": "department", "subject_id": "dept-risk", "role": "viewer"}
            ]
        },
    )
    assert granted.status_code == 200
    assert client.get(f"/api/knowledge/v1/bases/{base_id}", headers=carol).status_code == 200

    db.create_session(
        SessionModel(session_id="alice-session", title="test", username="alice")
    )
    saved = client.put(
        "/api/knowledge/v1/sessions/alice-session/knowledge-scope",
        headers=alice,
        json={"base_ids": [base_id]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["base_ids"] == [base_id]

    stolen = client.get(
        "/api/knowledge/v1/sessions/alice-session/knowledge-scope", headers=carol
    )
    assert stolen.status_code == 404

    client.put(
        f"/api/knowledge/v1/bases/{base_id}/permissions",
        headers=alice,
        json={"items": []},
    )
    assert client.get(f"/api/knowledge/v1/bases/{base_id}", headers=carol).status_code == 404


def test_selected_scope_is_revalidated_and_injected_into_chat(
    knowledge_api, monkeypatch
):
    client, db, _, tokens = knowledge_api
    alice = _auth(tokens, "alice")

    created = client.post(
        "/api/knowledge/v1/bases",
        headers=alice,
        json={"name": "聊天研报库", "visibility": "personal"},
    )
    base_id = created.json()["id"]
    catalog_only = client.post(
        f"/api/knowledge/v1/bases/{base_id}/documents",
        headers=alice,
        files={"file": ("catalog-only.txt", b"other", "text/plain")},
    )
    assert catalog_only.status_code == 202
    uploaded = client.post(
        f"/api/knowledge/v1/bases/{base_id}/documents",
        headers=alice,
        files={"file": ("chat-report.pdf", b"report", "application/pdf")},
    )
    assert uploaded.status_code == 202
    unselected = client.post(
        "/api/knowledge/v1/bases",
        headers=alice,
        json={"name": "未选择的知识库", "visibility": "personal"},
    )
    assert unselected.status_code == 201
    secret_upload = client.post(
        f"/api/knowledge/v1/bases/{unselected.json()['id']}/documents",
        headers=alice,
        files={"file": ("unselected-secret.txt", b"secret", "text/plain")},
    )
    assert secret_upload.status_code == 202
    refreshed = client.get(
        f"/api/knowledge/v1/bases/{base_id}/documents", headers=alice
    )
    assert refreshed.json()["items"][0]["status"] == "ready"

    db.create_session(
        SessionModel(session_id="knowledge-chat", title="test", username="alice")
    )
    saved = client.put(
        "/api/knowledge/v1/sessions/knowledge-chat/knowledge-scope",
        headers=alice,
        json={"base_ids": [base_id]},
    )
    assert saved.status_code == 200

    denied = client.post(
        "/api/chat/stream",
        headers={"X-Username": "alice"},
        json={"message": "研报的结论是什么？", "session_id": "knowledge-chat"},
    )
    assert denied.status_code == 401

    captured = {}

    async def fake_get_agent(*args, **kwargs):
        return None

    async def fake_stream_generator(**kwargs):
        captured["context"] = kwargs["context_prefix"]
        metadata = kwargs["assistant_metadata"]
        captured["evidence"] = metadata["knowledge_evidence"]
        captured["warnings"] = metadata["knowledge_warnings"]
        yield 'data: {"type":"done","session_id":"knowledge-chat"}\n\n'

    import easy_agent.api.chat as chat_module

    monkeypatch.setattr(chat_module, "get_or_create_agent_for_session", fake_get_agent)
    monkeypatch.setattr(chat_module, "chat_stream_generator", fake_stream_generator)

    response = client.post(
        "/api/chat/stream",
        headers=alice,
        json={"message": "研报的结论是什么？", "session_id": "knowledge-chat"},
    )
    assert response.status_code == 200, response.text
    assert "[知识依据1]" in captured["context"]
    assert "## 当前授权知识库文档目录" in captured["context"]
    assert "chat-report.pdf; 状态=已解析，可检索" in captured["context"]
    assert "catalog-only.txt; 状态=已解析，可检索" in captured["context"]
    assert "目录是完整资料清单" in captured["context"]
    assert "unselected-secret.txt" not in captured["context"]
    assert captured["evidence"][0]["document_name"] == "chat-report.pdf"
    assert captured["evidence"][0]["metadata"]["base_name"] == "聊天研报库"
    assert "remote-document" not in str(captured)


def test_knowledge_workbench_stream_reuses_easyagent_hidden_session(
    knowledge_api, monkeypatch
):
    client, db, _, tokens = knowledge_api
    alice = _auth(tokens, "alice")
    created = client.post(
        "/api/knowledge/v1/bases",
        headers=alice,
        json={"name": "Agent 研报库", "visibility": "personal"},
    )
    base_id = created.json()["id"]
    uploaded = client.post(
        f"/api/knowledge/v1/bases/{base_id}/documents", headers=alice,
        files={"file": ("panel.txt", b"panel evidence", "text/plain")},
    )
    assert uploaded.status_code == 202
    assert client.get(
        f"/api/knowledge/v1/bases/{base_id}/documents", headers=alice
    ).json()["items"][0]["status"] == "ready"
    raw_reasoning = "INTERNAL_MEMORY_AND_POLICY_SENTINEL"

    async def fake_get_agent(*args, **kwargs):
        raise AssertionError("知识面板不得创建含内置工具的 DeepAgent")

    import easy_agent.api.chat as chat_module

    prepared = client.post(
        f"/api/knowledge/v1/bases/{base_id}/chat-session",
        headers=alice,
    )
    assert prepared.status_code == 200, prepared.text
    hidden_session_id = prepared.json()["session_id"]
    hidden_session = db.get_session(hidden_session_id)
    assert hidden_session is not None
    assert hidden_session.title.startswith("[知识库问答]")
    assert KnowledgeRepository(db).get_session_scope(
        session_id=hidden_session_id,
        user_id="user-alice",
    ) == [base_id]
    assert all(
        item["session_id"] != hidden_session_id
        for item in client.get("/api/sessions", headers=alice).json()
    )

    from langchain_core.messages import AIMessageChunk

    class NoToolModel:
        async def astream(self, messages):
            assert "[知识依据" in messages[-1].content or "未检索到" in messages[-1].content
            yield AIMessageChunk(
                content="",
                additional_kwargs={"reasoning_content": raw_reasoning},
            )
            yield AIMessageChunk(content="流式回答")

    import easy_agent.knowledge.streaming as streaming_module
    monkeypatch.setattr(
        chat_module, "get_or_create_agent_for_session", fake_get_agent
    )
    monkeypatch.setattr(streaming_module, "create_model", lambda *args, **kwargs: NoToolModel())

    # 右侧面板的真正问答直接走与新对话相同的通用接口。
    streamed = client.post(
        "/api/chat/stream",
        headers=alice,
        json={
            "message": "核心结论是什么？",
            "session_id": hidden_session_id,
            "enable_deep_think": True,
        },
    )
    assert streamed.status_code == 200, streamed.text
    assert "正在检索并整理知识库依据" in streamed.text
    assert "流式回答" in streamed.text
    assert raw_reasoning not in streamed.text
    assert '"type": "tool_call"' not in streamed.text
    stored = db.get_session(hidden_session_id).messages[-1]
    assert stored["thinking"] is None
    assert raw_reasoning not in str(stored)
    assert not stored.get("tool_calls")
    assert db.get_session(hidden_session_id).title.startswith("[知识库问答]")
