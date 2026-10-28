"""配置键校验与环境变量占位符展开（2026-10-18 批次 1A 从 config.py 拆出）。

本模块**不 import config.py**：``Config.from_yaml`` 调用这里的校验与展开
逻辑，``config.py`` 负责 re-export，保持既有导入路径与行为不变。已知键里能和
``config_models`` 的模型一一对应的部分直接由模型字段生成，不再手写第二份。
"""

import logging
import re
from typing import Any

from pydantic import BaseModel

from easy_agent.bootstrap.config_models import (
    AgentConfig,
    DatabaseConfig,
    DistributedLockConfig,
    FileContextConfig,
    MySQLConfig,
    MySQLPoolConfig,
    PersonnelConfig,
    ProviderConfig,
    RetryConfig,
    SQLiteConfig,
    SummarizationConfig,
    ToolsConfig,
    UploadLimitsConfig,
    WebSearchConfig,
)
from easy_agent.bootstrap.env import env_bool, env_str

# 日志分类沿用 "easy_agent.bootstrap.config"：启动日志与 caplog 过滤按该名称订阅，
# 拆分模块不改变告警归属（见 tests/test_config_validation.py）。
logger = logging.getLogger("easy_agent.bootstrap.config")

# 未知配置键的默认策略是「告警并忽略」，保证旧配置可直接启动；
# 设置 EASY_CONFIG_STRICT=1 后改为 fail-fast（部署/CI 推荐）。
CONFIG_STRICT_ENV_VAR = "EASY_CONFIG_STRICT"


def _fields(model: type[BaseModel]) -> frozenset[str]:
    return frozenset(model.model_fields)


# 顶层键：AgentConfig 的字段（agent 配置写在顶层）、Config.from_yaml() 读取的各段，
# 以及兄弟模块直接消费的段（knowledge 由 knowledges.core.config 读取；log/log_dir 为历史遗留键）。
KNOWN_TOP_LEVEL_KEYS = _fields(AgentConfig) | frozenset(
    {
        "model",
        "models",
        "llm",  # 旧版单模型写法，已不再读取，出现时只告警（见 DEPRECATED_CONFIG_KEYS）
        "retry",
        "system_prompt_path",  # 旧键，兼容
        "summarization",
        "tools",
        "web_search",
        "preset_questions",
        "database",
        "personnel",
        "distributed_lock",
        "upload_limits",
        "file_context",
        "expert",
        "expert_tags",
        "expert_guardrail_enabled",
        "expert_relevance_check_enabled",
        "expert_relevance_strict",
        "expert_relevance_disable_thinking",
        "expert_auto_install_required_skills",
        "expert_tool_scope_enabled",
        "app_welcome_title",
        "knowledge",
        "log",  # 历史遗留，Config 不再读取
        "log_dir",  # 历史遗留，Config 不再读取
    }
)

# models.<provider> 下允许的键（provider 名本身是动态的）；max_input_tokens 是旧键，
# from_yaml 会映射为 context_length。
KNOWN_PROVIDER_KEYS = _fields(ProviderConfig) | frozenset({"max_input_tokens"})

# 关键配置节及其允许的直接子键。点号表示嵌套节。
KNOWN_SECTION_KEYS: dict[str, frozenset[str]] = {
    "retry": _fields(RetryConfig),
    "tools": _fields(ToolsConfig),
    "database": _fields(DatabaseConfig),
    "database.sqlite": _fields(SQLiteConfig),
    "database.mysql": _fields(MySQLConfig),
    "database.mysql.pool": _fields(MySQLPoolConfig),
    "summarization": _fields(SummarizationConfig),
    "web_search": _fields(WebSearchConfig),
    "distributed_lock": _fields(DistributedLockConfig),
    "personnel": _fields(PersonnelConfig),
    "upload_limits": _fields(UploadLimitsConfig),
    "file_context": _fields(FileContextConfig),
    # 以下各段与模型字段名不同（expert 段对应 Config.expert_*）或不归 Config 管（knowledge）
    "expert": frozenset(
        {
            "guardrail_enabled",
            "relevance_check_enabled",
            "relevance_strict",
            "relevance_disable_thinking",
            "auto_install_required_skills",
            "tool_scope_enabled",
        }
    ),
    "knowledge": frozenset(
        {
            "enabled",
            "base_url",
            "api_key",
            "shared_dataset_id",
            "embedding",
            "audit",
            "limits",
            "source_sync",
        }
    ),
    "knowledge.embedding": frozenset({"model"}),
    "knowledge.audit": frozenset({"enabled", "max_details_bytes"}),
    "knowledge.limits": frozenset(
        {
            "max_file_size_mb",
            "max_request_size_mb",
            "max_filename_utf8_bytes",
            "allowed_extensions",
            "max_documents_per_query",
            "parse_timeout_seconds",
        }
    ),
    "knowledge.source_sync": frozenset(
        {
            "enabled",
            "owner_username",
            "file_root",
            "base_id",
            "base_name",
            "lookback_days",
            "start_date",
            "interval_minutes",
            "max_inflight",
            "poll_interval_seconds",
            "max_attempts",
            "mysql",
        }
    ),
    "knowledge.source_sync.mysql": frozenset(
        {"host", "port", "user", "password", "database", "charset", "table"}
    ),
    "llm": frozenset(
        {
            "provider",
            "api_key",
            "model",
            "api_base",
            "context_length",
            "max_input_tokens",
            "protocol",
            "headers",
        }
    ),
}

