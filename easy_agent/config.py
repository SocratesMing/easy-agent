"""Configuration management module

Provides unified configuration loading and management functionality
"""

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

DEFAULT_APP_WELCOME_TITLE = "Easy Agent，让工作更简单"

# Matches ${VAR} and ${VAR:-default} placeholders inside string values.
_ENV_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")

# 最近一次配置解析中未取到值的占位符变量名（供启动日志提示，避免静默变空串）
_unresolved_env_vars: list[str] = []


def get_missing_env_vars() -> list[str]:
    """返回最近一次 ``Config.from_yaml`` 中未取到值的 ``${VAR}`` 变量名。"""
    return list(_unresolved_env_vars)


def _ensure_project_env_loaded() -> None:
    """解析配置前兜底加载项目根 .env（幂等）。

    ``${VAR}`` 的值来自 ``os.environ``；直接 ``python main.py`` 或 IDE 启动时，
    shell 里 export 的变量常常传不进进程，这里保证无论入口如何都能取到 .env 的值。
    """
    try:
        from .utils.env_loader import load_project_env
    except Exception as e:  # pragma: no cover - 兜底，不影响配置解析
        logger.debug(f"跳过项目根 .env 加载: {e}")
        return
    try:
        load_project_env()
    except Exception as e:
        logger.warning(f"⚠️ 加载项目根 .env 失败（忽略）: {e}")


def _expand_env(value: str, missing: set[str]) -> str:
    """Resolve ${VAR} / ${VAR:-default} placeholders in a single string."""

    def _replace(match: "re.Match[str]") -> str:
        name = match.group(1)
        default = match.group(2)
        env_value = os.environ.get(name)
        if env_value:
            return env_value
        if default is not None:
            return default
        missing.add(name)
        return ""

    return _ENV_VAR_RE.sub(_replace, value)


def _expand_env_recursive(obj: Any, missing: set[str] | None = None) -> Any:
    """Recursively expand env-var placeholders inside parsed YAML data."""
    if missing is None:
        missing = set()
    if isinstance(obj, str):
        return _expand_env(obj, missing)
    if isinstance(obj, dict):
        return {k: _expand_env_recursive(v, missing) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env_recursive(item, missing) for item in obj]
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
    # 未显式配置时的默认上下文窗口（1M）；实际值以各 provider 段配置为准。
    context_length: int = 1_000_000
    protocol: str = "openai"  # "openai" or "anthropic"
    supports_vision: bool = False  # 是否支持视觉/图片输入；False 时自动过滤 image_url 内容块


class LLMConfig(BaseModel):
    """LLM configuration - resolved from the active model selection"""

    api_key: str
    api_base: str | None = None
    model: str = "claude-sonnet-4-6"
    provider: str = "minimax"
    context_length: int = 1_000_000  # Model context window size
    protocol: str = "openai"  # "openai" or "anthropic"
    supports_vision: bool = False  # 是否支持视觉/图片输入
    retry: RetryConfig = Field(default_factory=RetryConfig)


