"""用户认证工具模块"""

import logging
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import bcrypt
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

# 默认的 JWT 密钥持久化路径（data/ 运行时目录，已被 gitignore）。
_DEFAULT_SECRET_FILE = Path("./data/.jwt_secret")


def _current_agent_env() -> str:
    """当前运行环境（AGENT_ENV，缺省 dev），与 initialization 语义一致。"""
    return (os.environ.get("AGENT_ENV") or "dev").strip().lower()


def validate_production_jwt_secret(
    env: str | None = None, secret: str | None = None
) -> None:
    """校验生产环境 JWT 密钥契约（纯函数，无副作用）。

    - ``env`` 缺省读取 ``AGENT_ENV``（缺省 dev）；
    - ``secret`` 缺省读取 ``EASY_JWT_SECRET``；
    - 仅 ``prod`` 强制要求显式密钥：未设置（或仅空白）时抛 ``RuntimeError``，
      阻止启动。生产禁止回退 ``data/.jwt_secret``：多副本/多 worker 各自本地
      文件会导致 JWT 无法互验，且密钥落盘不符合 Secret 管理要求；
    - dev/test 不校验，保留密钥文件回退（单机多 worker 仍可共享同一文件）。

    该函数必须在应用启动期（lifespan）调用，不能在模块 import 时调用，
    否则 pytest / 工具脚本仅 import 就会失败。
    """
    current = (env if env is not None else _current_agent_env()).strip().lower()
    if current != "prod":
        return
    value = secret if secret is not None else os.environ.get("EASY_JWT_SECRET")
    if not (value or "").strip():
        raise RuntimeError(
            "生产环境 (AGENT_ENV=prod) 必须显式设置 EASY_JWT_SECRET；"
            "禁止回退到 data/.jwt_secret：多副本/多 worker 各自本地文件会导致 "
            "JWT 无法互验，且密钥落盘不符合生产密钥管理要求。"
            "请通过部署 Secret 注入 EASY_JWT_SECRET（>=32 字节随机值）。"
        )


def _load_or_create_secret(secret_file: Path = _DEFAULT_SECRET_FILE) -> str:
    """返回稳定的 JWT 签名密钥，保证多 worker 进程共享同一密钥。

    优先级：
    1. EASY_JWT_SECRET 环境变量（推荐，生产环境显式配置）；
    2. 密钥文件（首次生成后持久化），使未设置环境变量时所有 worker 也能
       读到同一密钥，避免 --workers > 1 下各进程随机密钥互相解不开 token。
       **仅 dev/test**：prod 未设置环境变量时不会读写该文件（启动期 fail-fast）。
    """
    env_secret = os.environ.get("EASY_JWT_SECRET")
    if env_secret and env_secret.strip():
        return env_secret
    if _current_agent_env() == "prod":
        # 生产禁止把密钥落到本地 data/.jwt_secret：多副本各自本地文件无法互验，
        # 且密钥落盘不符合 Secret 管理要求。这里返回进程内临时密钥且不写文件，
        # 应用会在 lifespan 的 validate_production_jwt_secret 中拒绝启动。
        logger.error(
            "生产环境 (AGENT_ENV=prod) 未设置 EASY_JWT_SECRET："
            "不会写入 data/.jwt_secret，应用启动将被拒绝。"
        )
        return secrets.token_urlsafe(32)
    try:
        if secret_file.exists():
            stored = secret_file.read_text(encoding="utf-8").strip()
            if stored:
                return stored
        secret = secrets.token_urlsafe(32)
        secret_file.parent.mkdir(parents=True, exist_ok=True)
        secret_file.write_text(secret, encoding="utf-8")
        try:
            os.chmod(secret_file, 0o600)
        except OSError:
            pass
        return secret
    except OSError as exc:
        logger.warning(
            f"无法读写 JWT 密钥文件 {secret_file}，将使用进程内随机密钥；"
            f"多进程部署时请设置 EASY_JWT_SECRET 保证各 worker 密钥一致 | 错误: {exc}"
        )
        return secrets.token_urlsafe(32)


SECRET_KEY = _load_or_create_secret()


def hash_password(password: str) -> str:
    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        password_bytes = plain_password.encode("utf-8")
        hashed_bytes = hashed_password.encode("utf-8")
        return bcrypt.checkpw(password_bytes, hashed_bytes)
    except Exception:
        return False


def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
    never_expires: bool = False,
) -> str:
    to_encode = data.copy()
    now = datetime.utcnow()
    if never_expires:
        to_encode.update({"iat": now})
        return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

    expire = now + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    # iat（签发时间）用于登出时回溯登录时刻；exp 为过期时间
    to_encode.update({"exp": expire, "iat": now})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str, verify_exp: bool = True) -> Optional[dict]:
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=[ALGORITHM],
            options={"verify_exp": verify_exp},
        )
        return payload
    except JWTError:
        return None
