from easy_agent.config import Config


def test_config_from_yaml_does_not_expand_environment_placeholders(tmp_path, monkeypatch):
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

    assert config.llm.api_key == "${CONFIG_TEST_API_KEY}"
    assert config.database.mysql.password == "${CONFIG_TEST_MYSQL_PASSWORD}"


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
