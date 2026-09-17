"""Regression checks for the upstream Vue 2 merge boundaries."""

import json

import pytest

from easy_agent.config import Config
from easy_agent.models.api import ChatRequest
from easy_agent.models.db import SessionModel, UserModel
from easy_agent.knowledge import host_streaming


@pytest.mark.asyncio
async def test_host_stream_retains_scope_events_and_citations(db, monkeypatch):
    db.create_session(SessionModel(session_id="merge-chat", title="Chat", username="alice",
        messages=[{"role": "user", "content": "question"}]))
    db.add_message_row("merge-chat", {"role": "user", "content": "question"})
    captured = {}

    async def upstream(**kwargs):
        captured.update(kwargs)
        yield host_streaming.format_sse({"type": "start"})
        db.add_message("merge-chat", {"role": "assistant", "content": "answer"})
        db.add_message_row("merge-chat", {"role": "assistant", "content": "answer"})
        yield host_streaming.format_sse({"type": "done"})

    monkeypatch.setattr(host_streaming, "host_stream", upstream)
    evidence = [{"document_id": "allowed-document", "content": "allowed text"}]
    request = ChatRequest(message="question", session_id="merge-chat")
    events = [json.loads(event.removeprefix("data: ").strip()) async for event in
        host_streaming.chat_stream_generator(request=request, db=db, session_id="merge-chat",
            context_prefix="authorized context", initial_events=[{"type": "knowledge_evidence", "evidence": evidence}],
            assistant_metadata={"knowledge_evidence": evidence})]
    assert [event["type"] for event in events] == ["start", "knowledge_evidence", "done"]
    assert captured["parsed_content"].startswith("authorized context")
    assert request.message == "question"
    assert db.get_session("merge-chat").messages[-1]["knowledge_evidence"] == evidence


def test_personnel_fields_survive_upstream_user_updates(db):
    user = UserModel(user_id="merge-person", username="merge-person", password_hash="",
        employee_id="MERGE-1", department_id="dept-1", department_name="市场部", display_name="测试人员")
    db.create_user(user)
    loaded = db.get_user_by_employee_id("MERGE-1")
    loaded.email = "person@example.test"
    db.update_user(loaded)
    result = db.get_user_by_id(user.user_id)
    assert result.department_id == "dept-1"
    assert result.department_name == "市场部"
    assert result.display_name == "测试人员"
    assert result.email == "person@example.test"


def test_legacy_config_survives_until_unified_config_is_provisioned(tmp_path, monkeypatch):
    monkeypatch.delenv("EASY_CONFIG", raising=False)
    monkeypatch.setenv("AGENT_ENV", "dev")
    legacy = tmp_path / "config.dev.yaml"
    legacy.touch()
    assert Config.resolve_config_path(config_dir=tmp_path) == legacy
    unified = tmp_path / "config.yaml"
    unified.touch()
    assert Config.resolve_config_path(config_dir=tmp_path) == unified


def test_default_policy_blocks_passwordless_impersonation(auth_client, db):
    response = auth_client.post("/agent/auth/login-passwordless", json={"username": "intruder"})
    assert response.status_code == 403
    assert db.get_user_by_username("intruder") is None


def test_legacy_alias_and_new_host_route_share_auth(auth_client):
    for path in ("/api/auth/profile", "/agent/auth/profile"):
        assert auth_client.get(path).status_code == 401
