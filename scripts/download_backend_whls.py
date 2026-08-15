#!/usr/bin/env python3
"""
下载后端（Python）依赖的 wheel 包，一次下载多个平台/架构。

默认拉取 windows x86_64 与 linux aarch64 两个目标的 wheel；某个包在目标平台
无对应 wheel 时（如 Windows 上的 uvloop、Linux 上的 pywin32）会下载失败，
失败项统一记录到 FAILURES_TXT 指定的 txt 文件。

用法：
  python scripts/download_backend_whls.py

所有参数在下方「配置区」修改，无需命令行传参。复用同级 download_backend_deps.py
的工具函数（uv.lock 解析、pip 平台标签、按制品库层级归档），输出与其 --lock 模式
一致：<OUTPUT_DIR>/cp<py>_<platform>_<arch>/<a>/<b>/<hash>/<file>.whl + index.json
"""

from __future__ import annotations

import sys
import tempfile
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

# 复用同级 download_backend_deps.py 的工具函数
sys.path.insert(0, str(Path(__file__).resolve().parent))
from download_backend_deps import (  # noqa: E402
    repo_root,
    parse_uv_lock,
    parse_uv_lock_wheels,
    normalize_platform,
    normalize_arch,
    normalize_pyver,
    pip_platform_tags,
    organize_whls,
    _pip_download_base,
    subprocess_run,
)


# ============================ 配置区（在此修改） ============================
# uv.lock 文件路径（相对路径基于仓库根目录）
UV_LOCK = "uv.lock"

# 目标 Python 版本，如 "3.12" 或 "312"
PYTHON_VERSION = "3.12"

# 镜像源（pip --index-url）；留空使用默认 PyPI
# 例：清华源 "https://pypi.tuna.tsinghua.edu.cn/simple"
INDEX_URL = ""

# 输出根目录（相对路径基于仓库根目录）
OUTPUT_DIR = "offline_deps/backend"

# 失败记录 txt 文件路径（相对路径基于仓库根目录）
FAILURES_TXT = "offline_deps/backend/download_failures.txt"

# 要下载的目标平台/架构列表，每项为 (platform, arch)
#   platform: linux / windows / macos（别名 win/mac/darwin）
#   arch:     x86 / arm64（别名 x64/amd64/aarch64）
TARGETS = [
    ("windows", "x86"),   # Windows x86_64
    ("linux", "arm64"),   # Linux aarch64
]

# 下载前是否清空各目标目录下的旧 .whl 与 index.json（仅清理该平台子目录，
# 避免旧版本残留干扰离线安装；如需保留旧文件请改为 False）
CLEAR_TARGET_DIR = True
# ===========================================================================


def _resolve(path: str) -> Path:
    """相对路径基于仓库根目录解析。"""
    p = Path(path)
    return p if p.is_absolute() else repo_root() / p


def _clear_target(target_dir: Path) -> None:
    """清空目标目录下的旧 wheel、index.json 及空子目录。"""
    for whl in target_dir.rglob("*.whl"):
        whl.unlink()
    (target_dir / "index.json").unlink(missing_ok=True)
    for p in sorted(target_dir.rglob("*"), reverse=True):
        if p.is_dir() and not any(p.iterdir()) and p != target_dir:
            p.rmdir()


