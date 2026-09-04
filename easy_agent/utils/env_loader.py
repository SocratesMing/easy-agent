"""项目根 .env 文件加载（无第三方依赖）。

配置中的 ``${ENV_VAR}`` 占位符（如 ``database.mysql.password``）依赖**进程**环境
变量。直接 ``python main.py`` 或通过 IDE 启动时，shell 里 export 的变量常常传不进
进程，占位符会被解析成空字符串，典型症状是 MySQL 报 1045
``Access denied ... (using password: NO)`` —— 并不是密码错误，而是密码没传进来。

本模块在应用启动早期把项目根 ``.env`` 中的键值注入 ``os.environ``，与启动方式无关。
默认**不覆盖**已有的环境变量（真实环境变量优先），设置 ``EASY_ENV_OVERRIDE=1`` 可反转。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# easy_agent/utils/env_loader.py -> easy_agent -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[2]

_loaded = False
_env_file: Path | None = None
_env_keys: list[str] = []


def get_loaded_env_info() -> tuple[str | None, list[str]]:
    """返回已加载的 .env 路径与变量名列表（不含值），供启动日志展示。"""
    return (str(_env_file) if _env_file else None, list(_env_keys))


def _parse_line(line: str) -> tuple[str, str] | None:
    """解析一行 KEY=VALUE，支持 export 前缀与引号包裹。"""
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export ") :].strip()
    key, sep, value = line.partition("=")
    if not sep:
        return None
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    # 去掉成对引号（密码中的 #、空格等特殊字符请用引号包裹）
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def load_project_env(override: bool | None = None) -> list[str]:
    """加载 .env 到 os.environ。

    返回实际写入的**变量名**列表（不含值，避免密钥进日志）。函数幂等。
    """
    global _loaded, _env_file, _env_keys
    if _loaded:
        return list(_env_keys)
    _loaded = True

    if override is None:
        override = os.environ.get("EASY_ENV_OVERRIDE", "") == "1"

    env_file = os.environ.get("EASY_ENV_FILE")
    if env_file:
        candidates = [Path(env_file)]
    else:
        candidates = [PROJECT_ROOT / ".env", Path.cwd() / ".env"]

    path = next((p for p in candidates if p.is_file()), None)
    if path is None:
        logger.debug("未找到项目根 .env（可选），跳过加载")
        return []
    _env_file = path

    loaded: list[str] = []
    try:
        with open(path, encoding="utf-8") as f:
            for raw in f:
                parsed = _parse_line(raw)
                if not parsed:
                    continue
                key, value = parsed
                if key in os.environ and not override:
                    continue
                os.environ[key] = value
                loaded.append(key)
    except OSError as e:
        logger.warning(f"读取 .env 失败: {path} | {e}")
        return []

    _env_keys = loaded
    # 注：此处 INFO 通常不可见（调用发生在日志初始化之前），
    # 启动日志中的"环境变量文件"一行由 app.py 在日志就绪后补打。
    logger.debug(f"已加载 .env: {path} | 变量: {', '.join(sorted(set(loaded)))}")
    return loaded
