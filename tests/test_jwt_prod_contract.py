"""生产 JWT 密钥契约测试（纯函数 + 启动期 fail-fast）。"""

from types import SimpleNamespace

import pytest

import easy_agent.app as app_mod
from easy_agent.utils.auth import (
    _load_or_create_secret,
    validate_production_jwt_secret,
)


def test_prod_without_env_secret_raises(monkeypatch):
    monkeypatch.delenv("EASY_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="EASY_JWT_SECRET"):
        validate_production_jwt_secret(env="prod")


def test_prod_with_blank_secret_raises():
    with pytest.raises(RuntimeError, match="data/.jwt_secret"):
        validate_production_jwt_secret(env="prod", secret="   ")


def test_prod_with_secret_passes():
    assert validate_production_jwt_secret(env="prod", secret="s" * 48) is None
    assert validate_production_jwt_secret(env="PROD", secret="s" * 48) is None


def test_dev_and_test_without_secret_pass(monkeypatch):
    monkeypatch.delenv("EASY_JWT_SECRET", raising=False)
    assert validate_production_jwt_secret(env="dev") is None
    assert validate_production_jwt_secret(env="test") is None
    assert validate_production_jwt_secret(env="") is None


def test_env_defaults_to_agent_env(monkeypatch):
    monkeypatch.setenv("AGENT_ENV", "prod")
    monkeypatch.delenv("EASY_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="EASY_JWT_SECRET"):
        validate_production_jwt_secret()

    monkeypatch.setenv("EASY_JWT_SECRET", "x" * 40)
    assert validate_production_jwt_secret() is None


def test_prod_secret_loader_does_not_fall_back_to_file(monkeypatch, tmp_path):
    secret_file = tmp_path / ".jwt_secret"
    monkeypatch.setenv("AGENT_ENV", "prod")
    monkeypatch.delenv("EASY_JWT_SECRET", raising=False)

    secret = _load_or_create_secret(secret_file)

    assert secret
    assert not secret_file.exists()  # 生产不得把密钥落到本地文件


def test_dev_secret_loader_still_uses_file(monkeypatch, tmp_path):
    secret_file = tmp_path / ".jwt_secret"
    monkeypatch.setenv("AGENT_ENV", "dev")
    monkeypatch.delenv("EASY_JWT_SECRET", raising=False)

    first = _load_or_create_secret(secret_file)
    second = _load_or_create_secret(secret_file)

    assert secret_file.exists()
    assert first == second


async def test_lifespan_prod_fails_before_serving(monkeypatch):
    monkeypatch.delenv("EASY_JWT_SECRET", raising=False)
    fake_runtime = SimpleNamespace(
        environment=SimpleNamespace(agent_env="prod"),
    )
    monkeypatch.setattr(app_mod, "runtime_initialization", fake_runtime)

    with pytest.raises(RuntimeError, match="EASY_JWT_SECRET"):
        async with app_mod.lifespan(app_mod.app):
            pass
