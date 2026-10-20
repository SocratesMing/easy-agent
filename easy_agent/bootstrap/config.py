"""Configuration management module

Provides unified configuration loading and management functionality.

2026-10-18 批次 1A：Pydantic 模型拆分到 ``config_models.py``，配置键校验与
``${VAR}`` 展开拆分到 ``config_validation.py``；本模块只保留 ``Config``
（加载 / 解析 / 路径 helpers），并 re-export 原有公开名字与兼容私有名，
保证 ``from easy_agent.bootstrap.config import ...`` 的既有导入路径与行为不变。
"""

import logging
import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator

from easy_agent.bootstrap.config_models import (
    DEFAULT_APP_WELCOME_TITLE,
    DEFAULT_WEB_SEARCH_API_URLS,
    AgentConfig,
    DatabaseConfig,
    DistributedLockConfig,
    ExpertTag,
    FileContextConfig,
    LLMConfig,
    MCPToolConfig,
    MySQLConfig,
    MySQLPoolConfig,
    PersonnelConfig,
    PresetQuestionGroup,
    ProviderConfig,
    RetryConfig,
    SQLiteConfig,
    SummarizationConfig,
    ToolsConfig,
    UploadLimitsConfig,
    WebSearchConfig,
)
from easy_agent.bootstrap.config_validation import (
    CONFIG_STRICT_ENV_VAR,
    DEPRECATED_CONFIG_KEYS,
    KNOWN_PROVIDER_KEYS,
    KNOWN_SECTION_KEYS,
    KNOWN_TOP_LEVEL_KEYS,
    _ENV_VAR_RE,
    _ensure_project_env_loaded,
    _expand_env,
    _expand_env_recursive,
    _get_section,
    _set_unresolved_env_vars,
    _unresolved_env_vars,
    find_deprecated_config_keys,
    find_unknown_config_keys,
    get_missing_env_vars,
    validate_config_keys,
)
from easy_agent.bootstrap.env import env_str

logger = logging.getLogger(__name__)


