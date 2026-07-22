"""Configuration management module

Provides unified configuration loading and management functionality
"""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

# Matches ${VAR} and ${VAR:-default} placeholders inside string values.
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand_env(value: str) -> str:
    """Resolve ${VAR} / ${VAR:-default} placeholders in a single string."""

    def _replace(match: re.Match) -> str:
        name = match.group(1)
        default = match.group(2)
        env_value = os.environ.get(name)
        if env_value is not None:
            return env_value
        return default if default is not None else ""

    return _ENV_VAR_RE.sub(_replace, value)


def _expand_env_recursive(obj: Any) -> Any:
    """Recursively expand env-var placeholders inside parsed YAML data."""
    if isinstance(obj, str):
        return _expand_env(obj)
    if isinstance(obj, dict):
        return {k: _expand_env_recursive(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_recursive(item) for item in obj]
    return obj


class RetryConfig(BaseModel):
    enabled: bool = True
    max_retries: int = 3


class ProviderConfig(BaseModel):
    """Individual provider/model configuration"""

    provider: str = ""
    api_key: str = ""
    model: str = ""
    api_base: str = ""
    max_input_tokens: int = 200000
    protocol: str = "openai"  # "openai" or "anthropic"


class LLMConfig(BaseModel):
    """LLM configuration - resolved from the active model selection"""

    api_key: str
    api_base: str | None = None
    model: str = "claude-sonnet-4-6"
    provider: str = "minimax"
    max_input_tokens: int = 200000  # Model context window size
    protocol: str = "openai"  # "openai" or "anthropic"
    retry: RetryConfig = Field(default_factory=RetryConfig)


class ToolsConfig(BaseModel):
    skills_dir: str = "./skills"
    prompts_dir: str = "./prompts"
    read_file_line_limit: int = 2000
    """read_file 内置工具每次读取的行数，默认 2000。"""


class SQLiteConfig(BaseModel):
    """SQLite configuration"""

    path: str = "./data/easy_agent.db"


class MySQLPoolConfig(BaseModel):
    """MySQL connection pool configuration"""

    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: int = 30
    pool_recycle: int = 3600


class MySQLConfig(BaseModel):
    """MySQL configuration"""

    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "easy_agent"
    charset: str = "utf8mb4"
    pool: MySQLPoolConfig = Field(default_factory=MySQLPoolConfig)
    connect_timeout: int = 10
    read_timeout: int = 30
    write_timeout: int = 30


class DatabaseConfig(BaseModel):
    """Database configuration - supports SQLite and MySQL"""

    type: str = "sqlite"
    sqlite: SQLiteConfig = Field(default_factory=SQLiteConfig)
    mysql: MySQLConfig = Field(default_factory=MySQLConfig)


class AgentConfig(BaseModel):
    """Agent configuration"""

    max_steps: int = 50
    workspace_dir: str = "./workspace"
    memories_dir: str = "./memories"
    log_dir: str = "./logs"
    sessions_dir: str = "./sessions"
    system_prompt_path: str = "system_prompt.md"
    denied_dirs: list[str | dict[str, Any]] = Field(default_factory=list)
    """禁止智能体读写的虚拟路径目录列表。

    支持两种格式（可混用）：
    - 字符串：``"/user-skills"`` → 默认禁止 read+write
    - 字典：``{path: "/user-skills", operations: ["write"]}`` → 只禁止指定操作

    operations 可选值：``read``、``write``，默认两者都禁止。
    通过 FilesystemPermission(mode="deny") 传入 create_deep_agent。
    """

    external_dirs: dict[str, str] = Field(default_factory=dict)
    """外部目录映射：虚拟路径前缀 → 宿主机实际路径。

    将 skill 需要访问的宿主机目录挂载为虚拟路径路由，使文件工具
    (ls/read_file/write_file 等) 能通过虚拟路径访问。
    例: {"/strategy-workspace/": "/home/sututu/code/finance-skills/fast_backtest/workspace"}
    """


class MCPToolConfig(BaseModel):
    """Configuration for a single MCP server"""

    name: str
    transport: str = "stdio"  # stdio | sse | http
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str = ""
    headers: dict[str, str] = Field(default_factory=dict)


class SummarizationConfig(BaseModel):
    """Conversation summarization/compression configuration"""

    enabled: bool = True
    compression_threshold: float = (
        0.8  # Trigger compression at this fraction of context window
    )
    compression_target: float = (
        0.1  # Keep this fraction of context window after compression
    )


class PresetQuestionGroup(BaseModel):
    """预设问题分组，可在配置文件中按分类组织。"""

    category: str = "预设问题"
    icon: str = ""
    questions: list[str] = Field(default_factory=list)


class Config(BaseModel):
    """Main configuration class"""

    llm: LLMConfig
    agent: AgentConfig
    tools: ToolsConfig
    summarization: SummarizationConfig = Field(default_factory=SummarizationConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    models: dict[str, ProviderConfig] = Field(default_factory=dict)
    active_model: str = "minimax"
    preset_questions: list[PresetQuestionGroup] = Field(default_factory=list)

    @field_validator("preset_questions", mode="before")
    @classmethod
    def _normalize_preset_questions(cls, value):
        """兼容旧版扁平字符串列表与分类字典两种写法。"""
        if not isinstance(value, list):
            return []
        groups: list[PresetQuestionGroup] = []
        for item in value:
            if isinstance(item, str):
                groups.append(PresetQuestionGroup(category="预设问题", questions=[item]))
            elif isinstance(item, dict):
                groups.append(PresetQuestionGroup(**item))
        return groups

    @classmethod
    def load(cls) -> "Config":
        """Load configuration from the default search path."""
        config_path = cls.get_default_config_path()
        if not config_path.exists():
            raise FileNotFoundError(
                "Configuration file not found. Place config.yaml in easy_agent/config/ directory."
            )
        return cls.from_yaml(config_path)

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "Config":
        """Load configuration from YAML file"""
        config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file does not exist: {config_path}")

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError("Configuration file is empty")

        # Expand ${ENV_VAR} / ${ENV_VAR:-default} placeholders (e.g. api_key, password).
        data = _expand_env_recursive(data)

        # Parse active model selection
        active_model = data.get("model", "minimax")

        # Parse provider-specific model configs
        models_data = data.get("models", {})
        models: dict[str, ProviderConfig] = {}
        for name, mcfg in models_data.items():
            if isinstance(mcfg, dict):
                models[name] = ProviderConfig(
                    provider=mcfg.get("provider", name),
                    api_key=mcfg.get("api_key", ""),
                    model=mcfg.get("model", ""),
                    api_base=mcfg.get("api_base", ""),
                    max_input_tokens=mcfg.get("max_input_tokens", 200000),
                    protocol=mcfg.get("protocol", "openai"),
                )

        # Resolve active model config
        active_cfg = models.get(active_model, ProviderConfig())
        if not active_cfg.api_key:
            raise ValueError(
                f"Active model '{active_model}' has no api_key configured. "
                f"Available models: {list(models.keys())}"
            )

        retry_data = data.get("retry", {})
        retry_config = RetryConfig(
            enabled=retry_data.get("enabled", True),
            max_retries=retry_data.get("max_retries", 3),
        )

        llm_config = LLMConfig(
            api_key=active_cfg.api_key,
            api_base=active_cfg.api_base or None,
            model=active_cfg.model or "claude-sonnet-4-6",
            provider=active_cfg.provider or active_model,
            max_input_tokens=active_cfg.max_input_tokens or 200000,
            protocol=active_cfg.protocol or "openai",
            retry=retry_config,
        )

        agent_config = AgentConfig(
            max_steps=data.get("max_steps", 50),
            workspace_dir=data.get("workspace_dir", "./workspace"),
            memories_dir=data.get("memories_dir", "./memories"),
            log_dir=data.get("log_dir", "./logs"),
            sessions_dir=data.get("sessions_dir", "./sessions"),
            system_prompt_path=data.get("system_prompt_path", "system_prompt.md"),
            denied_dirs=data.get("denied_dirs", []),
            external_dirs=data.get("external_dirs", {}),
        )

        tools_data = data.get("tools", {})
        tools_config = ToolsConfig(
            skills_dir=tools_data.get("skills_dir", "./skills"),
            prompts_dir=tools_data.get("prompts_dir", "./prompts"),
            read_file_line_limit=tools_data.get("read_file_line_limit", 2000),
        )

        db_data = data.get("database", {})
        sqlite_data = (
            db_data.get("sqlite", {}) if isinstance(db_data.get("sqlite"), dict) else {}
        )
        mysql_data = (
            db_data.get("mysql", {}) if isinstance(db_data.get("mysql"), dict) else {}
        )
        mysql_pool_data = (
            mysql_data.get("pool", {})
            if isinstance(mysql_data.get("pool"), dict)
            else {}
        )
        db_config = DatabaseConfig(
            type=db_data.get("type", "sqlite"),
            sqlite=SQLiteConfig(path=sqlite_data.get("path", "./data/easy_agent.db")),
            mysql=MySQLConfig(
                host=mysql_data.get("host", "127.0.0.1"),
                port=mysql_data.get("port", 3306),
                user=mysql_data.get("user", "root"),
                password=mysql_data.get("password", ""),
                database=mysql_data.get("database", "easy_agent"),
                charset=mysql_data.get("charset", "utf8mb4"),
                pool=MySQLPoolConfig(
                    pool_size=mysql_pool_data.get("pool_size", 5),
                    max_overflow=mysql_pool_data.get("max_overflow", 10),
                    pool_timeout=mysql_pool_data.get("pool_timeout", 30),
                    pool_recycle=mysql_pool_data.get("pool_recycle", 3600),
                ),
                connect_timeout=mysql_data.get("connect_timeout", 10),
                read_timeout=mysql_data.get("read_timeout", 30),
                write_timeout=mysql_data.get("write_timeout", 30),
            ),
        )

        summ_data = data.get("summarization", {})
        summ_config = SummarizationConfig(
            enabled=summ_data.get("enabled", True),
            compression_threshold=summ_data.get("compression_threshold", 0.8),
            compression_target=summ_data.get("compression_target", 0.1),
        )

        return cls(
            llm=llm_config,
            agent=agent_config,
            tools=tools_config,
            summarization=summ_config,
            database=db_config,
            models=models,
            active_model=active_model,
            preset_questions=data.get("preset_questions", []),
        )

    @staticmethod
    def get_package_dir() -> Path:
        return Path(__file__).parent

    @classmethod
    def find_config_file(cls, filename: str) -> Path | None:
        dev_config = Path.cwd() / "easy_agent" / "config" / filename
        if dev_config.exists():
            return dev_config

        user_config = Path.home() / ".easy-agent" / "config" / filename
        if user_config.exists():
            return user_config

        package_config = cls.get_package_dir() / "config" / filename
        if package_config.exists():
            return package_config

        return None

    @staticmethod
    def sanitize_username(username: str) -> str:
        """将用户名转换为安全的目录名"""
        return "".join(c for c in username if c.isalnum() or c in ("_", "-")) or "user"

    @staticmethod
    def get_user_workspace_dir(username: str) -> Path:
        """获取用户的工作空间目录路径

        Args:
            username: 用户名

        Returns:
            用户工作空间路径，如 workspace/{username}/
        """
        safe_name = Config.sanitize_username(username)
        return Path("./workspace") / safe_name

    @classmethod
    def get_user_mcp_path(cls, username: str, config: "Config | None" = None) -> Path:
        """获取用户的 MCP 配置文件路径：{workspace_dir}/{username}/mcp.json

        文件可能不存在，由调用方判断后决定是否回退到全局 mcp.json。
        """
        safe_name = cls.sanitize_username(username)
        base = Path(config.agent.workspace_dir) if config else Path("./workspace")
        return base / safe_name / "mcp.json"

    @staticmethod
    def get_user_upload_dir(username: str) -> Path:
        """获取用户的上传文件目录路径

        Args:
            username: 用户名

        Returns:
            用户上传目录路径，如 data/uploads/{username}/
        """
        safe_name = Config.sanitize_username(username)
        return Path("./data/uploads") / safe_name

    @classmethod
    def get_default_config_path(cls) -> Path:
        config_path = cls.find_config_file("config.yaml")
        if config_path:
            return config_path

        return cls.get_package_dir() / "config" / "config.yaml"
