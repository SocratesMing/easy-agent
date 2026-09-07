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
    log_config: dict | None
    log_file: str


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
    """Load environment, then initialize logging before app components load."""
    global _initialization
    if _initialization is not None and not force:
        return _initialization

    environment = load_environment()
    config_path = Config.resolve_config_path()
    config: AgentConfig | None = None
    config_error: Exception | None = None
    log_config: dict | None = None
    try:
        config = Config.from_yaml(config_path)
        log_config = config.log.model_dump()
    except Exception as error:
        config_error = error

    log_file = setup_logging(log_config, log_dir=environment.log_dir)
    initialization = RuntimeInitialization(
        environment=environment,
        config_path=config_path,
        config=config,
        config_error=config_error,
        log_config=log_config,
        log_file=log_file,
    )
    _initialization = initialization
    _log_initialization(initialization)
    return initialization


def get_runtime_initialization() -> RuntimeInitialization:
    if _initialization is None:
        return initialize_runtime()
    return _initialization
