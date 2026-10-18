"""配置模块拆分回归（批次 1A）：config_models / config_validation / re-export。

覆盖：
- Pydantic 模型与默认值可直接从 ``easy_agent.bootstrap.config_models`` 导入；
- 配置键校验 / ``${VAR}`` 展开来自 ``easy_agent.bootstrap.config_validation``；
- ``easy_agent.bootstrap.config`` 的 re-export 是同一对象（既有导入路径兼容）；
- ``Config.from_yaml`` 最小配置仍可加载，未解析占位符记录不丢。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import easy_agent.bootstrap.config as config_module
import easy_agent.bootstrap.config_models as config_models
import easy_agent.bootstrap.config_validation as config_validation


def _write_minimal_config(tmp_path: Path, extra: str = "") -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        "model: test-model\n"
        "models:\n"
        "  test-model:\n"
        "    provider: test\n"
        "    api_key: sk-test\n" + extra,
        encoding="utf-8",
    )
    return path


class TestModelDefaults:
    def test_retry_and_provider_defaults(self):
        retry = config_models.RetryConfig()
        assert retry.enabled is True
        assert retry.max_retries == 3

        provider = config_models.ProviderConfig()
        assert provider.provider == ""
        assert provider.model == ""
        assert provider.context_length == 1_000_000
        assert provider.protocol == "openai"
        assert provider.headers == {}

    def test_llm_and_tools_defaults(self):
        llm = config_models.LLMConfig(api_key="sk-test")
        assert llm.model == "claude-sonnet-4-6"
        assert llm.provider == "minimax"
        assert llm.context_length == 1_000_000
        assert llm.retry == config_models.RetryConfig()

        tools = config_models.ToolsConfig()
        assert tools.skills_dir == "./skills"
        assert tools.prompts_dir == "./prompts"
        assert tools.experts_dir == "./experts"
        assert tools.result_log_truncate == 200

    def test_database_and_agent_defaults(self):
        database = config_models.DatabaseConfig()
        assert database.type == "sqlite"
        assert database.sqlite.path == "./data/easy_agent.db"
        assert database.mysql.port == 3306
        assert database.mysql.pool.pool_size == 5

        agent = config_models.AgentConfig()
        assert agent.max_steps == 50
        assert agent.workspace_dir == "./workspace"
        assert agent.sandbox_enabled is True
        assert agent.skill_write == "new-only"

    def test_limits_and_lock_defaults(self):
        distributed_lock = config_models.DistributedLockConfig()
        assert distributed_lock.enabled is False
        assert distributed_lock.key_prefix == "easy_agent"
        assert distributed_lock.ttl_seconds == 300.0

        limits = config_models.UploadLimitsConfig()
        assert limits.max_file_size_mb == 10
        assert limits.max_files_per_message == 5
        assert limits.max_total_size_mb == 20

        file_context = config_models.FileContextConfig()
        assert file_context.enabled is True
        assert file_context.max_stored_chars == 200000

    def test_welcome_title_and_web_search_url_constants(self):
        assert config_models.DEFAULT_APP_WELCOME_TITLE
        assert set(config_models.DEFAULT_WEB_SEARCH_API_URLS) == {
            "tavily",
            "internal",
        }
        assert config_models.DEFAULT_WEB_SEARCH_API_URLS["tavily"].startswith(
            "https://"
        )


class TestReExports:
    def test_models_are_the_same_objects(self):
        for name in (
            "RetryConfig",
            "ProviderConfig",
            "LLMConfig",
            "ToolsConfig",
            "SQLiteConfig",
            "MySQLPoolConfig",
            "MySQLConfig",
            "DatabaseConfig",
            "AgentConfig",
            "MCPToolConfig",
            "SummarizationConfig",
            "WebSearchConfig",
            "PresetQuestionGroup",
            "ExpertTag",
            "DistributedLockConfig",
            "PersonnelConfig",
            "UploadLimitsConfig",
            "FileContextConfig",
        ):
            assert getattr(config_module, name) is getattr(config_models, name), name

    def test_validation_helpers_are_the_same_objects(self):
        for name in (
            "KNOWN_TOP_LEVEL_KEYS",
            "KNOWN_SECTION_KEYS",
            "KNOWN_PROVIDER_KEYS",
            "DEPRECATED_CONFIG_KEYS",
            "CONFIG_STRICT_ENV_VAR",
            "find_unknown_config_keys",
            "find_deprecated_config_keys",
            "validate_config_keys",
            "get_missing_env_vars",
            "_get_section",
            "_expand_env",
            "_expand_env_recursive",
            "_ENV_VAR_RE",
            "_ensure_project_env_loaded",
            "_set_unresolved_env_vars",
        ):
            assert getattr(config_module, name) is getattr(
                config_validation, name
            ), name

    def test_constants_are_the_same_objects(self):
        assert (
            config_module.DEFAULT_APP_WELCOME_TITLE
            is config_models.DEFAULT_APP_WELCOME_TITLE
        )
        assert (
            config_module.DEFAULT_WEB_SEARCH_API_URLS
            is config_models.DEFAULT_WEB_SEARCH_API_URLS
        )


class TestValidationFunctions:
    def test_unknown_keys_are_detected(self):
        data = {
            "model": "m",
            "max_steps": 10,
            "unknown_key": 1,
            "web_search": {"enabled": True, "typo": "x"},
            "models": {"p": {"api_key": "k", "typo": "x"}},
        }
        assert config_validation.find_unknown_config_keys(data) == [
            "models.p.typo",
            "unknown_key",
            "web_search.typo",
        ]

    def test_deprecated_keys_and_non_mapping_root(self):
        data = {"llm": {"provider": "x"}, "knowledge": {"implementation": "v2"}}
        assert config_validation.find_deprecated_config_keys(data) == [
            "llm",
            "knowledge.implementation",
        ]
        assert config_validation.find_unknown_config_keys(None) == ["<root>"]

    def test_validate_config_keys_warns_and_strict_raises(self, caplog):
        assert config_validation.validate_config_keys({"unknown_key": 1}) == [
            "unknown_key"
        ]
        assert "unknown_key" in caplog.text

        with pytest.raises(ValueError, match="unknown_key"):
            config_validation.validate_config_keys({"unknown_key": 1}, strict=True)

    def test_get_section(self):
        data = {"database": {"mysql": {"host": "db"}}}
        assert config_validation._get_section(data, "database.mysql") == {
            "host": "db"
        }
        assert config_validation._get_section(data, "database.sqlite") is None
        assert config_validation._get_section(data, "database") == {
            "mysql": {"host": "db"}
        }


class TestEnvExpansion:
    def test_placeholders_and_missing_tracking(self, monkeypatch):
        monkeypatch.setenv("SPLIT_TEST_KEY", "secret")
        monkeypatch.delenv("SPLIT_TEST_UNSET", raising=False)
        missing: set[str] = set()
        expanded = config_validation._expand_env_recursive(
            {"a": "${SPLIT_TEST_KEY}", "b": "${SPLIT_TEST_UNSET}", "c": 3}, missing
        )
        assert expanded == {"a": "secret", "b": "", "c": 3}
        assert missing == {"SPLIT_TEST_UNSET"}

    def test_default_value(self, monkeypatch):
        monkeypatch.delenv("SPLIT_TEST_DEFAULT", raising=False)
        assert (
            config_validation._expand_env("${SPLIT_TEST_DEFAULT:-fallback}", set())
            == "fallback"
        )


class TestConfigFromYaml:
    def test_minimal_config_loads(self, tmp_path):
        path = _write_minimal_config(tmp_path)
        config = config_module.Config.from_yaml(path)

        assert config.llm.api_key == "sk-test"
        assert config.llm.model == "claude-sonnet-4-6"
        assert config.active_model == "test-model"
        assert config.models["test-model"].provider == "test"
        assert config.app_welcome_title == config_models.DEFAULT_APP_WELCOME_TITLE

    def test_env_placeholders_and_missing_tracking(self, tmp_path, monkeypatch):
        path = _write_minimal_config(
            tmp_path,
            "workspace_dir: ${SPLIT_TEST_WS:-/tmp/ws}\n"
            "memories_dir: ${SPLIT_TEST_MEM_UNSET}\n",
        )
        monkeypatch.delenv("SPLIT_TEST_WS", raising=False)
        monkeypatch.delenv("SPLIT_TEST_MEM_UNSET", raising=False)

        config = config_module.Config.from_yaml(path)

        assert config.agent.workspace_dir == "/tmp/ws"
        assert config.agent.memories_dir == ""
        assert config_validation.get_missing_env_vars() == ["SPLIT_TEST_MEM_UNSET"]
        # re-export 的函数与 config_validation 共享同一份全局状态。
        assert config_module.get_missing_env_vars() == ["SPLIT_TEST_MEM_UNSET"]

    def test_unknown_key_warns_but_loads(self, tmp_path, caplog):
        path = _write_minimal_config(tmp_path, "unknown_key: 1\n")

        with caplog.at_level("WARNING", logger="easy_agent.bootstrap.config"):
            config = config_module.Config.from_yaml(path)

        assert config.llm.api_key == "sk-test"
        assert "unknown_key" in caplog.text
