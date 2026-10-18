"""Environment initialization isolated from application components."""

from dataclasses import dataclass
import os
from easy_agent.bootstrap.env import env_str

from easy_agent.utils.env_loader import get_loaded_env_info, load_project_env


@dataclass(frozen=True)
class EnvironmentInitialization:
    env_file: str | None
    loaded_keys: list[str]
    agent_env: str
    cors_allow_origins: list[str]


def load_environment() -> EnvironmentInitialization:
    """Load project environment values and expose startup metadata."""
    load_project_env()
    env_file, loaded_keys = get_loaded_env_info()
    cors_raw = env_str("EASY_CORS_ALLOW_ORIGINS")
    agent_env = env_str("AGENT_ENV").lower()
    cors_allow_origins = (
        [origin.strip() for origin in cors_raw.split(",") if origin.strip()]
        if cors_raw
        else ["*"]
    )
    if agent_env == "prod" and cors_allow_origins == ["*"]:
        raise RuntimeError(
            "生产环境必须设置 EASY_CORS_ALLOW_ORIGINS 为逗号分隔的可信域名，禁止使用 *"
        )
    return EnvironmentInitialization(
        env_file=env_file,
        loaded_keys=loaded_keys,
        agent_env=agent_env,
        cors_allow_origins=cors_allow_origins,
    )
