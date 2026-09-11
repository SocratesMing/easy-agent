"""BFF DTO and capabilities endpoint contract."""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from pydantic import ValidationError

from easy_agent.app import app
from easy_agent.knowledge.config import KnowledgeConfig
from easy_agent.knowledge.models import (
    Evidence,
    KnowledgeErrorResponse,
    RetrieveRequest,
    UploadBatchAccepted,
)


TEMPLATE = Path(__file__).parents[1] / "fixtures" / "knowledge-config.yaml"


def _enabled_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeConfig:
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    data["enabled"] = True
    path = tmp_path / "enabled-knowledge.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("RAGFLOW_BASE_URL", "https://internal.example.test")
    monkeypatch.setenv("RAGFLOW_API_KEY", "must-never-be-returned")
    monkeypatch.setenv("RAGFLOW_EMBEDDING_MODEL", "embedding-model")
    return KnowledgeConfig.from_yaml(path)


def test_capabilities_is_always_available_when_module_disabled(client: TestClient):
    response = client.get("/api/knowledge/v1/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "schema_version": 1,
        "api_version": "v1",
        "enabled": False,
        "status": "disabled",
        "upstream_capabilities": [],
        "features": {},
    }


def test_enabled_capabilities_never_exposes_endpoint_or_secret(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    app.state.knowledge_config = _enabled_config(tmp_path, monkeypatch)

    response = client.get("/api/knowledge/v1/capabilities")

    assert response.status_code == 200
    body = response.json()
    serialized = response.text
    assert body["enabled"] is True
    assert body["status"] == "configured"
    assert "retrieval" in body["upstream_capabilities"]
    assert "internal.example.test" not in serialized
    assert "must-never-be-returned" not in serialized


def test_capabilities_is_present_in_openapi_contract():
    schema = app.openapi()
    operation = schema["paths"]["/api/knowledge/v1/capabilities"]["get"]

    assert operation["tags"] == ["knowledge-engineering"]
    assert "200" in operation["responses"]


def test_contract_models_forbid_supplier_fields():
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Evidence(
            document_id="local-doc",
            document_name="report.pdf",
            snippet="evidence",
            score=0.9,
            ragflow_document_id="remote-doc",
        )


def test_upload_batch_contract_has_one_batch_and_per_file_operations():
    payload = UploadBatchAccepted.model_validate(
        {
            "batch_operation_id": "batch-1",
            "items": [
                {
                    "client_file_id": "client-1",
                    "document_id": "local-doc-1",
                    "operation_id": "operation-1",
                    "status": "accepted",
                }
            ],
            "poll_after_seconds": 2,
        }
    )

    assert payload.batch_operation_id == "batch-1"
    assert payload.items[0].operation_id == "operation-1"


def test_error_contract_requires_request_id():
    with pytest.raises(ValidationError):
        KnowledgeErrorResponse(
            code="KNOWLEDGE_UPSTREAM_UNAVAILABLE",
            message="temporarily unavailable",
            retryable=True,
        )


def test_explicit_empty_document_filter_is_rejected():
    with pytest.raises(ValidationError, match="omitted or non-empty"):
        RetrieveRequest(question="What changed?", document_ids=[])
