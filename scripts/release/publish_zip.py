"""打包 easy-agent 发布 zip：源码 + 前端构建产物 + skills + 依赖声明。

用法: uv run scripts/release/publish_zip.py
输出: releases/easy-agent-<git-sha>.zip
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "releases"

# 需要打包的顶层路径（相对 ROOT），支持文件或目录；
# 目录会自动递归收集；文件/目录路径不能重复。
INCLUDE_PATHS = [
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


def collect_files() -> list[Path]:
    files: list[Path] = []
    for item in INCLUDE_PATHS:
        p = ROOT / item
        if not p.exists():
            print(f"  [warn] 跳过不存在: {item}")
            continue
        if p.is_file():
            files.append(p)
            continue
        # 目录
        for sub in p.rglob("*"):
            if not sub.is_file():
                continue
            rel = str(sub.relative_to(ROOT)).replace("\\", "/")
            if _should_skip(rel):
                continue
            files.append(sub)
    return files


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

    print(f"收集文件中... (root={ROOT})")
    files = collect_files()
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
