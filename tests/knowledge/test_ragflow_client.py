"""Contract tests for the configuration-driven RAGFlow HTTP client."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import yaml

from easy_agent.knowledge.ragflow import (
    RagflowAuthenticationError,
    RagflowClient,
    RagflowContractError,
    RagflowUnavailableError,
)
from easy_agent.knowledge.config import KnowledgeConfig


TEMPLATE = Path(__file__).parents[1] / "fixtures" / "knowledge-config.yaml"


def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> KnowledgeConfig:
    data = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))
    data["enabled"] = True
    data["transport"]["retry"]["attempts"] = 2
    path = tmp_path / "knowledge.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    monkeypatch.setenv("RAGFLOW_BASE_URL", "https://ragflow.example.test")
    monkeypatch.setenv("RAGFLOW_API_KEY", "secret-token")
    monkeypatch.setenv("RAGFLOW_EMBEDDING_MODEL", "embedding-test")
    return KnowledgeConfig.from_yaml(path)


@pytest.mark.asyncio
async def test_create_dataset_uses_configured_route_auth_and_private_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers["Authorization"]
        captured["sys_code"] = request.headers["sys-code"]
        captured["request_id"] = request.headers["X-Request-Id"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 0, "data": {"id": "dataset-1"}})

    client = RagflowClient(
        _config(tmp_path, monkeypatch), transport=httpx.MockTransport(handler)
    )
    try:
        result = await client.create_dataset(
            name="测试知识库", description="test", request_id="request-1"
        )
    finally:
        await client.close()

    assert result["id"] == "dataset-1"
    assert captured["url"] == "https://ragflow.example.test/api/v1/datasets"
    assert captured["authorization"] == "Bearer secret-token"
    assert captured["sys_code"] == "EASYAGENT"
    assert captured["request_id"] == "request-1"
    assert captured["body"]["permission"] == "me"
    assert captured["body"]["embedding_model"] == "embedding-test"
    assert captured["body"]["parser_config"]["chunk_token_num"] == 512
    assert captured["body"]["parser_config"]["layout_recognize"] is True
    assert captured["body"]["parser_config"]["method"] == "minerullm"


@pytest.mark.asyncio
async def test_create_dataset_can_use_ragflow_tenant_default_embedding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"code": 0, "data": {"id": "dataset-1"}})

    monkeypatch.setenv("RAGFLOW_LOCAL_COMPAT_ENABLED", "true")
    monkeypatch.setenv("RAGFLOW_LOCAL_BASE_URL", "https://ragflow.example.test")
    client = RagflowClient(
        _config(tmp_path, monkeypatch), transport=httpx.MockTransport(handler)
    )
    try:
        await client.create_dataset(name="custom-provider")
    finally:
        await client.close()

    assert "embedding_model" not in captured
    assert captured["permission"] == "me"
    parser_config = captured["parser_config"]
    assert parser_config["layout_recognize"] == "Plain Text"
    assert "method" not in parser_config
    assert "parent_retrieval" not in parser_config


@pytest.mark.asyncio
async def test_bank_dataset_list_accepts_data_datasets_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client = RagflowClient(
        _config(tmp_path, monkeypatch),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {"datasets": [{"id": "dataset-1"}], "total": 1},
                },
            )
        ),
    )
    try:
        result = await client.list_datasets()
    finally:
        await client.close()

    assert result == [{"id": "dataset-1"}]


@pytest.mark.asyncio
async def test_http_200_code_zero_data_false_auth_is_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"code": 0, "data": False, "message": "Authentication failed"},
        )
    )
    client = RagflowClient(_config(tmp_path, monkeypatch), transport=transport)
    try:
        with pytest.raises(RagflowAuthenticationError):
            await client.list_datasets()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_download_detects_json_auth_failure_instead_of_returning_fake_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"code": 109, "data": False, "message": "invalid token"},
        )
    )
    client = RagflowClient(_config(tmp_path, monkeypatch), transport=transport)
    try:
        with pytest.raises(RagflowAuthenticationError):
            await client.download_document(
                dataset_id="dataset-1", document_id="document-1"
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_list_documents_accepts_official_docs_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "ragflow_v0_17_2"
            / "document_statuses.json"
        ).read_text(encoding="utf-8")
    )
    client = RagflowClient(
        _config(tmp_path, monkeypatch),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=fixture)
        ),
    )
    try:
        result = await client.list_documents(dataset_id="dataset-1")
    finally:
        await client.close()

    assert result["total"] == 5
    assert result["docs"][3]["run"] == "3"


@pytest.mark.asyncio
async def test_retrieve_sends_both_non_empty_dataset_and_document_filters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "ragflow_v0_17_2"
            / "retrieval_success.json"
        ).read_text(encoding="utf-8")
    )
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json=fixture)

    client = RagflowClient(
        _config(tmp_path, monkeypatch), transport=httpx.MockTransport(handler)
    )
    try:
        result = await client.retrieve(
            question="What changed?",
            dataset_ids=["dataset-1"],
            document_ids=["document-1"],
            top_n=5,
        )
    finally:
        await client.close()

    assert captured["dataset_ids"] == ["dataset-1"]
    assert captured["document_ids"] == ["document-1"]
    assert captured["page_size"] == 5
    assert captured["url"] == "https://aaas.example.test/AIGCQA/api/rag_retrieval"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["sys-code"] == "EASYAGENT"
    assert headers["stdauthToken".lower()] == "aaas-test-token"
    assert headers["stdrqtsyt"] == "EASYAGENT"
    assert headers["stdrspsys"] == "AIGCQA"
    assert headers["stdbanknum"] == "001"
    assert headers["stdtransid"].startswith("EASYAGENT")
    assert headers["stdapplysysttmtp"]
    assert result["chunks"][0]["document_id"] == "doc_ready"


@pytest.mark.asyncio
async def test_local_v017_bridge_redirects_retrieval_and_strips_aaas_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("RAGFLOW_LOCAL_COMPAT_ENABLED", "true")
    monkeypatch.setenv("RAGFLOW_LOCAL_BASE_URL", "https://local-ragflow.example.test")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        return httpx.Response(200, json={"code": 0, "data": {"chunks": []}})

    client = RagflowClient(
        _config(tmp_path, monkeypatch), transport=httpx.MockTransport(handler)
    )
    try:
        await client.retrieve(
            question="q",
            dataset_ids=["dataset-1"],
            document_ids=["document-1"],
            top_n=5,
        )
    finally:
        await client.close()

    assert captured["url"] == "https://local-ragflow.example.test/api/v1/retrieval"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers["sys-code"] == "EASYAGENT"
    assert not any(name.startswith("std") for name in headers)


@pytest.mark.asyncio
async def test_local_v017_bridge_can_skip_unsupported_presentation_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("RAGFLOW_LOCAL_COMPAT_ENABLED", "true")
    config = _config(tmp_path, monkeypatch)
    config.adapter.local_v017_bridge.skip_document_configuration_extensions = [
        "ppt",
        "pptx",
    ]
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"code": 0, "data": True})

    client = RagflowClient(config, transport=httpx.MockTransport(handler))
    try:
        await client.configure_document(
            dataset_id="dataset-1",
            document_id="presentation-1",
            filename="report.PPTX",
        )
        await client.configure_document(
            dataset_id="dataset-1",
            document_id="document-1",
            filename="report.docx",
        )
    finally:
        await client.close()

    assert len(calls) == 1
    assert calls[0].url.path.endswith("/documents/document-1")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["dataset", "document", "retrieve_dataset", "retrieve_document"])
async def test_empty_destructive_or_retrieval_scope_is_rejected_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
):
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"code": 0})

    client = RagflowClient(
        _config(tmp_path, monkeypatch), transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(ValueError, match="must be non-empty"):
            if kind == "dataset":
                await client.delete_datasets([])
            elif kind == "document":
                await client.delete_documents(dataset_id="dataset-1", document_ids=[])
            elif kind == "retrieve_dataset":
                await client.retrieve(
                    question="q", dataset_ids=[], document_ids=["document-1"], top_n=5
                )
            else:
                await client.retrieve(
                    question="q", dataset_ids=["dataset-1"], document_ids=[], top_n=5
                )
    finally:
        await client.close()

    assert called is False


@pytest.mark.asyncio
async def test_non_json_success_response_is_contract_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client = RagflowClient(
        _config(tmp_path, monkeypatch),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, text="<html>proxy login</html>", headers={"content-type": "text/html"}
            )
        ),
    )
    try:
        with pytest.raises(RagflowContractError, match="non-JSON"):
            await client.list_datasets()
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_safe_read_retries_but_mutation_does_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"code": 0, "data": []})

    client = RagflowClient(
        _config(tmp_path, monkeypatch), transport=httpx.MockTransport(handler)
    )
    client._backoff = AsyncMock()
    try:
        assert await client.list_datasets() == []
        assert calls == 2
    finally:
        await client.close()

    mutation_calls = 0

    def mutation_handler(request: httpx.Request) -> httpx.Response:
        nonlocal mutation_calls
        mutation_calls += 1
        return httpx.Response(503)

    client = RagflowClient(
        _config(tmp_path, monkeypatch),
        transport=httpx.MockTransport(mutation_handler),
    )
    try:
        with pytest.raises(RagflowUnavailableError):
            await client.create_dataset(name="no retry")
    finally:
        await client.close()
    assert mutation_calls == 1
