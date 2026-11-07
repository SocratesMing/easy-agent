"""环境初始化回归：prod 必须显式配置可信 CORS 来源；dev/test 有安全默认值。"""

import pytest

from easy_agent.bootstrap.env import PLACEHOLDER_PREFIX
from easy_agent.bootstrap.initialization.environment import (
    LOCAL_CORS_ORIGINS,
    load_environment,
)
from easy_agent.utils import env_loader


def _isolate_env_loader(monkeypatch):
    """跳过真实 .env 注入，避免测试之间互相污染。"""
    monkeypatch.setattr(env_loader, "_loaded", True)


def test_prod_requires_cors_origins(monkeypatch):
    _isolate_env_loader(monkeypatch)
    monkeypatch.setenv("AGENT_ENV", "prod")
    monkeypatch.delenv("EASY_CORS_ALLOW_ORIGINS", raising=False)
    with pytest.raises(RuntimeError, match="EASY_CORS_ALLOW_ORIGINS"):
        load_environment()


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "*",
        "https://a.example.com,*",
        PLACEHOLDER_PREFIX + "EASY_CORS_ALLOW_ORIGINS",
    ],
)
def test_prod_rejects_wildcard_blank_and_placeholder(monkeypatch, raw):
    _isolate_env_loader(monkeypatch)
    monkeypatch.setenv("AGENT_ENV", "prod")
    monkeypatch.setenv("EASY_CORS_ALLOW_ORIGINS", raw)
    with pytest.raises(RuntimeError, match="EASY_CORS_ALLOW_ORIGINS"):
        load_environment()


def test_prod_accepts_explicit_trusted_origins(monkeypatch):
    _isolate_env_loader(monkeypatch)
    monkeypatch.setenv("AGENT_ENV", "prod")
    monkeypatch.setenv("EASY_CORS_ALLOW_ORIGINS", "https://a.example.com,https://b.example.com")
    environment = load_environment()
    assert environment.cors_allow_origins == ["https://a.example.com", "https://b.example.com"]


@pytest.mark.parametrize("agent_env", ["dev", "test"])
@pytest.mark.parametrize(
    "raw",
    ["", PLACEHOLDER_PREFIX + "EASY_CORS_ALLOW_ORIGINS"],
)
def test_dev_test_generate_loopback_origins(monkeypatch, agent_env, raw):
    _isolate_env_loader(monkeypatch)
    monkeypatch.setenv("AGENT_ENV", agent_env)
    monkeypatch.setenv("EASY_CORS_ALLOW_ORIGINS", raw)
    environment = load_environment()
    assert environment.agent_env == agent_env
    assert environment.cors_allow_origins == LOCAL_CORS_ORIGINS
    assert "*" not in environment.cors_allow_origins


@pytest.mark.parametrize("agent_env", ["dev", "test"])
def test_dev_test_generate_loopback_origins_when_unset(monkeypatch, agent_env):
    _isolate_env_loader(monkeypatch)
    monkeypatch.setenv("AGENT_ENV", agent_env)
    monkeypatch.delenv("EASY_CORS_ALLOW_ORIGINS", raising=False)
    environment = load_environment()
    assert environment.cors_allow_origins == LOCAL_CORS_ORIGINS


def test_agent_env_defaults_to_dev(monkeypatch):
    _isolate_env_loader(monkeypatch)
    monkeypatch.delenv("AGENT_ENV", raising=False)
    monkeypatch.setenv("EASY_CORS_ALLOW_ORIGINS", "https://a.example.com")
    environment = load_environment()
    assert environment.agent_env == "dev"
