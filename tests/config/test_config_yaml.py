from easy_agent.config import Config, get_missing_env_vars


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
    port: ${CONFIG_TEST_MYSQL_PORT:-3307}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_TEST_API_KEY", "from-environment")
    monkeypatch.setenv("CONFIG_TEST_MYSQL_PASSWORD", "from-environment")
    monkeypatch.delenv("CONFIG_TEST_MYSQL_PORT", raising=False)

    config = Config.from_yaml(config_path)

    assert config.llm.api_key == "from-environment"
    assert config.database.mysql.password == "from-environment"
    # ${VAR:-default}：变量未设置时回退默认值
    assert config.database.mysql.port == 3307
    assert get_missing_env_vars() == []


def test_config_from_yaml_ignores_log_configuration(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: sk-test
log:
  dir: ./ignored
log_dir: ./ignored
""",
        encoding="utf-8",
    )

    config = Config.from_yaml(config_path)

    assert not hasattr(config, "log")
    assert not hasattr(config.agent, "log_dir")


def test_config_from_yaml_reports_unresolved_placeholders(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: "sk-fallback"
workspace_dir: "${CONFIG_TEST_UNSET_DIR}"
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("CONFIG_TEST_UNSET_DIR", raising=False)

    config = Config.from_yaml(config_path)

    # 未取到值且无默认值 -> 空串，并记录变量名供启动日志提示
    assert config.agent.workspace_dir == ""
    assert get_missing_env_vars() == ["CONFIG_TEST_UNSET_DIR"]


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
