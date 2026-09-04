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


def _load_or_create_secret(secret_file: Path = _DEFAULT_SECRET_FILE) -> str:
    """返回稳定的 JWT 签名密钥，保证多 worker 进程共享同一密钥。

    优先级：
    1. EASY_JWT_SECRET 环境变量（推荐，生产环境显式配置）；
    2. 密钥文件（首次生成后持久化），使未设置环境变量时所有 worker 也能
       读到同一密钥，避免 --workers > 1 下各进程随机密钥互相解不开 token。
    """
    env_secret = os.environ.get("EASY_JWT_SECRET")
    if env_secret:
        return env_secret
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


def get_username_from_token(token: str) -> Optional[str]:
    payload = decode_access_token(token)
    if payload:
        return payload.get("sub")
    return None
