"""部署版本信息：来源仓库的分支、commit 等，写在项目根的 ``version.json``。

``version.json`` 由 git hook（``.githooks/``）在每次提交 / 切分支 / 合并后重新生成，
发布打包时再生成一次，随代码一起拷到离线环境。它不入库：文件里要写当前提交的 commit id，
而把它提交进去就会产生新的 commit id。

只用标准库：git hook 与打包脚本在未安装项目依赖的 Python 下也要能运行这个模块。
"""

from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from easy_agent.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

VERSION_FILE = PROJECT_ROOT / "version.json"
UNKNOWN = "unknown"


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

        return version("easy-agent")
    except PackageNotFoundError:
        return UNKNOWN


def _git(args: list[str], cwd: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def collect_from_git(root: Path = PROJECT_ROOT, source: str = "manual") -> dict[str, Any] | None:
    """从本地 git 仓库读出当前版本信息；不是 git 仓库或没有 git 时返回 None。"""
    commit = _git(["rev-parse", "HEAD"], root)
    if not commit:
        return None
    branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], root) or UNKNOWN
    if branch == "HEAD":
        branch = "HEAD (detached)"
    log = _git(["log", "-1", "--format=%cI%n%an%n%s"], root) or ""
    commit_time, author, subject = (log.split("\n", 2) + ["", "", ""])[:3]
    status = _git(["status", "--porcelain", "--untracked-files=no"], root)
    return {
        "name": "easy-agent",
        "version": package_version(),
        "branch": branch,
        "commit": commit,
        "commit_short": commit[:8],
        "commit_time": commit_time,
        "commit_author": author,
        "commit_subject": subject,
        "tag": _git(["describe", "--tags", "--exact-match"], root) or "",
        "remote": _strip_credentials(_git(["remote", "get-url", "origin"], root) or ""),
        "dirty": bool(status),
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": source,
    }


def _strip_credentials(url: str) -> str:
    """去掉 ``https://user:token@host/...`` 里的凭据，避免把 token 写进 version.json。"""
    if "://" in url and "@" in url.split("://", 1)[1].split("/", 1)[0]:
        scheme, rest = url.split("://", 1)
        return f"{scheme}://{rest.split('@', 1)[1]}"
    return url


def write_version_file(path: Path = VERSION_FILE, source: str = "manual") -> dict[str, Any] | None:
    info = collect_from_git(path.parent, source=source)
    if info is None:
        return None
    path.write_text(json.dumps(info, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return info


@lru_cache(maxsize=1)
def get_version_info() -> dict[str, Any]:
    """运行期版本信息：优先读 version.json，没有时回退到本地 git（开发环境），再没有就只给包版本。"""
    if VERSION_FILE.is_file():
        try:
            data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, ValueError) as e:
            logger.warning(f"[版本] version.json 读取失败，回退到 git: {e}")
    info = collect_from_git(source="runtime-git")
    if info is not None:
        return info
    return {
        "name": "easy-agent",
        "version": package_version(),
        "branch": UNKNOWN,
        "commit": UNKNOWN,
        "commit_short": UNKNOWN,
        "source": "none",
    }


def describe_version(info: dict[str, Any] | None = None) -> str:
    """一行可读的版本描述，用于启动日志和系统提示词。"""
    info = info or get_version_info()
    parts = [f"easy-agent {info.get('version') or package_version()}"]
    parts.append(f"分支 `{info.get('branch') or UNKNOWN}`")
    parts.append(f"commit `{info.get('commit_short') or info.get('commit') or UNKNOWN}`")
    if info.get("commit_time"):
        parts.append(f"提交于 {info['commit_time']}")
    if info.get("dirty"):
        parts.append("含未提交改动")
    return "，".join(parts)
