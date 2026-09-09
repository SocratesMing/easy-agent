"""Runtime initialization orchestration."""

from dataclasses import dataclass
import logging
from pathlib import Path

from ..config import AgentConfig, Config, get_missing_env_vars
from .environment import EnvironmentInitialization, load_environment
from .logging import setup_logging

logger = logging.getLogger(__name__)


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
