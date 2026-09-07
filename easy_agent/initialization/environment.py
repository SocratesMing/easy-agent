"""Environment initialization isolated from application components."""

from dataclasses import dataclass
import os

from ..utils.env_loader import get_loaded_env_info, load_project_env


@dataclass(frozen=True)
class EnvironmentInitialization:
    env_file: str | None
    loaded_keys: list[str]
    log_dir: str
    agent_env: str
    cors_allow_origins: list[str]


def load_environment() -> EnvironmentInitialization:
    """Load project environment values and expose startup metadata."""
    load_project_env()
    env_file, loaded_keys = get_loaded_env_info()
    cors_raw = os.getenv("EASY_CORS_ALLOW_ORIGINS", "")
    cors_allow_origins = (
        [origin.strip() for origin in cors_raw.split(",") if origin.strip()]
        if cors_raw
        else ["*"]
    )
    return EnvironmentInitialization(
        env_file=env_file,
        loaded_keys=loaded_keys,
        log_dir=os.getenv("EASY_LOG_DIR", ""),
        agent_env=os.getenv("AGENT_ENV", "").lower(),
        cors_allow_origins=cors_allow_origins,
    )
