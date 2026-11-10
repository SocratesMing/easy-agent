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
