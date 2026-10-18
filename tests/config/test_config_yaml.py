from easy_agent.bootstrap.config import Config, get_missing_env_vars


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


def test_config_web_search_placeholders(tmp_path, monkeypatch):
    """web_search 段复用 ${VAR} 占位符加载逻辑（与 models 一致）。"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: "sk-test"
web_search:
  enabled: ${CONFIG_TEST_WS_ENABLED:-true}
  provider: "${CONFIG_TEST_WS_PROVIDER:-tavily}"
  api_url: "${CONFIG_TEST_WS_URL:-http://default.example/api/v1/webSearch}"
  api_key: "${CONFIG_TEST_WS_KEY}"
  search_depth: "${CONFIG_TEST_WS_DEPTH:-basic}"
  timeout_seconds: ${CONFIG_TEST_WS_TIMEOUT:-30}
  max_results: ${CONFIG_TEST_WS_MAX:-5}
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_TEST_WS_KEY", "ws-secret")
    monkeypatch.setenv("CONFIG_TEST_WS_URL", "http://search.example/api/v1/webSearch")
    monkeypatch.delenv("CONFIG_TEST_WS_ENABLED", raising=False)
    monkeypatch.delenv("CONFIG_TEST_WS_TIMEOUT", raising=False)
    monkeypatch.delenv("CONFIG_TEST_WS_MAX", raising=False)

    config = Config.from_yaml(config_path)

    assert config.web_search.enabled is True
    assert config.web_search.provider == "tavily"
    assert config.web_search.api_url == "http://search.example/api/v1/webSearch"
    assert config.web_search.api_key == "ws-secret"
    assert config.web_search.search_depth == "basic"
    assert config.web_search.timeout_seconds == 30
    assert config.web_search.max_results == 5


def test_config_web_search_defaults_when_section_missing(tmp_path, monkeypatch):
    """未配置 web_search 段时使用默认值：api_key 为空 -> 功能不可用。"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: "sk-test"
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("WEB_SEARCH_API_KEY", raising=False)

    config = Config.from_yaml(config_path)

    assert config.web_search.enabled is True
    assert config.web_search.provider == "tavily"
    assert config.web_search.api_url == ""
    assert config.web_search.api_key == ""
    assert config.web_search.default_time_range == "NoLimit"


def test_config_distributed_lock_placeholders(tmp_path, monkeypatch):
    """distributed_lock 段复用 ${VAR} 占位符加载逻辑（与 web_search 一致）。"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: "sk-test"
distributed_lock:
  enabled: ${DISTRIBUTED_LOCK_ENABLED:-false}
  key_prefix: "${DISTRIBUTED_LOCK_KEY_PREFIX:-easy_agent}"
  ttl_seconds: ${DISTRIBUTED_LOCK_TTL:-300}
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("DISTRIBUTED_LOCK_ENABLED", raising=False)
    monkeypatch.delenv("DISTRIBUTED_LOCK_KEY_PREFIX", raising=False)
    monkeypatch.delenv("DISTRIBUTED_LOCK_TTL", raising=False)

    # 未设置环境变量时回退默认值：默认关闭，单实例部署不受影响
    config = Config.from_yaml(config_path)
    assert config.distributed_lock.enabled is False
    assert config.distributed_lock.key_prefix == "easy_agent"
    assert config.distributed_lock.ttl_seconds == 300

    # 多 pod 部署通过 .env.{AGENT_ENV} 打开开关并覆盖 TTL
    monkeypatch.setenv("DISTRIBUTED_LOCK_ENABLED", "true")
    monkeypatch.setenv("DISTRIBUTED_LOCK_KEY_PREFIX", "easy-agent-prod")
    monkeypatch.setenv("DISTRIBUTED_LOCK_TTL", "120")

    config = Config.from_yaml(config_path)
    assert config.distributed_lock.enabled is True
    assert config.distributed_lock.key_prefix == "easy-agent-prod"
    assert config.distributed_lock.ttl_seconds == 120
    assert config.distributed_lock.renew_interval_seconds == 100


def test_config_distributed_lock_disabled_when_section_missing(tmp_path):
    """未配置 distributed_lock 段时默认关闭（多实例部署需显式开启）。"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: "sk-test"
