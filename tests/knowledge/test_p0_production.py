from __future__ import annotations

import io
import json
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

from easy_agent.knowledge.config import KnowledgeConfig
from easy_agent.knowledge.auth import KnowledgePrincipal
from easy_agent.knowledge.file_validation import UploadValidationError, validate_upload
from easy_agent.knowledge.observability import resolve_request_id
from easy_agent.knowledge.operations_repository import KnowledgeOperationsRepository
from easy_agent.knowledge.quality import validate_answer_citations
from easy_agent.knowledge.repository import KnowledgeRepository
from easy_agent.knowledge.schema import validate_knowledge_schema
from easy_agent.knowledge.models import Evidence
from easy_agent.knowledge.service import KnowledgeService, KnowledgeServiceError
from easy_agent.knowledge.storage import create_original_store
from easy_agent.knowledge.worker import KnowledgeTaskWorker


TEMPLATE = Path(__file__).parents[1] / "fixtures" / "knowledge-config.yaml"


def enabled_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeConfig:
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    data["enabled"] = True
    data["operations"]["worker"]["mode"] = "queue"
    data["original_storage"]["enabled"] = True
    data["original_storage"]["filesystem"]["root_path"] = str(tmp_path / "nas")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("RAGFLOW_BASE_URL", "https://ragflow.example.test")
    monkeypatch.setenv("RAGFLOW_API_KEY", "test-secret")
    monkeypatch.setenv("RAGFLOW_EMBEDDING_MODEL", "test-embedding")
    return KnowledgeConfig.from_yaml(path)


class WorkerRagflow:
    def __init__(self):
        self.documents: dict[str, dict] = {}
        self.deleted_datasets: list[str] = []

    async def health(self, **kwargs):
        return True

    async def upload_document(self, *, filename, content, **kwargs):
        assert content.read() == b"worker payload"
        item = {"id": "remote-doc-1", "name": filename, "run": "0", "progress": 0.0}
        self.documents[item["id"]] = item
        return item

    async def configure_document(self, **kwargs):
        return None

    async def start_parsing(self, *, document_ids, **kwargs):
        for item in document_ids:
            self.documents[item].update(run="3", progress=1.0)

    async def delete_documents(self, *, document_ids, **kwargs):
        for item in document_ids:
            self.documents.pop(item, None)

    async def delete_datasets(self, dataset_ids, **kwargs):
        self.deleted_datasets.extend(dataset_ids)

    async def list_documents(self, **kwargs):
        return {"docs": list(self.documents.values()), "total": len(self.documents)}


def test_numbered_schema_and_durable_task_idempotency(db):
    repository = KnowledgeRepository(db)
    operations = KnowledgeOperationsRepository(db)
    base = repository.create_base(
        name="test", description="", space_type="personal", owner_user_id="owner",
        department_id=None, embedding_model="embedding",
    )
    document = repository.create_document(
        base_id=base["id"], folder_id=None, name="a.txt", content_type="text/plain",
        size_bytes=1, created_by="owner",
    )
    operation = repository.create_operation(
        operation_type="document_upload", resource_type="document",
        resource_id=document["id"], phase="queued", status="accepted", created_by="owner",
    )
    first = operations.create_task(
        operation_id=operation["id"], task_type="document_upload", resource_type="document",
        resource_id=document["id"], idempotency_key="same-request", payload={},
        request_id="request-1", created_by="owner", max_attempts=3,
    )
    second = operations.create_task(
        operation_id=operation["id"], task_type="document_upload", resource_type="document",
        resource_id=document["id"], idempotency_key="same-request", payload={},
        request_id="request-1", created_by="owner", max_attempts=3,
    )
    assert first["id"] == second["id"]
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT version FROM knowledge_schema_migrations ORDER BY version")
        assert [row[0] if not isinstance(row, dict) else row["version"] for row in cursor.fetchall()] == [1, 2, 3, 4]