class Config(BaseModel):
    """Main configuration class"""

    llm: LLMConfig
    agent: AgentConfig
    tools: ToolsConfig
    summarization: SummarizationConfig = Field(default_factory=SummarizationConfig)
    web_search: WebSearchConfig = Field(default_factory=WebSearchConfig)
    distributed_lock: DistributedLockConfig = Field(
        default_factory=DistributedLockConfig
    )
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    personnel: PersonnelConfig = Field(default_factory=PersonnelConfig)
    upload_limits: UploadLimitsConfig = Field(default_factory=UploadLimitsConfig)
    file_context: FileContextConfig = Field(default_factory=FileContextConfig)
    """对话附件上传限额（upload_limits 段）；缺失该段时使用默认值 10/5/20。"""
    models: dict[str, ProviderConfig] = Field(default_factory=dict)
    active_model: str = "minimax"
    preset_questions: list[PresetQuestionGroup] = Field(default_factory=list)
    expert_tags: list[ExpertTag] = Field(default_factory=list)
    """专家分类标签候选（创建专家时交给大模型做归类，见 ExpertTag）。"""
    expert_guardrail_enabled: bool = True
    """专家会话是否追加统一的「专家职责边界」块（领域限定 / 拒答无关问题 /
    禁止高危操作 / 事实来源标注）。预置与自建专家都生效；关闭仅去掉这层提示词
    约束，归属校验等硬防线不受影响。"""

    expert_relevance_check_enabled: bool = True
    """专家会话是否在创建 Agent 前做「相关性预检」（一次轻量 LLM 三分类）。

    开启时与本专家职责无关的消息**短路拒答**，Agent 不创建、工具零可达；预检
    异常/超时一律放行（fail-open）。见 services/expert_relevance.py。"""

    expert_relevance_strict: bool = False
    """「部分相关」消息的口径：true = 整条拒答；false（默认）= 放行，交给职责边界块
    只回答其中相关的部分。

    默认宽松是实测调过的：严格口径会把「分析文件里的金融数据出一份报告」「写 Python
    爬利率数据再回答利率问题」这类正常需求整条拒掉。与领域无关的消息、以及删除/修改
    文件等高危操作**不受本开关影响**，始终在预检层短路拒答。"""

    expert_relevance_disable_thinking: bool = True
    """预检的那一次 LLM 调用是否**关闭思考模式**。

    三分类只需输出一行 JSON，思考纯属浪费：实测同一提示词下输出 token 从
    258~1007 降到 7、耗时从 1.7~6.4 秒降到 0.5~0.6 秒。极少数网关不认
    ``reasoning_effort`` / ``thinking`` 参数时会自动去掉它们重试，因此一般无需关闭；
    只有在关思考反而更慢或不返回内容时才设为 false。"""
    app_welcome_title: str = DEFAULT_APP_WELCOME_TITLE

    @field_validator("expert_tags", mode="before")
    @classmethod
    def _normalize_expert_tags(cls, value):
        """兼容两种写法：``[{tag_id, tag_name}]`` 与 ``{tag_id: tag_name}`` 字典。

        同时丢弃 ``tag_id`` / ``tag_name`` 为空的条目，避免配置写错时前端出现
        空白 chip。
        """
        items: list[ExpertTag] = []
        if isinstance(value, dict):
            for key, name in value.items():
                if str(key).strip() and str(name).strip():
                    items.append(
                        ExpertTag(tag_id=str(key).strip(), tag_name=str(name).strip())
                    )
            return items
        if not isinstance(value, list):
            return []
        for item in value:
            if isinstance(item, dict):
                tag_id = str(item.get("tag_id") or item.get("id") or "").strip()
                tag_name = str(item.get("tag_name") or item.get("name") or "").strip()
                if tag_id and tag_name:
                    items.append(ExpertTag(tag_id=tag_id, tag_name=tag_name))
            elif isinstance(item, str) and item.strip():
                # 只给了名字：用名字本身当 tag_id
                items.append(ExpertTag(tag_id=item.strip(), tag_name=item.strip()))
        return items

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
            explicit_path = env_str("EASY_CONFIG")
        if explicit_path:
            return Path(explicit_path)

        # 统一单一配置：只读 config.yaml。环境差异（路径 / 窗口 / 阈值 / 模型名）
        # 一律通过 ${VAR:-默认值} 占位符从 .env.{AGENT_ENV} 注入，不再按 AGENT_ENV
        # 选择不同的 yaml —— config.{dev,test,prod}.yaml 已废弃。
        base_dir = Path(config_dir) if config_dir else cls.get_package_dir() / "config"
        unified = base_dir / "config.yaml"
        if unified.exists():
            return unified
        # Preserve existing knowledge deployments without moving their config/data.
        legacy = base_dir / f"config.{env_str('AGENT_ENV', 'dev').lower()}.yaml"
        return legacy if legacy.exists() else unified

    @classmethod
    def from_yaml(cls, config_path: str | Path) -> "Config":
        """Load configuration from YAML file

        YAML 中的字符串支持 ``${VAR}`` / ``${VAR:-默认值}`` 占位符，值取自
        ``os.environ``（项目根 .env 会在解析前自动注入），用于不入库的敏感配置。
        未取到值且无默认值的变量按空串处理，变量名记录在 ``get_missing_env_vars()``。
        """
        _set_unresolved_env_vars()

        config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file does not exist: {config_path}")

        with open(config_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError("Configuration file is empty")
        if not isinstance(data, dict):
            raise ValueError("Configuration file must contain a YAML mapping")

        # 未知键默认只告警（旧配置兼容）；EASY_CONFIG_STRICT=1 时 fail-fast。
        validate_config_keys(data, source=str(config_path))

        # Expand ${ENV_VAR} / ${ENV_VAR:-default} placeholders (e.g. api_key, password).
        _ensure_project_env_loaded()
        missing: set[str] = set()
        data = _expand_env_recursive(data, missing)
        # Read legacy model context limits without changing the stored YAML.
        for model in [data.get("llm", {}), *data.get("models", {}).values()]:
            if isinstance(model, dict) and "context_length" not in model and "max_input_tokens" in model:
                model["context_length"] = model["max_input_tokens"]
        _set_unresolved_env_vars(sorted(missing))

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

        web_search_config = cls._parse_web_search_config(data)

        distributed_lock_config = cls._parse_distributed_lock_config(data)

        upload_limits_config = cls._parse_upload_limits_config(data)

        file_context_config = cls._parse_file_context_config(data)

        # 专家会话安全边界：优先读 expert 段（expert.relevance_strict 等），
        # 同时兼容旧的扁平键（expert_guardrail_enabled），两种写法都能生效。
        expert_section = data.get("expert")
        if not isinstance(expert_section, dict):
            expert_section = {}

        return cls(
            llm=llm_config,
            agent=agent_config,
            tools=tools_config,
            summarization=summ_config,
            web_search=web_search_config,
            distributed_lock=distributed_lock_config,
            database=db_config,
            personnel=PersonnelConfig(**data.get("personnel", {})),
            upload_limits=upload_limits_config,
            file_context=file_context_config,
            models=models,
            active_model=active_model,
            preset_questions=data.get("preset_questions", []),
            expert_tags=data.get("expert_tags", []),
            expert_guardrail_enabled=bool(
                data.get(
                    "expert_guardrail_enabled",
                    expert_section.get("guardrail_enabled", True),
                )
            ),
            expert_relevance_check_enabled=bool(
                data.get(
                    "expert_relevance_check_enabled",
                    expert_section.get("relevance_check_enabled", True),
                )
            ),
            expert_relevance_strict=bool(
                data.get(
                    "expert_relevance_strict",
                    expert_section.get("relevance_strict", False),
                )
            ),
            expert_relevance_disable_thinking=bool(
                data.get(
                    "expert_relevance_disable_thinking",
                    expert_section.get("relevance_disable_thinking", True),
                )
            ),
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
                    headers=dict(mcfg.get("headers") or {}),
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
            headers=dict(active_cfg.headers or {}),
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
            skill_write=data.get("skill_write") or "new-only",
            external_dirs=data.get("external_dirs", {}),
            sandbox_enabled=data.get("sandbox_enabled", True),
            sandbox_network=str(data.get("sandbox_network") or "host"),
            execute_max_timeout=data.get("execute_max_timeout", 600),
            session_thread=data.get("session_thread", False),
        )

    @staticmethod
    def _parse_tools_config(data: dict) -> ToolsConfig:
        """解析 tools 段（技能目录、提示词目录、日志截断长度）。"""
        tools_data = data.get("tools", {})
        return ToolsConfig(
            skills_dir=tools_data.get("skills_dir", "./skills"),
            prompts_dir=tools_data.get("prompts_dir", "./prompts"),
            result_log_truncate=tools_data.get("result_log_truncate", 200),
            experts_dir=tools_data.get("experts_dir", "./experts"),
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
    def _parse_web_search_config(data: dict) -> WebSearchConfig:
        """解析 web_search 段（联网搜索地址 / api_key / 超时等）。

        api_url / api_key 通常写成 ``${WEB_SEARCH_API_URL}`` / ``${WEB_SEARCH_API_KEY}``，
        由 ``_expand_env_recursive`` 提前展开，这里只做字段级默认值兜底。
        """
        ws_data = data.get("web_search", {})
        if not isinstance(ws_data, dict):
            ws_data = {}
        return WebSearchConfig(
            enabled=ws_data.get("enabled", True),
            provider=ws_data.get("provider", "tavily"),
            api_url=ws_data.get("api_url", ""),
            api_key=ws_data.get("api_key", ""),
            search_depth=ws_data.get("search_depth", "basic"),
            topic=ws_data.get("topic", "general"),
            timeout_seconds=ws_data.get("timeout_seconds", 30.0),
            default_time_range=ws_data.get("default_time_range", "NoLimit"),
            max_results=ws_data.get("max_results", 10),
            main_text_max_chars=ws_data.get("main_text_max_chars", 500),
        )

    @staticmethod
    def _parse_distributed_lock_config(data: dict) -> DistributedLockConfig:
        """解析 distributed_lock 段（多实例部署下的分布式锁开关与 TTL）。

        enabled 通常写成 ``${DISTRIBUTED_LOCK_ENABLED:-false}``，由
        ``_expand_env_recursive`` 提前展开（字符串取值由 Pydantic 归一为 bool）。
        """
        dl_data = data.get("distributed_lock", {})
        if not isinstance(dl_data, dict):
            dl_data = {}
        return DistributedLockConfig(
            enabled=dl_data.get("enabled", False),
            key_prefix=dl_data.get("key_prefix", "easy_agent"),
            ttl_seconds=dl_data.get("ttl_seconds", 300.0),
            renew_interval_seconds=dl_data.get("renew_interval_seconds", 100.0),
            retry_interval_seconds=dl_data.get("retry_interval_seconds", 1.0),
        )

    @staticmethod
    def _parse_upload_limits_config(data: dict) -> UploadLimitsConfig:
        """解析 upload_limits 段（对话附件上传限额）。

        缺失该段时使用默认值（10MB / 5 个 / 20MB），保证老配置文件可直接启动。
        配置写歪（例如总量小于单文件上限）时只告警不中断启动：那种组合会让
        "单文件校验通过、组合必然失败"，表现为所有文件都传不上去，需尽早提示。
        """
        ul_data = data.get("upload_limits", {})
        if not isinstance(ul_data, dict):
            ul_data = {}
        limits = UploadLimitsConfig(
            enabled=ul_data.get("enabled", True),
            max_file_size_mb=ul_data.get("max_file_size_mb", 10),
            max_files_per_message=ul_data.get("max_files_per_message", 5),
            max_total_size_mb=ul_data.get("max_total_size_mb", 20),
        )
        if limits.max_total_size_mb < limits.max_file_size_mb:
            logger.warning(
                "⚠️ upload_limits 配置异常：max_total_size_mb(%s) < max_file_size_mb(%s)，"
                "会导致单个文件合法但整体必然超限，请检查 config.yaml",
                limits.max_total_size_mb,
                limits.max_file_size_mb,
            )
        return limits

    @staticmethod
    def _parse_file_context_config(data: dict) -> FileContextConfig:
        """解析 file_context 段（上传文件解析落盘 + 清单记忆）。

        缺失该段时使用默认值，保证老配置文件可直接启动。
        """
        fc_data = data.get("file_context", {})
        if not isinstance(fc_data, dict):
            fc_data = {}
        return FileContextConfig(
            enabled=fc_data.get("enabled", True),
            parse_max_file_size_mb=fc_data.get("parse_max_file_size_mb", 20),
            max_stored_chars=fc_data.get("max_stored_chars", 200000),
            manifest_max_chars=fc_data.get("manifest_max_chars", 2000),
            preview_max_chars=fc_data.get("preview_max_chars", 2000),
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
        # bootstrap/config.py -> easy_agent/  (YAML/prompts live in easy_agent/config/)
        return Path(__file__).resolve().parents[1]

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
                from easy_agent.bootstrap.runtime_config import get_runtime_agent_config
                _cfg = get_runtime_agent_config()
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
                from easy_agent.bootstrap.runtime_config import get_runtime_agent_config
                _cfg = get_runtime_agent_config()
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
                from easy_agent.bootstrap.runtime_config import get_runtime_agent_config

                _cfg = get_runtime_agent_config()
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
                from easy_agent.bootstrap.runtime_config import get_runtime_agent_config

                _cfg = get_runtime_agent_config()
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
