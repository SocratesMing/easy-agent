"""手动压缩 API：真实 SQLite 图、归属、去重和历史投影。"""
from datetime import UTC, datetime, timedelta

import pytest

from easy_agent.db.providers import get_session_runtime_repository
from easy_agent.db.repositories.message_repository import MessageRepository
from easy_agent.services.agents import manager
from easy_agent.services.sessions import compaction
from tests.services.test_manual_compaction import agent, config, history


@pytest.fixture()
def setup_compaction(client, db, monkeypatch, tmp_path):
    cfg = config()
    monkeypatch.setattr(manager, "get_agent_config", lambda: {"config": cfg})
    agents = {}

    async def create(sid, **kwargs):
        if sid not in agents:
            agents[sid] = agent(compaction.get_session_checkpointer(sid), tmp_path / sid)
        return agents[sid]

    monkeypatch.setattr(compaction, "get_or_create_agent_for_session", create)
    monkeypatch.setattr(compaction, "is_session_streaming", lambda sid: False)
    sid = client.post("/agent/sessions", json={}).json()["session_id"]
    for row in history():
        MessageRepository(db).add_message(sid, row)
    return sid, agents, cfg


def test_compact_api_projection_dedupe_and_session_isolation(client, db, setup_compaction):
    sid, agents, _ = setup_compaction
    url = f"/agent/sessions/{sid}/context"
    assert client.get(url).json()["supported"]
    body = {"model": "small", "request_id": "request-api-1"}
    result = client.post(url + "/compact", json=body)
    assert result.status_code == 200, result.text
    record = result.json()["compaction"]
    assert record["status"] == "compacted" and record["after_tokens"] < record["before_tokens"]
    assert client.post(url + "/compact", json=body).json() == result.json()
    assert len(agents[sid].model.inputs) == 1
    for suffix in ("", "/history", "/context"):
        projection = client.get(f"/agent/sessions/{sid}{suffix}").json()
        assert projection["compactions"] == [record]
    assert len(client.get(f"/agent/sessions/{sid}/history").json()["messages"]) == len(history())
    other = client.post("/agent/sessions", json={}).json()["session_id"]
    assert client.get(f"/agent/sessions/{other}/context").json()["compactions"] == []
    with db.get_connection() as conn:
        conn.execute("UPDATE sessions SET username='another' WHERE session_id=?", (sid,))
    assert client.get(url).status_code == 404
    assert client.post(url + "/compact", json=body).status_code == 404


def test_api_rejects_busy_approval_empty_invalid_and_unsupported(client, db, setup_compaction, monkeypatch):
    sid, _, cfg = setup_compaction
    url = f"/agent/sessions/{sid}/context/compact"
    body = {"model": "small", "request_id": "request-api-2"}
    assert client.post(url, json={**body, "request_id": "!"}).status_code == 422
    assert client.post(url, json={**body, "model": "missing"}).status_code == 422
    rt = get_session_runtime_repository()
    now = datetime.now(UTC)
    assert rt.try_acquire_session_stream(sid, "another-operation", (now + timedelta(minutes=1)).isoformat(), now.isoformat())[0]
    assert client.post(url, json=body).status_code == 409
    rt.release_session_stream(sid, "another-operation", now.isoformat())
    MessageRepository(db).add_message(sid, {"role": "assistant", "content": "", "pending_approval": {"thread_id": "pending"}})
    assert client.post(url, json=body).status_code == 409
    empty = client.post("/agent/sessions", json={}).json()["session_id"]
    assert client.post(f"/agent/sessions/{empty}/context/compact", json=body).status_code == 409
    cfg.agent.session_thread = False
    assert client.post(url, json=body).status_code == 409
    cfg.agent.session_thread = True
    monkeypatch.setenv("EASY_REPLICA_COUNT", "2")
    assert not client.get(f"/agent/sessions/{sid}/context").json()["supported"]
    assert client.post(url, json=body).status_code == 409