def test_deleted_resources_do_not_keep_actionable_dead_letter_alerts(db):
    repository = KnowledgeRepository(db)
    operations = KnowledgeOperationsRepository(db)
    base = repository.create_base(
        name="test", description="", space_type="personal", owner_user_id="owner",
        department_id=None, embedding_model="embedding", status="active",
    )
    document = repository.create_document(
        base_id=base["id"], folder_id=None, name="failed.txt", content_type="text/plain",
        size_bytes=1, created_by="owner",
    )
    operation = repository.create_operation(
        operation_type="document_upload", resource_type="document",
        resource_id=document["id"], phase="failed", status="dead_letter",
        created_by="owner",
    )
    task = operations.create_task(
        operation_id=operation["id"], task_type="document_upload",
        resource_type="document", resource_id=document["id"],
        idempotency_key="deleted-resource-dead-letter", payload={},
        request_id="failed-request", created_by="owner", max_attempts=1,
    )
    operations.fail_task(
        task["id"], error_code="RAGFLOW_ERROR", error_message="failed",
        retryable=False, retry_backoff_seconds=1,
    )

    assert operations.actionable_dead_letter_count() == 1
    repository.update_base(base["id"], status="deleted")
    assert operations.actionable_dead_letter_count() == 0
    assert operations.task_counts()["dead_letter"] == 1


def test_schema_validation_rejects_a_changed_migration_checksum(db):
    assert [item["version"] for item in validate_knowledge_schema(db)] == [1, 2, 3, 4]
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE knowledge_schema_migrations SET checksum=? WHERE version=?",
            ("0" * 64, 2),
        )

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        validate_knowledge_schema(db)


def test_audit_redacts_secrets_and_original_content(db):
    operations = KnowledgeOperationsRepository(db)
    operations.record_audit(
        request_id="request-safe", actor_user_id="u1", actor_username="admin",
        action="personnel.import", object_type="personnel", object_id="batch",
        details={"token": "leak", "password": "leak", "content": "sensitive report", "count": 2},
    )
    details = json.loads(operations.list_audits(limit=1)[0]["details_json"])
    assert details["token"] == details["password"] == details["content"] == "[redacted]"
    assert details["count"] == 2


def test_upload_magic_and_macro_validation():
    from easy_agent.knowledge.config import UploadSecurityConfig
    config = UploadSecurityConfig()
    with pytest.raises(UploadValidationError) as invalid:
        validate_upload(io.BytesIO(b"not-pdf"), filename="report.pdf",
                        content_type="application/pdf", size_bytes=7, config=config)
    assert invalid.value.code == "KNOWLEDGE_FILE_SIGNATURE_MISMATCH"

    stream = io.BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("word/document.xml", "<doc/>")
        archive.writestr("word/vbaProject.bin", b"macro")
    with pytest.raises(UploadValidationError) as macro:
        validate_upload(stream, filename="report.docx",
                        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        size_bytes=len(stream.getvalue()), config=config)
    assert macro.value.code == "KNOWLEDGE_OFFICE_MACRO_REJECTED"


def test_quality_gate_rejects_missing_or_fake_citations():
    evidence = [Evidence(document_id="d1", document_name="one.pdf", snippet="fact", score=0.9)]
    answer, warnings = validate_answer_citations("model guess", evidence, no_answer_text="NO ANSWER")
    assert answer == "NO ANSWER" and warnings
    answer, warnings = validate_answer_citations("fact [知识依据2]", evidence, no_answer_text="NO ANSWER")
    assert answer == "NO ANSWER" and warnings == ["KNOWLEDGE_ANSWER_INVALID_CITATION"]
    answer, warnings = validate_answer_citations("fact [知识依据1]", evidence, no_answer_text="NO ANSWER")
    assert answer.endswith("[知识依据1]") and not warnings