# models.<provider> 下允许的键（provider 名本身是动态的）。
KNOWN_PROVIDER_KEYS = frozenset(
    {
        "provider",
        "api_key",
        "model",
        "api_base",
        "context_length",
        "max_input_tokens",  # 旧键，from_yaml 会映射为 context_length
        "protocol",
        "headers",
    }
)

# 已废弃但仍兼容的键 -> 提示信息。不参与 unknown 判定，也不参与
# config.yaml / config-example.yaml 的键集合漂移比较。
DEPRECATED_CONFIG_KEYS: dict[str, str] = {
    "llm": "llm 为旧版单模型写法，已不再读取：当前模型由 models + model 决定，可删除该段",
    "system_prompt_path": "system_prompt_path 已由 prompt_path 取代，当前仍兼容读取",
    "log": "log 段不再由 Config 读取（日志由 initialization/log.py 管理）",
    "log_dir": "log_dir 不再由 Config 读取（日志由 initialization/log.py 管理）",
}


def _get_section(data: dict, path: str) -> dict | None:
    """按点号路径取一个 mapping 节；不存在或类型不符时返回 None。"""
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current if isinstance(current, dict) else None


def find_unknown_config_keys(data: dict) -> list[str]:
    """返回配置中不在已知 schema 内的键（点号路径，已排序）。

    只检查顶层键与关键配置节（含嵌套节、models.<provider>）；列表/字典
    动态内容（preset_questions、external_dirs、headers 等）不做键名枚举。
    """
    if not isinstance(data, dict):
        return ["<root>"]

    unknown: set[str] = set()
    for key in data:
        if key not in KNOWN_TOP_LEVEL_KEYS:
            unknown.add(str(key))

    for section_path, allowed in KNOWN_SECTION_KEYS.items():
        section = _get_section(data, section_path)
        if section is None:
            continue
        for key in section:
            if key not in allowed:
                unknown.add(f"{section_path}.{key}")

    models = data.get("models")
    if isinstance(models, dict):
        for provider_name, provider_cfg in models.items():
            if not isinstance(provider_cfg, dict):
                continue
            for key in provider_cfg:
                if key not in KNOWN_PROVIDER_KEYS:
                    unknown.add(f"models.{provider_name}.{key}")

    return sorted(unknown)


def find_deprecated_config_keys(data: dict) -> list[str]:
    """返回实际出现（值为非 None）的已废弃键。"""
    found: list[str] = []
    for path in DEPRECATED_CONFIG_KEYS:
        if "." in path:
            parent_path, key = path.rsplit(".", 1)
            section = _get_section(data, parent_path)
            present = bool(section) and key in section
        else:
            present = isinstance(data, dict) and path in data
        if present:
            found.append(path)
    return found


def validate_config_keys(
    data: dict,
    *,
    strict: bool | None = None,
    source: str | None = None,
) -> list[str]:
    """检测未知/废弃配置键；返回未知键列表。

    - 默认（strict=False）：未知键仅 WARNING，旧配置保持可启动；
    - ``strict=True`` 或环境变量 ``EASY_CONFIG_STRICT=1``：未知键抛
      ``ValueError``，用于部署自检 / CI；
    - 废弃键始终只 WARNING（兼容旧配置，不阻断启动）。
    """
    unknown = find_unknown_config_keys(data)
    if unknown:
        where = f"（{source}）" if source else ""
        message = (
            f"配置包含未知键{where}: {', '.join(unknown)}；已忽略。"
            f"如需启动失败请设置 {CONFIG_STRICT_ENV_VAR}=1"
        )
        if strict is None:
            strict = env_bool(CONFIG_STRICT_ENV_VAR, False)
        if strict:
            raise ValueError(message)
        logger.warning("⚠️ %s", message)

    for path in find_deprecated_config_keys(data):
        logger.warning("⚠️ 配置键 %s 已废弃：%s", path, DEPRECATED_CONFIG_KEYS[path])

    return unknown

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
        from easy_agent.utils.env_loader import load_project_env
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
        env_value = env_str(name, strip=False)  # 密钥等值原样替换，不 strip
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


def _set_unresolved_env_vars(names: list[str] | None = None) -> None:
    """原地更新最近一次解析的未解析变量名。

    ``Config.from_yaml`` 解析前清空、结束后写入排序结果。这里用切片赋值而
    不是重新绑定，保证 re-export 到 ``config.py`` 的 ``_unresolved_env_vars``
    引用始终指向同一个列表对象。
    """
    _unresolved_env_vars[:] = names or []
