import pytest

from easy_agent.bootstrap.initialization.environment import load_environment
from easy_agent.utils import env_loader


def test_prod_requires_cors_origins(monkeypatch):
    monkeypatch.setattr(env_loader, "_loaded", True)
    monkeypatch.setenv("AGENT_ENV", "prod")
    monkeypatch.delenv("EASY_CORS_ALLOW_ORIGINS", raising=False)
    with pytest.raises(RuntimeError, match="EASY_CORS_ALLOW_ORIGINS"):
        load_environment()
