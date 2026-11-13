"""Token 统计 API：真实 JWT、真实用户ID隔离与失败语义。"""

from types import SimpleNamespace
from unittest.mock import Mock
import time
import logging
import sqlite3

import pymysql

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from easy_agent.api.deps import get_token_usage_repository, get_user_repository
from easy_agent.api.usage import router
from easy_agent.db import get_database
from easy_agent.db.repositories.token_usage import TokenUsageRepository
from easy_agent.db.repositories.user_repository import UserRepository
from easy_agent.middleware import get_current_username
from easy_agent.utils.auth import create_access_token


@pytest.fixture
def usage_client(db):
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_database] = lambda: db
    with TestClient(app) as client:
        yield client


def login(db, username):
    user = db.register_user(username, "secret123")
    UserRepository(db).touch_user_activity(username, time.time())
    return user, {
        "Authorization": "Bearer " + create_access_token({"sub": username, "v": user.token_version})
    }


def add(repo, user, event, count):
    repo.record_usage(
        event_id=event,
        user_id=user,
        session_id=None,
        model_key="default",
        model_name="model",
        completed_at_ms=1,
        input_tokens=count,
        output_tokens=0,
    )


def test_real_jwt_isolation_and_client_user_id_is_ignored(db, usage_client):
    alice, alice_headers = login(db, "alice")
    bob, bob_headers = login(db, "bob")
    repo = TokenUsageRepository(db)
    add(repo, alice.user_id, "alice-event", 100)
    add(repo, bob.user_id, "bob-event", 999)
    # 用户名故意也有一条记录，必须查真实 user_id。
    add(repo, "alice", "wrong-username-event", 50000)
    response = usage_client.get(f"/agent/usage?user_id={bob.user_id}", headers=alice_headers)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["summary"]["all_time"] == 100
    assert response.json()["days"] == 7
    assert len(response.json()["daily"]["dates"]) == 7
    response = usage_client.get("/agent/usage?days=30", headers=bob_headers)
    assert response.status_code == 200
    assert response.json()["summary"]["all_time"] == 999
    assert len(response.json()["daily"]["dates"]) == 30
    assert response.headers["Cache-Control"] == "no-store"
    response = usage_client.get("/agent/usage?days=7", headers=alice_headers)
    assert response.status_code == 200
    assert response.json()["days"] == 7
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("days", ["1", "0", "all", "8", "-7", "7.1"])
def test_invalid_days_return_422(db, usage_client, days):
    _, headers = login(db, "alice")
    response = usage_client.get(f"/agent/usage?days={days}", headers=headers)
    assert response.status_code == 422
    assert response.headers["Cache-Control"] == "no-store"


def test_anonymous_and_missing_jwt_user_return_401(usage_client):
    response = usage_client.get("/agent/usage")
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["WWW-Authenticate"] == "Bearer"
    headers = {"Authorization": "Bearer " + create_access_token({"sub": "missing", "v": 0})}
    response = usage_client.get("/agent/usage", headers=headers)
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"


def test_user_disappears_after_auth_is_401_not_username_fallback(usage_client):
    usage_client.app.dependency_overrides[get_current_username] = lambda: "deleted"
    usage_client.app.dependency_overrides[get_user_repository] = lambda: SimpleNamespace(
        get_user_by_username=lambda _: None
    )
    repo = Mock()
    usage_client.app.dependency_overrides[get_token_usage_repository] = lambda: repo
    response = usage_client.get("/agent/usage")
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"
    repo.query.assert_not_called()


@pytest.mark.parametrize("failing", ["user", "statistics"])
@pytest.mark.parametrize("error", [sqlite3.OperationalError, pymysql.err.OperationalError])
def test_database_failures_return_503_and_no_store(usage_client, failing, error):
    usage_client.app.dependency_overrides[get_current_username] = lambda: "alice"
    users = SimpleNamespace(
        get_user_by_username=Mock(return_value=SimpleNamespace(user_id="real-id"))
    )
    stats = SimpleNamespace(query=Mock())
    if failing == "user":
        users.get_user_by_username.side_effect = error("database unavailable")
    else:
        stats.query.side_effect = error("database unavailable")
    usage_client.app.dependency_overrides[get_user_repository] = lambda: users
    usage_client.app.dependency_overrides[get_token_usage_repository] = lambda: stats
    response = usage_client.get("/agent/usage")
    assert response.status_code == 503
    assert response.json()["detail"] == "Token统计暂不可用，请重试"
    assert response.headers["Cache-Control"] == "no-store"


def test_database_failure_during_real_auth_returns_503(usage_client):
    users = SimpleNamespace(get_user_by_username=Mock(side_effect=sqlite3.OperationalError("unavailable")))
    usage_client.app.dependency_overrides[get_user_repository] = lambda: users
    headers = {"Authorization": "Bearer " + create_access_token({"sub": "alice", "v": 0})}
    response = usage_client.get("/agent/usage", headers=headers)
    assert response.status_code == 503
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("failure", ["type_error", "output_validation", "auth_error"])
def test_programming_errors_stay_500_with_traceback_and_no_store(usage_client, caplog, failure):
    users = SimpleNamespace(get_user_by_username=Mock(return_value=SimpleNamespace(user_id="id")))
    stats = SimpleNamespace(query=Mock(return_value={}))
    if failure == "auth_error":
        users.get_user_by_username.side_effect = TypeError("private diagnostic")
    else:
        usage_client.app.dependency_overrides[get_current_username] = lambda: "alice"
        if failure == "type_error":
            stats.query.side_effect = TypeError("private diagnostic")
    usage_client.app.dependency_overrides[get_user_repository] = lambda: users
    usage_client.app.dependency_overrides[get_token_usage_repository] = lambda: stats
    headers = {"Authorization": "Bearer " + create_access_token({"sub": "alice", "v": 0})}
    with caplog.at_level(logging.ERROR, logger="easy_agent.usage"):
        with TestClient(usage_client.app, raise_server_exceptions=False) as client:
            response = client.get("/agent/usage", headers=headers)
    assert response.status_code == 500
    assert response.headers["Cache-Control"] == "no-store"
    assert "private diagnostic" not in response.text
    assert "ValidationError" not in response.text
    assert any(record.exc_info for record in caplog.records if record.name == "easy_agent.usage")


def test_usage_response_policy_does_not_apply_to_other_routes(usage_client):
    @usage_client.app.get("/unrelated")
    def unrelated():
        return {"ok": True}

    response = usage_client.get("/unrelated")
    assert response.status_code == 200
    assert "Cache-Control" not in response.headers