@pytest.mark.asyncio
async def test_worker_resumes_persisted_upload(db, tmp_path, monkeypatch):
    config = enabled_config(tmp_path, monkeypatch)
    repository = KnowledgeRepository(db)
    operations = KnowledgeOperationsRepository(db)
    store = create_original_store(config)
    base = repository.create_base(
        name="test", description="", space_type="personal", owner_user_id="owner",
        department_id=None, embedding_model="embedding",
    )
    repository.update_base(base["id"], remote_dataset_id="remote-base", status="active")
    document = repository.create_document(
        base_id=base["id"], folder_id=None, name="a.txt", content_type="text/plain",
        size_bytes=14, created_by="owner",
    )
    key = store.build_key(base["id"], document["id"], document["name"])
    repository.create_original_object(
        document_id=document["id"], provider=store.provider, storage_key=key,
        original_name=document["name"], content_type="text/plain", size_bytes=14,
        created_by="owner",
    )
    stored = store.put_atomic(key, io.BytesIO(b"worker payload"), 14)
    repository.update_original_object(document["id"], status="available", sha256=stored.sha256)
    operation = repository.create_operation(
        operation_type="document_upload", resource_type="document",
        resource_id=document["id"], phase="queued", status="accepted", created_by="owner",
    )
    task = operations.create_task(
        operation_id=operation["id"], task_type="document_upload", resource_type="document",
        resource_id=document["id"], idempotency_key="persisted-upload", payload={},
        request_id="request-worker", created_by="owner", max_attempts=3,
    )
    worker = KnowledgeTaskWorker(db, config, WorkerRagflow(), store, worker_id="test-worker")
    assert await worker.process_one() is True
    assert operations.get_task(task["id"])["status"] == "succeeded"
    updated = repository.get_document(document["id"])
    assert updated["remote_document_id"] == "remote-doc-1"
    assert updated["status"] == "processing"


@pytest.mark.asyncio
async def test_successful_retry_supersedes_obsolete_document_dead_letter(
    db, tmp_path, monkeypatch
):
    config = enabled_config(tmp_path, monkeypatch)
    repository = KnowledgeRepository(db)
    operations = KnowledgeOperationsRepository(db)
    store = create_original_store(config)
    ragflow = WorkerRagflow()
    base = repository.create_base(
        name="test", description="", space_type="personal", owner_user_id="owner",
        department_id=None, embedding_model="embedding",
    )
    repository.update_base(base["id"], remote_dataset_id="remote-base", status="active")
    document = repository.create_document(
        base_id=base["id"], folder_id=None, name="a.txt", content_type="text/plain",
        size_bytes=14, created_by="owner",
    )
    key = store.build_key(base["id"], document["id"], document["name"])
    repository.create_original_object(
        document_id=document["id"], provider=store.provider, storage_key=key,
        original_name=document["name"], content_type="text/plain", size_bytes=14,
        created_by="owner",
    )
    stored = store.put_atomic(key, io.BytesIO(b"worker payload"), 14)
    repository.update_original_object(document["id"], status="available", sha256=stored.sha256)

    failed_operation = repository.create_operation(
        operation_type="document_upload", resource_type="document",
        resource_id=document["id"], phase="failed", status="dead_letter",
        created_by="owner",
    )
    failed_task = operations.create_task(
        operation_id=failed_operation["id"], task_type="document_upload",
        resource_type="document", resource_id=document["id"],
        idempotency_key="failed-upload", payload={}, request_id="failed-request",
        created_by="owner", max_attempts=1,
    )
    operations.fail_task(
        failed_task["id"], error_code="RAGFLOW_ERROR",
        error_message="configuration failed", retryable=False,
        retry_backoff_seconds=1,
    )
    retry_operation = repository.create_operation(
        operation_type="document_retry", resource_type="document",
        resource_id=document["id"], phase="queued", status="accepted",
        created_by="owner",
    )
    retry_task = operations.create_task(
        operation_id=retry_operation["id"], task_type="document_retry",
        resource_type="document", resource_id=document["id"],
        idempotency_key="replacement-upload", payload={}, request_id="retry-request",
        created_by="owner", max_attempts=3,
    )

    worker = KnowledgeTaskWorker(db, config, ragflow, store, worker_id="retry-worker")
    assert await worker.process_one() is True

    assert operations.get_task(failed_task["id"])["status"] == "cancelled"
    assert operations.get_task(retry_task["id"])["status"] == "succeeded"
    assert operations.task_counts()["dead_letter"] == 0
    assert repository.get_operation(failed_operation["id"])["status"] == "dead_letter"


