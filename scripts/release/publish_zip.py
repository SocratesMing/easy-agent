"""打包 easy-agent 发布 zip：源码 + 前端构建产物 + skills + 依赖声明。

源码目录只收集 Git index 中的文件；前端 ``dist`` 是唯一允许从工作区
收集的构建产物。这样构建机上的本地配置、凭据和缓存不会随递归遍历进入发布物。

用法: uv run scripts/release/publish_zip.py
输出: releases/easy-agent-<git-sha>.zip
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "releases"
sys.path.insert(0, str(ROOT))

from easy_agent.utils.version_info import write_version_file  # noqa: E402

# 需要打包的顶层路径（相对 ROOT），支持文件或目录；
# 目录会自动递归收集；文件/目录路径不能重复。
INCLUDE_PATHS = [
    # 部署版本号（末尾为 commit id 前 6 位），main() 打包前重新生成
    "version.json",
    # 后端源码 + 入口
    "main.py",
    "pyproject.toml",
    "uv.lock",
    "start.prod.sh",
    ".python-version",
    # 配置模板
    ".env.prod.example",
    ".env.test.example",
    ".env.dev.example",
    # Docker / 部署
    "Dockerfile",
    ".dockerignore",
    "docker-compose.yml",
    # 源码目录（内含 easy_agent/config/config-example.yaml）
    "easy_agent",
    # 前端构建产物（后端静态托管）
    "frontend-vue2/dist",
    # 运行时技能（7 个）
    "skills",
    # 文档
    "README.md",
    "AGENTS.md",
    "API_KEY_SETUP.md",
    "deploy/README.deploy-223.md",
    "docs/architecture-current.md",
]

# 目录内需要跳过的子路径（相对该目录的任意层级出现即跳过）
SKIP_INSIDE = {
    "easy_agent": {"tests", "__pycache__", "*.pyc", ".pytest_cache"},
    "frontend-vue2/dist": {"*.map"},
    "skills": {"__pycache__", "*.pyc"},
    "docs": {"archive"},
}

# `frontend-vue2/dist` is a build artifact and is intentionally included even
# though `dist/` is ignored by Git.  Every other directory is collected from
# the Git index so a release cannot accidentally contain files from the
# builder's working tree.
GENERATED_INCLUDE_PATHS = {"frontend-vue2/dist"}

# These names are local state rather than release inputs.  Keep this policy in
# the packager instead of relying on `.gitignore`: filesystem walks do not
# consult Git's ignore rules.
LOCAL_ONLY_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".cache",
    "__pycache__",
    "node_modules",
}
LOCAL_ONLY_FILES = {
    ".env",
    ".npmrc",
    ".pypirc",
    "credentials.json",
    "mcp.json",
    "secret.json",
    "secrets.json",
    "settings.local.json",
}


def _git_sha() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "nogit"


def _git_tag() -> str:
    try:
        out = subprocess.check_output(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _should_skip(rel: str) -> bool:
    """根据路径判断是否跳过。rel 是相对 ROOT 的正斜杠路径。"""
    parts = rel.split("/")
    name = parts[-1]

    # Do this check for every include root, including explicitly generated
    # directories.  A local cache or credential file must never be packaged
    # merely because it happens to sit below an allowed directory.
    if any(part in LOCAL_ONLY_DIRS for part in parts):
        return True
    if name in LOCAL_ONLY_FILES:
        return True
    if name.startswith(".env") and not name.endswith(".example"):
        return True
    if name.endswith(".local") or ".local." in name:
        return True
    if name == "config.yaml" or (
        name.startswith("config.")
        and name.endswith(".yaml")
        and name != "config-example.yaml"
    ):
        return True

    for top, skip_set in SKIP_INSIDE.items():
        if parts[0] != top:
            continue
        for skip in skip_set:
            if skip.startswith("*."):
                if parts[-1].endswith(skip[1:]):
                    return True
            elif skip.endswith("__pycache__"):
                if "__pycache__" in parts:
                    return True
            elif skip in parts:
                return True
    return False


def _git_tracked_files() -> set[str] | None:
    """Return paths in the Git index, or ``None`` when ROOT is not a repo."""
    try:
        output = subprocess.check_output(
            ["git", "ls-files", "--cached", "-z"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return {
        path.decode("utf-8")
        for path in output.split(b"\0")
        if path
    }


def _relative(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def _iter_directory_files(path: Path) -> Iterable[Path]:
    for sub in path.rglob("*"):
        if sub.is_file():
            yield sub


def collect_files() -> list[Path]:
    files_by_rel: dict[str, Path] = {}
    tracked = _git_tracked_files()
    for item in INCLUDE_PATHS:
        p = ROOT / item
        if not p.exists():
            print(f"  [warn] 跳过不存在: {item}")
            continue
        if p.is_file():
            rel = _relative(p)
            if not _should_skip(rel) and (tracked is None or rel in tracked):
                files_by_rel[rel] = p
            continue

        # Generated assets are deliberately allowed to come from the working
        # tree.  Source directories use only indexed files; this is what keeps
        # ignored local config and caches out of a release.
        allow_untracked = item in GENERATED_INCLUDE_PATHS
        for sub in _iter_directory_files(p):
            rel = _relative(sub)
            if _should_skip(rel):
                continue
            if not allow_untracked and tracked is not None and rel not in tracked:
                continue
            files_by_rel[rel] = sub
    return [files_by_rel[rel] for rel in sorted(files_by_rel)]


def validate_package_files(files: Iterable[Path]) -> None:
    """Reject a manifest that contains known local-only paths.

    Collection filters these paths already; this second check protects future
    changes to the include list and makes the release boundary testable.
    """
    violations = sorted(_relative(path) for path in files if _should_skip(_relative(path)))
    if violations:
        details = "\n".join(f"  - {path}" for path in violations)
        raise RuntimeError(f"release package contains local-only files:\n{details}")


def sha256_of_zip(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    sha = _git_sha()
    tag = _git_tag()
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    OUTPUT_DIR.mkdir(exist_ok=True)

    zip_name = f"easy-agent-{tag or sha}.zip" if tag else f"easy-agent-{sha}.zip"
    zip_path = OUTPUT_DIR / zip_name

    version_info = write_version_file()
    if version_info is None:
        print("  [warn] 无法读取 git 信息，version.json 未更新")

    print(f"收集文件中... (root={ROOT})")
    files = collect_files()
    validate_package_files(files)
    print(f"  待打包文件: {len(files)}")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in sorted(files):
            arcname = str(f.relative_to(ROOT)).replace("\\", "/")
            zf.write(f, arcname)

    size_mb = zip_path.stat().st_size / 1024 / 1024
    digest = sha256_of_zip(zip_path)

    manifest = {
        "name": zip_name,
        "git_sha": sha,
        "git_tag": tag,
        "version": (version_info or {}).get("version", ""),
        "built_at": ts,
        "files": len(files),
        "size_mb": round(size_mb, 2),
        "sha256": digest,
    }
    manifest_path = OUTPUT_DIR / f"{zip_name}.sha256.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n✅ 打包完成")
    print(f"  zip : {zip_path}")
    print(f"  size: {size_mb:.2f} MB")
    print(f"  sha : {digest}")
    print(f"  git : {tag or sha}")
    print(f"\n解压后：")
    print(f"  cd easy-agent/")
    print(f"  uv sync                               # 装后端依赖")
    print(f"  AGENT_ENV=prod ./start.prod.sh        # 启动（已内置前端 dist）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
