"""Enhancement is a regular-user capability; management operations stay admin-only."""
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from easy_agent.api import prompt_enhancement as route
from easy_agent.bootstrap.config import Config
from easy_agent.bootstrap.config_models import PromptEnhancementConfig
from easy_agent.knowledges.facade import LegacyHostRoutes
from easy_agent.middleware.rate_limit import InMemorySlidingWindowCounter, RateLimitMiddleware
from easy_agent.services.prompt_enhancement import EnhancedPrompt, EnhancementError


@pytest.fixture()
def enhance_mock(monkeypatch):
    config = Config.model_construct(prompt_enhancement=PromptEnhancementConfig(max_input_chars=10))
    monkeypatch.setattr(route, "get_runtime_agent_config", lambda: {"config": config})
    mock = AsyncMock(return_value=EnhancedPrompt(enhanced_prompt="更清晰的提示词"))
    monkeypatch.setattr(route, "enhance_prompt", mock)
    return mock


def test_ordinary_user_and_legacy_entry(client, enhance_mock):
    for path in ["/agent/prompts/enhance", "/api/prompts/enhance"]:
        response = client.post(path, json={"prompt": "原文"})
        assert response.status_code == 200
        assert response.json() == {"enhanced_prompt": "更清晰的提示词"}
    assert client.post("/agent/prompts/query").status_code == 403
    assert enhance_mock.await_count == 2


def test_enhance_requires_jwt(auth_client, enhance_mock):
    assert auth_client.post("/agent/prompts/enhance", json={"prompt": "原文"}).status_code == 401
    enhance_mock.assert_not_called()


@pytest.mark.parametrize("body", [{}, {"prompt": None}, {"prompt": 4}, {"prompt": []}, {"prompt": " \n "}, {"prompt": "x" * 11}, {"prompt": "ok", "model": "untrusted"}])
def test_input_validation(client, enhance_mock, body):
    response = client.post("/agent/prompts/enhance", json=body)
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    enhance_mock.assert_not_called()


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_expected_errors(client, enhance_mock, status):
    enhance_mock.side_effect = EnhancementError(status)
    response = client.post("/agent/prompts/enhance", json={"prompt": "原文"})
    assert response.status_code == status
    assert "detail" in response.json()


def test_runtime_config_unavailable(client, monkeypatch):
    monkeypatch.setattr(route, "get_runtime_agent_config", lambda: None)
    assert client.post("/agent/prompts/enhance", json={"prompt": "原文"}).status_code == 503


def test_real_jwt_sdk_usage_isolates_users_and_keeps_parse_failure(auth_client, db, monkeypatch):
    from types import SimpleNamespace
    from openai.types.chat import ChatCompletion
    from easy_agent.services import prompt_enhancement as service
    from easy_agent.utils.usage_context import get_usage_identity
    from tests.test_prompt_enhancement import configuration

    monkeypatch.setattr(route, "get_runtime_agent_config", lambda: {"config": configuration()})
    response = ChatCompletion.model_validate({
        "id": "response", "object": "chat.completion", "created": 1, "model": "alias",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {
            "role": "assistant", "content": '{"enhanced_prompt":"valid"}',
        }}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    })
    observed = []

    async def create(**kwargs):
        observed.append(get_usage_identity())
        return response

    class SDK:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(service, "AsyncOpenAI", SDK)
    ids = []
    for username in ("usage-alice", "usage-bob"):
        user = db.register_user(username, "secret123")
        ids.append(user.user_id)
        token = auth_client.post("/agent/auth/login", json={"username": username, "password": "secret123"}).json()["access_token"]
        if username == "usage-bob":
            response.choices[0].message.content = "invalid JSON"
        result = auth_client.post("/agent/prompts/enhance", json={"prompt": "原文"}, headers={"Authorization": f"Bearer {token}"})
        assert result.status_code == (200 if username == "usage-alice" else 502), result.text

    with db.get_connection() as conn:
        rows = conn.execute("SELECT user_id, session_id, model_key, model_name, input_tokens, output_tokens FROM token_usage ORDER BY user_id").fetchall()
    assert len(rows) == 2
    assert sorted(row["user_id"] for row in rows) == sorted(ids)
    assert sorted(identity.user_id for identity in observed) == sorted(ids)
    assert all(row["session_id"] is None and row["model_key"] == "deepseek" and row["model_name"] == "configured-model" for row in rows)
    assert all((row["input_tokens"], row["output_tokens"]) == (100, 50) for row in rows)
    assert get_usage_identity() is None


def test_legacy_and_standard_share_rate_allowance(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    app = FastAPI()

    @app.post("/agent/prompts/enhance")
    async def endpoint():
        return {"ok": True}

    app.add_middleware(RateLimitMiddleware, counter=InMemorySlidingWindowCounter())
    app.add_middleware(LegacyHostRoutes)
    with TestClient(app) as client:
        for index in range(30):
            prefix = "/api" if index % 2 else "/agent"
            assert client.post(prefix + "/prompts/enhance").status_code == 200
        assert client.post("/api/prompts/enhance").status_code == 429
        assert client.post("/agent/prompts/enhance").status_code == 429