""",
        encoding="utf-8",
    )

    config = Config.from_yaml(config_path)

    assert config.distributed_lock.enabled is False
    assert config.distributed_lock.ttl_seconds == 300


def test_resolve_config_path_ignores_agent_env(tmp_path, monkeypatch):
    """不再按 AGENT_ENV 选 yaml：即使 config.prod.yaml 存在，也一律返回 config.yaml。

    环境差异改由 .env.{AGENT_ENV} 注入（见 utils/env_loader.py）。
    """
    (tmp_path / "config.prod.yaml").touch()
    (tmp_path / "config.yaml").touch()
    monkeypatch.delenv("EASY_CONFIG", raising=False)
    monkeypatch.setenv("AGENT_ENV", "prod")

    resolved = Config.resolve_config_path(config_dir=tmp_path)

    assert resolved == tmp_path / "config.yaml"


def test_resolve_config_path_defaults_to_config_yaml(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").touch()
    monkeypatch.delenv("EASY_CONFIG", raising=False)
    monkeypatch.delenv("AGENT_ENV", raising=False)

    resolved = Config.resolve_config_path(config_dir=tmp_path)

    assert resolved == tmp_path / "config.yaml"

def test_config_expert_tags_list_form(tmp_path):
    """expert_tags 支持 [{tag_id, tag_name}] 写法。"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: "sk-test"
expert_tags:
  - { tag_id: macro_research, tag_name: 宏观投研 }
  - tag_id: factor_analysis
    tag_name: 因子分析
  - tag_id: ""
    tag_name: 空条目应被丢弃
""",
        encoding="utf-8",
    )

    config = Config.from_yaml(config_path)
    assert [(t.tag_id, t.tag_name) for t in config.expert_tags] == [
        ("macro_research", "宏观投研"),
        ("factor_analysis", "因子分析"),
    ]


def test_config_expert_tags_dict_and_missing(tmp_path):
    """兼容 {tag_id: tag_name} 字典写法；未配置时为空列表。"""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: "sk-test"
expert_tags:
  bond_research: 债券投研
""",
        encoding="utf-8",
    )
    config = Config.from_yaml(config_path)
    assert [(t.tag_id, t.tag_name) for t in config.expert_tags] == [
        ("bond_research", "债券投研")
    ]

    config_path2 = tmp_path / "config2.yaml"
    config_path2.write_text(
        """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: "sk-test"
""",
        encoding="utf-8",
    )
    assert Config.from_yaml(config_path2).expert_tags == []


def test_config_agent_sandbox_enabled_is_parsed(tmp_path, monkeypatch):
    """回归：sandbox_enabled 必须真正解析进 agent 配置。

    历史 bug：AgentConfig 声明了该字段，但 _parse_agent_config 漏传，
    导致无论 config.yaml / SANDBOX_ENABLED 怎么写都恒定等于默认 True，
    沙箱无法通过配置关闭。
    """
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: "sk-test"
sandbox_enabled: ${CONFIG_TEST_SANDBOX:-true}
""",
        encoding="utf-8",
    )

    monkeypatch.delenv("CONFIG_TEST_SANDBOX", raising=False)
    assert Config.from_yaml(config_path).agent.sandbox_enabled is True

    monkeypatch.setenv("CONFIG_TEST_SANDBOX", "false")
    assert Config.from_yaml(config_path).agent.sandbox_enabled is False

    # 段缺失时保持默认开启
    (tmp_path / "empty.yaml").write_text(
        'model: test-model\nmodels:\n  test-model:\n    api_key: "sk-test"\n',
        encoding="utf-8",
    )
    assert Config.from_yaml(tmp_path / "empty.yaml").agent.sandbox_enabled is True


def test_config_tools_result_log_truncate_is_parsed(tmp_path):
    """回归：tools.result_log_truncate 必须解析进 tools 配置。

    历史 bug：ToolsConfig 未声明该字段，_parse_tools_config 也不读，
    导致配置值被丢弃，消费方 getattr 永远回退硬编码默认值。
    """
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: "sk-test"
tools:
  result_log_truncate: 1234
  skills_dir: /tmp/skills
  prompts_dir: /tmp/prompts
""",
        encoding="utf-8",
    )

    config = Config.from_yaml(config_path)

    assert config.tools.result_log_truncate == 1234
    assert config.tools.skills_dir == "/tmp/skills"
    assert config.tools.prompts_dir == "/tmp/prompts"

    # 段缺失时回退默认 200
    (tmp_path / "empty.yaml").write_text(
        'model: test-model\nmodels:\n  test-model:\n    api_key: "sk-test"\n',
        encoding="utf-8",
    )
    assert Config.from_yaml(tmp_path / "empty.yaml").tools.result_log_truncate == 200