class ToolsConfig(BaseModel):
    skills_dir: str = "./skills"
    prompts_dir: str = "./prompts"

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
    sessions_dir: str = "./sessions"
    prompt_path: str = "prompts"
    """提示词目录（相对配置目录），内含 system.md、fragments/ 与 memory_*.md。

    目录不存在时依次回落：包内 ``easy_agent/config/prompts`` → 内置默认提示词。
    也可用环境变量 EASY_PROMPTS_DIR 指向外部目录覆盖。
    """
    idle_logout_minutes: int = 0
    """登录态空闲超时（分钟）：后端以最近一次接口调用为起点滑动续期，
    前端超过该时长无操作时自动退出到登录页。0 表示永不过期（默认，后台不启用登录超时机制）。"""
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
    sandbox_enabled: bool = True
    """Shell 命令沙箱：Linux 优先使用 bwrap，无法使用 bwrap 时降级 Landlock。

    沙箱仅允许读写工作区、技能和外部目录，并只读访问系统命令库与当前 Python
    环境。Windows 暂无等效沙箱，默认拒绝 execute；仅在可信单用户环境才可设为
    False 直接执行宿主机命令。
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
    app_welcome_title: str = DEFAULT_APP_WELCOME_TITLE

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
        config_path = cls.resolve_config_path()
        if not config_path.exists():
            raise FileNotFoundError(
                "Configuration file not found. Place config.yaml in easy_agent/config/ directory."
            )
        return cls.from_yaml(config_path)

    @classmethod
    def resolve_config_path(
        cls,
        explicit_path: str | Path | None = None,
        config_dir: str | Path | None = None,
    ) -> Path:
        """Resolve the active YAML config without expanding config values."""
        if explicit_path is None:
            explicit_path = os.environ.get("EASY_CONFIG")
        if explicit_path:
            return Path(explicit_path)

        base_dir = Path(config_dir) if config_dir else cls.get_package_dir() / "config"
        agent_env = os.environ.get("AGENT_ENV", "dev").lower()
        if agent_env in ("dev", "test", "prod"):
            candidate = base_dir / f"config.{agent_env}.yaml"
            if candidate.exists():
                return candidate

        dev_candidate = base_dir / "config.dev.yaml"
        if dev_candidate.exists():
            return dev_candidate
        return base_dir / "config.yaml"

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "Config":
        """Load configuration from YAML file

        YAML 中的字符串支持 ``${VAR}`` / ``${VAR:-默认值}`` 占位符，值取自
        ``os.environ``（项目根 .env 会在解析前自动注入），用于不入库的敏感配置。
        未取到值且无默认值的变量按空串处理，变量名记录在 ``get_missing_env_vars()``。
        """
        global _unresolved_env_vars
        _unresolved_env_vars = []

        config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file does not exist: {config_path}")

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError("Configuration file is empty")

        # Expand ${ENV_VAR} / ${ENV_VAR:-default} placeholders (e.g. api_key, password).
        _ensure_project_env_loaded()
        missing: set[str] = set()
        data = _expand_env_recursive(data, missing)
        _unresolved_env_vars = sorted(missing)

        # Parse active model selection
        active_model = data.get("model", "minimax")

        # Parse provider-specific model configs
        models = cls._parse_models(data)

        # Resolve active model config
        llm_config = cls._parse_llm_config(data, models, active_model)

        agent_config = cls._parse_agent_config(data)

        tools_config = cls._parse_tools_config(data)

        db_config = cls._parse_database_config(data)

        summ_config = cls._parse_summarization_config(data)

        return cls(
            llm=llm_config,
            agent=agent_config,
            tools=tools_config,
            summarization=summ_config,
            database=db_config,
            models=models,
            active_model=active_model,
            preset_questions=data.get("preset_questions", []),
            app_welcome_title=data.get(
                "app_welcome_title", DEFAULT_APP_WELCOME_TITLE
            ),
        )

    @staticmethod
    def _parse_models(data: dict) -> dict[str, ProviderConfig]:
        """解析 models 段：每个 provider 的 api_key / model / api_base 等。"""
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
                    context_length=mcfg.get("context_length", 1_000_000),
                    protocol=mcfg.get("protocol", "openai"),
                    supports_vision=mcfg.get("supports_vision", False),
                )
        return models

    @staticmethod
    def _parse_llm_config(data: dict, models: dict, active_model: str) -> LLMConfig:
        """解析当前激活模型为 LLMConfig，并校验 api_key 非空。"""
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

        return LLMConfig(
            api_key=active_cfg.api_key,
            api_base=active_cfg.api_base or None,
            model=active_cfg.model or "claude-sonnet-4-6",
            provider=active_cfg.provider or active_model,
            context_length=active_cfg.context_length or 1_000_000,
            protocol=active_cfg.protocol or "openai",
            supports_vision=active_cfg.supports_vision,
            retry=retry_config,
        )

    @staticmethod
    def _parse_agent_config(data: dict) -> AgentConfig:
        """解析 agent 段（工作目录、记忆目录、提示词路径等）。"""
        return AgentConfig(
            max_steps=data.get("max_steps", 50),
            workspace_dir=data.get("workspace_dir", "./workspace"),
            memories_dir=data.get("memories_dir", "./memories"),
            sessions_dir=data.get("sessions_dir", "./sessions"),
            # 旧字段 system_prompt_path 仅作兼容（值为单文件时按单文件读取）
            prompt_path=data.get("prompt_path")
            or data.get("system_prompt_path")
            or "prompts",
            idle_logout_minutes=data.get("idle_logout_minutes", 0),
            denied_dirs=data.get("denied_dirs", []),
            external_dirs=data.get("external_dirs", {}),
        )

    @staticmethod
    def _parse_tools_config(data: dict) -> ToolsConfig:
        """解析 tools 段（技能目录、提示词目录、读取行数上限）。"""
        tools_data = data.get("tools", {})
        return ToolsConfig(
            skills_dir=tools_data.get("skills_dir", "./skills"),
            prompts_dir=tools_data.get("prompts_dir", "./prompts"),
        )

    @staticmethod
    def _parse_database_config(data: dict) -> DatabaseConfig:
        """解析 database 段（sqlite / mysql 及其连接池参数）。"""
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
        return DatabaseConfig(
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

    @staticmethod
    def _parse_summarization_config(data: dict) -> SummarizationConfig:
        """解析 summarization 段（压缩阈值与目标比例）。"""
        summ_data = data.get("summarization", {})
        return SummarizationConfig(
            enabled=summ_data.get("enabled", True),
            compression_threshold=summ_data.get("compression_threshold", 0.8),
            compression_target=summ_data.get("compression_target", 0.1),
        )

    def ensure_directories(self) -> list[str]:
        """Create all directories referenced by the configuration if missing.

        Covers runtime directories (workspace / memories / logs / sessions),
        tools directories (skills / prompts), the log directory, the SQLite
        database parent directory, and external_dirs host paths. Virtual paths
        (denied_dirs) are intentionally skipped.

        Returns the absolute paths that were created (empty if all existed).
        Failures are swallowed (logged caller-side) so startup is never blocked.
        """
        # (label, path) for every real, on-disk directory in the config.
        candidates: list[tuple[str, str]] = [
            ("workspace", self.agent.workspace_dir),
            ("memories", self.agent.memories_dir),
            ("sessions", self.agent.sessions_dir),
            ("skills", self.tools.skills_dir),
            ("prompts", self.tools.prompts_dir),
        ]

        # SQLite database file -> its parent directory.
        db_path = self.database.sqlite.path
        if db_path:
            db_parent = os.path.dirname(os.path.abspath(db_path))
            if db_parent:
                candidates.append(("sqlite_db_dir", db_parent))

        # external_dirs: virtual prefix -> host real path (create the host side).
        for vhost, host in self.agent.external_dirs.items():
            candidates.append((f"external_dirs[{vhost}]", host))

        created: list[str] = []
        for label, raw in candidates:
            if not raw:
                continue
            p = Path(raw)
            if p.exists():
                continue
            try:
                p.mkdir(parents=True, exist_ok=True)
                created.append(str(p.absolute()))
                logger.debug(f"📁 已创建配置目录 [{label}]: {p.absolute()}")
            except Exception as e:
                logger.warning(f"⚠️ 创建配置目录 [{label}] 失败 {raw}: {e}")

        return created

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

    @classmethod
    def get_user_workspace_dir(cls, username: str, config: "Config | None" = None) -> Path:
        """获取用户的工作空间目录路径

        遵循配置 agent.workspace_dir；未传入 config 时回退到运行期配置
        （get_agent_config()），确保「更改配置工作目录后」查询会话目录文件
        能按配置路径解析。

        Args:
            username: 用户名
            config:   可选 Config 实例；省略则使用运行期配置

        Returns:
            用户工作空间路径，如 {workspace_dir}/{username}/
        """
        safe_name = cls.sanitize_username(username)
        if config is not None:
            base = Path(config.agent.workspace_dir)
        else:
            try:
                # 运行期配置结构为 {"config": Config, "win":..., "agent_env":...}，
                # 真正的目录在 _cfg["config"].agent.workspace_dir（而非顶层 "agent" 字典）。
                # 惰性导入避免与 agent_manager 形成循环依赖。
                from easy_agent.services.agent_manager import get_agent_config
                _cfg = get_agent_config()
                agent_cfg = _cfg.get("config") if _cfg else None
                base = Path(agent_cfg.agent.workspace_dir)
            except Exception:
                base = Path("./workspace")
        return base / safe_name

    @classmethod
    def get_user_mcp_path(cls, username: str, config: "Config | None" = None) -> Path:
        """获取用户的 MCP 配置文件路径：{workspace_dir}/{username}/mcp.json

        文件可能不存在，由调用方判断后决定是否回退到全局 mcp.json。
        """
        safe_name = cls.sanitize_username(username)
        if config is not None:
            base = Path(config.agent.workspace_dir)
        else:
            try:
                from easy_agent.services.agent_manager import get_agent_config
                _cfg = get_agent_config()
                agent_cfg = _cfg.get("config") if _cfg else None
                base = Path(agent_cfg.agent.workspace_dir)
            except Exception:
                base = Path("./workspace")
        return base / safe_name / "mcp.json"

    @classmethod
    def get_user_sessions_dir(cls, username: str, config: "Config | None" = None) -> Path:
        """获取用户的会话日志目录路径：{sessions_dir}/{username}/

        遵循配置 agent.sessions_dir；未传入 config 时回退到运行期配置
        （get_agent_config()）。会话 JSON 日志按用户隔离，与
        workspace/memories 的用户隔离约定一致。

        Args:
            username: 用户名
            config:   可选 Config 实例；省略则使用运行期配置

        Returns:
            用户会话日志目录路径，如 {sessions_dir}/{username}/
        """
        safe_name = cls.sanitize_username(username)
        if config is not None:
            base = Path(config.agent.sessions_dir)
        else:
            try:
                from easy_agent.services.agent_manager import get_agent_config

                _cfg = get_agent_config()
                agent_cfg = _cfg.get("config") if _cfg else None
                base = Path(agent_cfg.agent.sessions_dir)
            except Exception:
                base = Path("./sessions")
        return base / safe_name

    @classmethod
    def get_user_memories_dir(cls, username: str, config: "Config | None" = None) -> Path:
        """获取用户的长期记忆目录路径：{memories_dir}/{username}/

        遵循配置 agent.memories_dir；未传入 config 时回退到运行期配置。
        与 get_user_sessions_dir 对称，按用户隔离。记忆文件约定为
        {memories_dir}/{username}/AGENTS.md。

        Args:
            username: 用户名
            config:   可选 Config 实例；省略则使用运行期配置

        Returns:
            用户记忆目录路径，如 {memories_dir}/{username}/
        """
        safe_name = cls.sanitize_username(username)
        if config is not None:
            base = Path(config.agent.memories_dir)
        else:
            try:
                from easy_agent.services.agent_manager import get_agent_config

                _cfg = get_agent_config()
                agent_cfg = _cfg.get("config") if _cfg else None
                base = Path(agent_cfg.agent.memories_dir)
            except Exception:
                base = Path("./memories")
        return base / safe_name

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
        return cls.resolve_config_path()
