#!/usr/bin/env python3
"""
下载前端（Node/npm）依赖的 tarball 包，按 平台 / 架构 分类存放。

原理（与后端脚本同理）：
  在临时目录中执行 `npm install --os <os> --cpu <cpu> --ignore-scripts`，
  让 npm 仅解析并安装目标平台/架构对应的依赖（含平台相关的可选原生包，
  如 @esbuild/linux-arm64 等），随后对每个已安装的包执行 `npm pack`，
  将其还原为 <name>-<version>.tgz 离线包，按 platform/arch 分类输出。

分类目录结构：
  <output>/<platform>/<arch>/
  例如：offline_deps/frontend/linux/x86_64/*.tgz

参数均可自由设置（支持别名），例如：
  --platform   linux | windows | macos   （别名：win / mac / darwin ...）
  --arch       x86 | arm64               （别名：x64 / amd64 / aarch64 ...）
  也可用 --os / --cpu 直接指定 npm 原生参数（跳过自动映射）。

用法示例：
  # Linux x86_64 的前端依赖
  python scripts/download_frontend_deps.py --platform linux --arch x86

  # Windows arm64
  python scripts/download_frontend_deps.py --platform win --arch arm64

  # 直接指定 npm os/cpu（高级用法）
  python scripts/download_frontend_deps.py --os darwin --cpu arm64
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def normalize_platform(p: str) -> str:
    aliases = {
        "linux": "linux", "linux64": "linux", "ubuntu": "linux",
        "windows": "windows", "win": "windows", "win64": "windows",
        "macos": "macos", "mac": "macos", "darwin": "macos",
    }
    key = p.strip().lower()
    if key not in aliases:
        raise ValueError(f"不支持的平台: {p}（支持 linux / windows / macos）")
    return aliases[key]


def normalize_arch(a: str) -> str:
    key = a.strip().lower()
    if key in ("x86", "x86_64", "x64", "amd64"):
        return "x86"
    if key in ("arm64", "aarch64", "arm"):
        return "arm64"
    raise ValueError(f"不支持的架构: {a}（支持 x86 / arm64）")


def npm_target(platform: str, arch: str,
               os_override: str | None, cpu_override: str | None) -> tuple[str, str]:
    """平台/架构 -> npm 的 --os / --cpu 取值。"""
    if os_override and cpu_override:
        return os_override, cpu_override
    os_name = {"linux": "linux", "windows": "win32", "macos": "darwin"}[platform]
    cpu = "arm64" if arch == "arm64" else "x64"
    return os_name, cpu


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="下载前端 npm 依赖（按平台/架构分类，参数可自由设置）"
    )
    parser.add_argument("--platform", default="linux",
                        help="目标操作系统：linux / windows / macos（支持别名）")
    parser.add_argument("--arch", default="x86",
                        help="目标 CPU 架构：x86 / arm64（支持别名）")
    parser.add_argument("--os", default=None,
                        help="可选：直接指定 npm --os（如 linux/win32/darwin），跳过自动映射")
    parser.add_argument("--cpu", default=None,
                        help="可选：直接指定 npm --cpu（如 x64/arm64），跳过自动映射")
    parser.add_argument("--frontend-dir", default=None,
                        help="前端目录（默认 <repo>/frontend）")
    parser.add_argument("--registry", default=None,
                        help="可选：指定 npm registry，例如 https://registry.npmmirror.com")
    parser.add_argument("--output", default=None,
                        help="输出根目录（默认 <repo>/offline_deps/frontend）")
    args = parser.parse_args()

    if shutil.which("npm") is None:
        print("[error] 未找到 npm，请先安装 Node.js / npm。", file=sys.stderr)
        return 1

    try:
        platform = normalize_platform(args.platform)
        arch = normalize_arch(args.arch)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2

    root = repo_root()
    fe_dir = Path(args.frontend_dir) if args.frontend_dir else root / "frontend"
    if not (fe_dir / "package.json").exists():
        print(f"[error] 找不到前端 package.json: {fe_dir}", file=sys.stderr)
        return 1

    arch_dir = "x86_64" if arch == "x86" else "arm64"
    out_dir = (
        Path(args.output)
        if args.output
        else root / "offline_deps" / "frontend"
    ) / platform / arch_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    os_name, cpu = npm_target(platform, arch, args.os, args.cpu)

    print("=" * 60)
    print(f"目标: platform={platform} arch={arch} (npm os={os_name} cpu={cpu})")
    print(f"前端目录: {fe_dir}")
    print(f"输出目录: {out_dir}")
    print("=" * 60)

    with tempfile.TemporaryDirectory(prefix="fe_deps_") as tmp:
        tmp_path = Path(tmp)
        for fname in ("package.json", "package-lock.json"):
            src = fe_dir / fname
            if src.exists():
                shutil.copy(src, tmp_path / fname)

        install_cmd = [
            "npm", "install",
            "--os", os_name,
            "--cpu", cpu,
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ]
        if args.registry:
            install_cmd += ["--registry", args.registry]
        r = run(install_cmd, cwd=tmp_path)
        if r.returncode != 0:
            print(r.stderr, file=sys.stderr)
            print("[error] npm install 失败。", file=sys.stderr)
            return r.returncode

        ls = run(["npm", "ls", "--all", "--parseable", "--prefix", str(tmp_path)])
        pkg_dirs = sorted({
            line.strip()
            for line in ls.stdout.splitlines()
            if line.strip() and Path(line.strip()).is_dir()
        })

        ok = 0
        failed: list[str] = []
        for pkg_dir in pkg_dirs:
            pack = run(
                ["npm", "pack", pkg_dir, "--pack-destination", str(out_dir)],
                cwd=tmp_path,
            )
            if pack.returncode == 0:
                ok += 1
            else:
                failed.append(pkg_dir)

        print(f"\n[ok] 已打包 {ok} 个前端依赖到: {out_dir}")
        if failed:
            print(f"[warn] 以下 {len(failed)} 个包打包失败（已跳过）：", file=sys.stderr)
            for f in failed:
                print("  -", f, file=sys.stderr)
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
