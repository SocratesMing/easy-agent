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
    # 已通过部署校验的共享 saver 不再按副本/worker 数禁用压缩；框架兼容检查仍保留。
    monkeypatch.setenv("EASY_REPLICA_COUNT", "2")
    monkeypatch.setenv("WEB_CONCURRENCY", "4")
    assert client.get(f"/agent/sessions/{sid}/context").json()["supported"]
    monkeypatch.setattr(compaction, "shared_checkpointer_active", lambda: False)
    assert not client.get(f"/agent/sessions/{sid}/context").json()["supported"]
    assert client.post(url, json=body).status_code == 409


@pytest.mark.parametrize("package,installed", [("deepagents", "0.7.29"), ("langgraph", "1.2.17")])
def test_unsupported_framework_rejected_before_agent_or_archive_work(
    client, setup_compaction, monkeypatch, package, installed,
):
    from easy_agent.agent import manual_compaction

    sid, agents, _ = setup_compaction
    real_version = manual_compaction.version
    monkeypatch.setattr(manual_compaction, "version", lambda name: installed if name == package else real_version(name))
    url = f"/agent/sessions/{sid}/context"
    status = client.get(url)
    assert status.status_code == 200
    assert status.json()["supported"] is False
    assert package in status.json()["reason"] and installed in status.json()["reason"]
    result = client.post(url + "/compact", json={"model": "small", "request_id": "unsupported-version"})
    assert result.status_code == 409
    assert result.json()["detail"] == status.json()["reason"]
    assert agents == {}  # 拒绝发生在建图、模型和 backend/归档之前。
    assert client.get(url).json()["compactions"] == []


def test_committed_result_still_recovers_when_framework_becomes_unsupported(client, setup_compaction, monkeypatch):
    from easy_agent.agent import manual_compaction

    sid, agents, _ = setup_compaction
    url = f"/agent/sessions/{sid}/context"
    body = {"model": "small", "request_id": "saved-before-upgrade"}
    result = client.post(url + "/compact", json=body)
    assert result.status_code == 200
    monkeypatch.setattr(manual_compaction, "version", lambda name: "unsupported")
    status = client.get(url).json()
    assert not status["supported"]
    assert status["compactions"] == [result.json()["compaction"]]
    retry = client.post(url + "/compact", json=body)
    assert retry.status_code == 200 and retry.json() == result.json()
    assert len(agents[sid].model.inputs) == 1


@pytest.mark.parametrize("double_summary", [False, True])
def test_summary_usage_survives_commit_failure_and_only_new_calls_bill(client, db, setup_compaction, monkeypatch, double_summary):
    from easy_agent.agent.token_usage import TokenUsageCallback, complete_usage_snapshot
    from tests.services.test_manual_compaction import Model

    owner = db.register_user("testuser", "password")
    sid, agents, _ = setup_compaction
    original_create = compaction.get_or_create_agent_for_session
    original_generate = Model._generate

    async def create(*args, **kwargs):
        result = await original_create(*args, **kwargs)
        result.model.callbacks = [TokenUsageCallback("small", "test")]
        return result

    def generate(self, *args, **kwargs):
        result = original_generate(self, *args, **kwargs)
        if double_summary and len(self.inputs) % 2:
            result.generations[0].message.content = "Previous conversation was too long to summarize."
        result.generations[0].generation_info = {
            "easy_agent_usage": [complete_usage_snapshot({"prompt_tokens": 20, "completion_tokens": 5})],
        }
        return result

    monkeypatch.setattr(compaction, "get_or_create_agent_for_session", create)
    monkeypatch.setattr(Model, "_generate", generate)
    runtime = get_session_runtime_repository()
    original_commit = runtime.guarded_commit
    attempts = []

    def commit(*args, **kwargs):
        attempts.append(None)
        if len(attempts) == 1:
            raise RuntimeError("checkpoint commit unavailable")
        return original_commit(*args, **kwargs)

    monkeypatch.setattr(runtime, "guarded_commit", commit)
    monkeypatch.setattr(compaction, "get_session_runtime_repository", lambda: runtime)
    url = f"/agent/sessions/{sid}/context/compact"
    body = {"model": "small", "request_id": "usage-request"}
    assert client.post(url, json=body).status_code == 502
    assert client.post(url, json=body).status_code == 200
    assert client.post(url, json=body).status_code == 200
    expected_calls = 4 if double_summary else 2
    assert len(agents[sid].model.inputs) == expected_calls
    with db.get_connection() as conn:
        rows = conn.execute("SELECT user_id, session_id, input_tokens, output_tokens FROM token_usage").fetchall()
    assert len(rows) == expected_calls
    assert all(row["user_id"] == owner.user_id and row["session_id"] == sid for row in rows)
    assert all((row["input_tokens"], row["output_tokens"]) == (20, 5) for row in rows)