def download_target(platform_in: str, arch_in: str, py: str, specs: list[str],
                    wheel_urls: dict[str, str]) -> tuple[int, list[str]]:
    """下载单个目标平台的全部 wheel，返回 (成功数, 失败 spec 列表)。"""
    platform = normalize_platform(platform_in)
    arch = normalize_arch(arch_in)
    arch_dir = "x86_64" if arch == "x86" else "aarch64"
    plats = pip_platform_tags(platform, arch, None)
    abis = [f"cp{py}", "abi3", "none"]
    target_dir = _resolve(OUTPUT_DIR) / f"cp{py}_{platform}_{arch_dir}"
    target_dir.mkdir(parents=True, exist_ok=True)

    if CLEAR_TARGET_DIR:
        _clear_target(target_dir)

    label = f"{platform}/{arch_dir} cp{py}"
    print(f"\n[{label}] 开始下载 {len(specs)} 个包 (pip --platform {plats})")

    failed: list[str] = []
    ok = 0
    with tempfile.TemporaryDirectory(prefix="whl_raw_", dir=str(target_dir)) as raw:
        raw_dir = Path(raw)
        for spec in specs:
            cmd = _pip_download_base(raw_dir, plats, py, abis, INDEX_URL or None)
            cmd += ["--no-deps", spec]
            r = subprocess_run(cmd)
            if r.returncode == 0:
                ok += 1
            else:
                failed.append(spec)
                print(f"  [FAIL] {spec}")
        meta = {
            "python_version": PYTHON_VERSION,
            "python_tag": py,
            "platform": platform,
            "arch": arch,
            "pip_platform": plats,
            "pip_abi": abis,
            "source": f"uv.lock ({_resolve(UV_LOCK)})",
        }
        organize_whls(target_dir, raw_dir, "", meta,
                      wheel_urls=wheel_urls, skipped=failed)

    print(f"[{label}] 完成: 成功 {ok}，失败 {len(failed)}，输出 {target_dir}")
    return ok, failed


def write_failures(failures: list[tuple[str, str]], path: Path) -> None:
    """把失败项按目标分组写入 txt。failures 为 (label, spec) 列表。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# uv.lock 后端 wheel 下载失败记录",
        f"# 生成时间: {datetime.now(timezone.utc).isoformat()}",
        f"# uv.lock: {_resolve(UV_LOCK)}",
        f"# 镜像源: {INDEX_URL or '默认 PyPI'}",
        f"# Python: {PYTHON_VERSION}  目标: {TARGETS}",
        "",
    ]
    if not failures:
        lines.append("全部下载成功，无失败项。")
    else:
        groups: "OrderedDict[str, list[str]]" = OrderedDict()
        for label, spec in failures:
            groups.setdefault(label, []).append(spec)
        for label, specs in groups.items():
            lines.append(f"[{label}] ({len(specs)})")
            lines.extend(specs)
            lines.append("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    lock_path = _resolve(UV_LOCK)
    if not lock_path.exists():
        print(f"[error] 找不到 uv.lock: {lock_path}", file=sys.stderr)
        return 1

    py = normalize_pyver(PYTHON_VERSION)
    specs = parse_uv_lock(lock_path)
    wheel_urls = parse_uv_lock_wheels(lock_path)
    if not specs:
        print(f"[error] 未能从 {lock_path} 解析到任何依赖", file=sys.stderr)
        return 1

    print("=" * 60)
    print(f"uv.lock: {lock_path}（共 {len(specs)} 个包）")
    print(f"Python: {PYTHON_VERSION} (cp{py})  镜像源: {INDEX_URL or '默认 PyPI'}")
    print(f"目标: {TARGETS}")
    print(f"输出: {_resolve(OUTPUT_DIR)}")
    print("=" * 60)

    failures: list[tuple[str, str]] = []
    total_ok = 0
    for platform_in, arch_in in TARGETS:
        try:
            platform = normalize_platform(platform_in)
            arch = normalize_arch(arch_in)
        except ValueError as e:
            print(f"[error] 目标配置无效 ({platform_in}, {arch_in}): {e}", file=sys.stderr)
            continue
        arch_dir = "x86_64" if arch == "x86" else "aarch64"
        label = f"{platform}/{arch_dir} cp{py}"
        ok, failed = download_target(platform_in, arch_in, py, specs, wheel_urls)
        total_ok += ok
        for spec in failed:
            failures.append((label, spec))

    failures_path = _resolve(FAILURES_TXT)
    write_failures(failures, failures_path)

    print("\n" + "=" * 60)
    print(f"全部完成: 共下载 {total_ok} 个 wheel，失败 {len(failures)} 个")
    print(f"失败记录: {failures_path}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