@pytest.mark.asyncio
async def test_base_delete_is_queued_hidden_and_restorable(db, tmp_path, monkeypatch):
    config = enabled_config(tmp_path, monkeypatch)
    repository = KnowledgeRepository(db)
    ragflow = WorkerRagflow()
    store = create_original_store(config)
    service = KnowledgeService(repository, ragflow, config, original_store=store)
    principal = KnowledgePrincipal(
        user_id="owner-id", username="owner", department_id=None
    )
    base = repository.create_base(
        name="restorable", description="", space_type="personal",
        owner_user_id=principal.user_id, department_id=None,
        embedding_model="embedding",
    )
    repository.update_base(
        base["id"], remote_dataset_id="remote-base", status="active"
    )

    await service.delete_base(
        base_id=base["id"], principal=principal, request_id="delete-base-request"
    )

    assert repository.get_base(base["id"])["status"] == "deleted"
    assert repository.list_candidate_bases(
        user_id=principal.user_id, department_id=None
    ) == []
    with pytest.raises(KnowledgeServiceError) as hidden:
        await service.get_base(base["id"], principal)
    assert hidden.value.status_code == 404

    worker = KnowledgeTaskWorker(
        db, config, ragflow, store, worker_id="base-delete-worker"
    )
    assert await worker.process_one() is True
    restored = await service.restore_base(
        base_id=base["id"], principal=principal, request_id="restore-base-request"
    )
    assert restored.status.value == "active"
    assert repository.get_base(base["id"])["purge_after"] is None


@pytest.mark.asyncio
async def test_expired_base_purge_cleans_remote_nas_and_metadata(
    db, tmp_path, monkeypatch
):
    config = enabled_config(tmp_path, monkeypatch)
    repository = KnowledgeRepository(db)
    ragflow = WorkerRagflow()
    store = create_original_store(config)
    base = repository.create_base(
        name="expired", description="", space_type="personal",
        owner_user_id="owner-id", department_id=None, embedding_model="embedding",
    )
    repository.update_base(
        base["id"], remote_dataset_id="expired-remote", status="deleted",
        deleted_at="2026-01-01T00:00:00+00:00",
        deleted_by="owner-id", purge_after="2026-01-02T00:00:00+00:00",
    )
    document = repository.create_document(
        base_id=base["id"], folder_id=None, name="source.txt",
        content_type="text/plain", size_bytes=7, created_by="owner-id",
    )
    key = store.build_key(base["id"], document["id"], document["name"])
    repository.create_original_object(
        document_id=document["id"], provider=store.provider, storage_key=key,
        original_name=document["name"], content_type="text/plain", size_bytes=7,
        created_by="owner-id",
    )
    stored = store.put_atomic(key, io.BytesIO(b"source\n"), 7)
    stored_path = store.open(key).path
    repository.update_original_object(
        document["id"], status="available", sha256=stored.sha256
    )

    worker = KnowledgeTaskWorker(
        db, config, ragflow, store, worker_id="base-purge-worker"
    )
    await worker._purge_expired_bases()

    assert repository.get_base(base["id"]) is None
    assert ragflow.deleted_datasets == ["expired-remote"]
    assert not stored_path.exists()


def test_request_id_accepts_safe_value_and_replaces_injection():
    assert resolve_request_id("bank-request.123") == "bank-request.123"
    assert resolve_request_id("bad\nheader") != "bad\nheader"
