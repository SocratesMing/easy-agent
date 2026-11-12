"""部署版本信息：项目根的 ``version.json``，只有 ``name`` 和 ``version`` 两个字段。

``version`` 是包版本号拼上来源 commit id 的后 6 位，例如 ``0.1.0.c64f43``；不记录分支、作者等
其他 git 信息。文件由 git hook（``.githooks/``）在每次提交 / 切分支 / 合并后重新生成，发布打包时
再生成一次，随代码一起拷到离线环境。它不入库：commit id 要在提交之后才知道。

只用标准库：git hook 与打包脚本在未安装项目依赖的 Python 下也要能运行这个模块。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from functools import lru_cache
from pathlib import Path

from easy_agent.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

VERSION_FILE = PROJECT_ROOT / "version.json"
NAME = "easy-agent"
COMMIT_SUFFIX_LEN = 6


@lru_cache(maxsize=1)
def package_version() -> str:
    """包版本号：取 pyproject.toml，读不到时用已安装包的元数据（utils 层不能导入根包的 ``__version__``）。"""
    try:
        import tomllib

        with (PROJECT_ROOT / "pyproject.toml").open("rb") as f:
            return str(tomllib.load(f)["project"]["version"])
    except (OSError, KeyError, ValueError):
        pass
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version(NAME)
    except PackageNotFoundError:
        return "unknown"


def _head_commit(root: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
            # Windows 下后端（无控制台的服务进程）调用 git 时不弹出命令行窗口
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def build_version(commit: str | None) -> str:
    base = package_version()
    return f"{base}.{commit[-COMMIT_SUFFIX_LEN:]}" if commit else base


def collect_from_git(root: Path = PROJECT_ROOT) -> dict[str, str] | None:
    """按本地 git 的 HEAD 算出版本信息；不是 git 仓库或没有 git 时返回 None。"""
    commit = _head_commit(root)
    if commit is None:
        return None
    return {"name": NAME, "version": build_version(commit)}


def write_version_file(path: Path = VERSION_FILE) -> dict[str, str] | None:
    info = collect_from_git(path.parent)
    if info is None:
        return None
    path.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return info


@lru_cache(maxsize=1)
def get_version_info() -> dict[str, str]:
    """运行期版本信息：优先读 version.json，没有时回退到本地 git（开发环境），再没有就只给包版本号。"""
    if VERSION_FILE.is_file():
        try:
            data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("version"):
                return {"name": str(data.get("name") or NAME), "version": str(data["version"])}
        except (OSError, ValueError) as e:
            logger.warning(f"[版本] version.json 读取失败，回退到 git: {e}")
    return collect_from_git() or {"name": NAME, "version": build_version(None)}
