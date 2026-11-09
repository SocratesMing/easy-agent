"""Environment initialization isolated from application components."""

from dataclasses import dataclass
from easy_agent.bootstrap.env import env_str, is_unset_placeholder

from easy_agent.utils.env_loader import get_loaded_env_info, load_project_env

# dev/test 未显式配置 CORS 时只放行本机回环地址，避免默认 ["*"] 的跨域敞口。
LOCAL_CORS_ORIGINS = [
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


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
    agent_env = env_str("AGENT_ENV", "dev").lower()
    cors_allow_origins = (
        [origin.strip() for origin in cors_raw.split(",") if origin.strip()]
        if cors_raw
        else []
    )
    unset = not cors_allow_origins or any(
        is_unset_placeholder(origin) for origin in cors_allow_origins
    )
    if agent_env == "prod":
        # 收紧混合写法：https://a.example.com,* 也不能绕过生产校验。
        if unset or "*" in cors_allow_origins:
            raise RuntimeError(
                "生产环境必须设置 EASY_CORS_ALLOW_ORIGINS 为逗号分隔的可信域名，"
                "禁止使用 *、模板占位值或留空"
            )
    elif unset:
        # dev/test：模板写哨兵时由代码生成安全默认值，占位值不进入 CORS 来源。
        cors_allow_origins = list(LOCAL_CORS_ORIGINS)
    return EnvironmentInitialization(
        env_file=env_file,
        loaded_keys=loaded_keys,
        agent_env=agent_env,
        cors_allow_origins=cors_allow_origins,
    )
