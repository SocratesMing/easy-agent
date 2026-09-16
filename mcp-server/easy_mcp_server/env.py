"""极简 .env 加载（无第三方依赖）。

与子项目保持零外部依赖：读取 mcp-server/.env 并注入 os.environ，
已存在的环境变量不被覆盖（真实部署环境优先于文件）。
"""

from __future__ import annotations

import os
from pathlib import Path


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        return None
    if line.startswith("export "):
        line = line[len("export ") :].strip()
    key, _, value = line.partition("=")
    key = key.strip()
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return (key, value) if key else None


_LOADED: set[Path] = set()


def load_env(env_file: str | Path | None = None, override: bool = False) -> list[str]:
    """加载 .env，返回被注入的变量名列表（幂等）。

    进程内同一个文件**只真正解析一次**。各个配置 getter（`column_family()`、
    `max_rows()` …）都会调本函数，而它原来是"每次读盘 + 逐行解析"：
    只要在循环里取配置就会退化得很难看——实测 `seed` 生成 4000 行数据因此多花
    了近 50 秒（每行一次文件读）。

    注意：首次加载后不再感知 .env 的文件改动，改配置请重启进程
    （与绝大多数服务的行为一致）。`override=True` 仍会重新注入。
    """
    path = Path(env_file) if env_file else Path(__file__).resolve().parent.parent / ".env"
    if not override and path in _LOADED:
        return []
    _LOADED.add(path)
    if not path.exists():
        return []
    injected: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_line(raw)
        if not parsed:
            continue
        key, value = parsed
        if not override and key in os.environ:
            continue
        os.environ[key] = value
        injected.append(key)
    return injected
