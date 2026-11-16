"""Runtime initialization orchestration."""

from dataclasses import dataclass
import logging
from pathlib import Path

from easy_agent.bootstrap.config import AgentConfig, Config, get_missing_env_vars
from easy_agent.bootstrap.env import env_bool, env_str
from easy_agent.bootstrap.initialization.environment import EnvironmentInitialization, load_environment
from easy_agent.bootstrap.initialization.log import parse_log_level, set_log_level, setup_logging

logger = logging.getLogger(__name__)

#: 运行期日志级别：debug / info / warning / error，默认 info（log.LOG_LEVEL）。
#: debug 时大模型的请求上下文与原始返回报文会写进 proc 日志（agent/model.py 的 wire_logger）。
#: 可写在 shell 或 .env.{AGENT_ENV} 里；easy-web --log-level 会设置它。
LOG_LEVEL_ENV = "EASY_LOG_LEVEL"
#: 已废弃：大模型报文日志改由 DEBUG 级别控制。
_RETIRED_LLM_LOG_ENV = "EASY_LLM_REQUEST_LOG"


def _apply_log_level_from_env() -> None:
    """加载环境变量之后按 ``EASY_LOG_LEVEL`` 调整级别（.env 文件里的值也能生效）。"""
    raw = env_str(LOG_LEVEL_ENV)
    if raw:
        level = parse_log_level(raw)
        if level is None:
            logger.warning(
                "%s=%r 无效（可选 debug / info / warning / error），保持默认级别", LOG_LEVEL_ENV, raw
            )
        else:
            set_log_level(level)
            logger.info("日志级别: %s（%s）", logging.getLevelName(level), LOG_LEVEL_ENV)
    if env_bool(_RETIRED_LLM_LOG_ENV):
        logger.warning(
            "%s 已不再生效：大模型请求 / 响应报文改为 DEBUG 级别，设置 %s=debug"
            "（或 easy-web --log-level debug）后写进 proc 日志",
            _RETIRED_LLM_LOG_ENV,
            LOG_LEVEL_ENV,
        )


@dataclass(frozen=True)
class RuntimeInitialization:
    environment: EnvironmentInitialization
    config_path: Path
    config: AgentConfig | None
    config_error: Exception | None
    log_files: dict[str, str]


_initialization: RuntimeInitialization | None = None


def _log_initialization(initialization: RuntimeInitialization) -> None:
    environment = initialization.environment
    if environment.env_file:
        logger.info(
            "环境变量文件: %s | 已注入: %s",
            environment.env_file,
            ", ".join(sorted(set(environment.loaded_keys))) or "无",
        )
    else:
        logger.info("环境变量文件: 未找到项目根 .env（可选）")

    if initialization.config_error:
        logger.error(
            "配置文件加载失败: %s | %s",
            initialization.config_path,
            initialization.config_error,
        )
    else:
        logger.info("配置文件加载成功: %s", initialization.config_path)
        missing_env = get_missing_env_vars()
        if missing_env:
            logger.warning(
                "配置中的环境变量占位符未取到值（已按空值处理）: %s",
                ", ".join(missing_env),
            )


def initialize_runtime(force: bool = False) -> RuntimeInitialization:
    """Initialize logging, then load environment and config before app components."""
    global _initialization
    if _initialization is not None and not force:
        return _initialization

    log_files = setup_logging()
    logger.info(
        "运行日志初始化完成 | 运行日志: %s | 错误日志: %s | 通信日志: %s",
        log_files["proc"],
        log_files["err"],
        log_files["comm"],
    )

    environment = load_environment()
    _apply_log_level_from_env()
    config_path = Config.resolve_config_path()
    config: AgentConfig | None = None
    config_error: Exception | None = None
    try:
        config = Config.from_yaml(config_path)
    except Exception as error:
        config_error = error

    initialization = RuntimeInitialization(
        environment=environment,
        config_path=config_path,
        config=config,
        config_error=config_error,
        log_files=log_files,
    )
    _initialization = initialization
    _log_initialization(initialization)
    return initialization


def get_runtime_initialization() -> RuntimeInitialization:
    if _initialization is None:
        return initialize_runtime()
    return _initialization
