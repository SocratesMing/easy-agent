"""Executable assertions for the pinned official RAGFlow fixture set."""

import json
from pathlib import Path

from easy_agent.knowledge.config import KnowledgeConfig
from easy_agent.knowledge.domain import DocumentStatus


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ragflow_v0_17_2"
TEMPLATE = Path(__file__).parents[1] / "fixtures" / "knowledge-config.yaml"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_all_official_document_states_map_fail_closed():
    config = KnowledgeConfig.from_yaml(TEMPLATE)
    payload = _fixture("document_statuses.json")
    mapped = {
        item["id"]: config.compatibility.map_document_status(item["run"])
        for item in payload["data"]["docs"]
    }

    assert mapped == {
        "doc_pending": DocumentStatus.PENDING,
        "doc_processing": DocumentStatus.PROCESSING,
        "doc_cancelled": DocumentStatus.CANCELLED,
        "doc_ready": DocumentStatus.READY,
        "doc_failed": DocumentStatus.FAILED,
    }
    assert config.compatibility.map_document_status("future_state") == DocumentStatus.UNKNOWN


def test_retrieval_fixture_uses_document_id_contract():
    payload = _fixture("retrieval_success.json")
    chunk = payload["data"]["chunks"][0]

    assert chunk["document_id"] == "doc_ready"
    assert "documents" not in chunk
    assert payload["data"]["total"] == 1


def test_code_zero_data_false_is_explicitly_not_a_success_shape():
    config = KnowledgeConfig.from_yaml(TEMPLATE)
    payload = _fixture("auth_failure_data_false.json")

    assert payload["code"] in config.response.envelope.success_codes
    assert payload["data"] is False
    assert payload["message"]
    assert config.response.envelope.data_false_with_message_is_error is True


def test_delete_success_may_omit_data():
    payload = _fixture("delete_success_no_data.json")

    assert payload == {"code": 0}
