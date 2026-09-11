from easy_agent.config import Config


def test_config_from_yaml_expands_environment_placeholders(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: "${CONFIG_TEST_API_KEY}"
database:
  type: mysql
  mysql:
    password: "${CONFIG_TEST_MYSQL_PASSWORD}"
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_TEST_API_KEY", "from-environment")
    monkeypatch.setenv("CONFIG_TEST_MYSQL_PASSWORD", "from-environment")

    config = Config.from_yaml(config_path)

    assert config.llm.api_key == "from-environment"
    assert config.database.mysql.password == "from-environment"


def test_config_from_yaml_expands_typed_default(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: "${CONFIG_TEST_API_KEY}"
    timeout_seconds: "${CONFIG_TEST_TIMEOUT:-75.5}"
database:
  type: sqlite
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_TEST_API_KEY", "secret")
    monkeypatch.delenv("CONFIG_TEST_TIMEOUT", raising=False)

    config = Config.from_yaml(config_path)

    assert config.llm.timeout_seconds == 75.5
    assert config.models["test-model"].timeout_seconds == 75.5


def test_config_from_yaml_rejects_unresolved_selected_secret(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: selected
models:
  selected:
    api_key: "${MISSING_SELECTED_API_KEY}"
  inactive:
    api_key: "${MISSING_INACTIVE_API_KEY}"
database:
  type: sqlite
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("MISSING_SELECTED_API_KEY", raising=False)
    monkeypatch.delenv("MISSING_INACTIVE_API_KEY", raising=False)

    try:
        Config.from_yaml(config_path)
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("unresolved selected secret must fail")

    assert "models.selected.api_key" in message
    assert "MISSING_SELECTED_API_KEY" not in message
    assert "inactive" not in message


def test_resolve_config_path_prefers_explicit_path(tmp_path):
    explicit_path = tmp_path / "custom.yaml"

    resolved = Config.resolve_config_path(explicit_path, config_dir=tmp_path)

    assert resolved == explicit_path


def test_resolve_config_path_uses_agent_env(tmp_path, monkeypatch):
    (tmp_path / "config.prod.yaml").touch()
    monkeypatch.delenv("EASY_CONFIG", raising=False)
    monkeypatch.setenv("AGENT_ENV", "prod")

    resolved = Config.resolve_config_path(config_dir=tmp_path)

    assert resolved == tmp_path / "config.prod.yaml"


def test_resolve_config_path_defaults_to_dev(tmp_path, monkeypatch):
    (tmp_path / "config.dev.yaml").touch()
    (tmp_path / "config.yaml").touch()
    monkeypatch.delenv("EASY_CONFIG", raising=False)
    monkeypatch.delenv("AGENT_ENV", raising=False)

    resolved = Config.resolve_config_path(config_dir=tmp_path)

    assert resolved == tmp_path / "config.dev.yaml"


def test_database_fallback_flag_is_loaded(tmp_path):
    """MySQL deployments can fail closed instead of silently using SQLite."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: test
models:
  test:
    provider: test
    api_key: test-key
    model: test-model
database:
  type: mysql
  fallback_to_sqlite: false
  mysql:
    host: 127.0.0.1
    port: 3307
    user: easyagent
    password: test
    database: agent
""",
        encoding="utf-8",
    )

    config = Config.from_yaml(config_path)

    assert config.database.type == "mysql"
    assert config.database.fallback_to_sqlite is False


def test_mysql_fallback_is_fail_closed_by_default(tmp_path):
    """Omitting the flag must never silently split production data."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: test
models:
  test:
    provider: test
    api_key: test-key
    model: test-model
database:
  type: mysql
  mysql:
    host: 127.0.0.1
    user: easyagent
    password: test
    database: agent
""",
        encoding="utf-8",
    )

    assert Config.from_yaml(config_path).database.fallback_to_sqlite is False


def test_personnel_self_registration_is_fail_closed_and_can_be_enabled(tmp_path):
    base = """
model: test
models:
  test:
    provider: test
    api_key: test-key
    model: test-model
database:
  type: sqlite
"""
    disabled_path = tmp_path / "disabled.yaml"
    enabled_path = tmp_path / "enabled.yaml"
    disabled_path.write_text(base, encoding="utf-8")
    enabled_path.write_text(
        base + "personnel:\n  self_registration_enabled: true\n",
        encoding="utf-8",
    )

    assert Config.from_yaml(disabled_path).personnel.self_registration_enabled is False
    assert Config.from_yaml(enabled_path).personnel.self_registration_enabled is True
