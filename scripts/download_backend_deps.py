#!/usr/bin/env python3
"""
下载后端（Python）依赖的 wheel 包，按 平台 / 架构 / Python 版本 分类存放，
并按「制品库文件夹层级」归档，同时生成 JSON 清单（含每个包在制品库的 URL）。

制品库层级（还原 PyPI/制品库 CDN 的 URL 路径结构）：
  <output>/cp<py>_<platform>_<arch>/<a>/<b>/<hash>/<文件>.whl
  例如（对应 uv.lock 中的 wheel url .../packages/1c/f5/bf75.../pymupdf-...whl）：
    offline_deps/backend/cp311_linux_aarch64/1c/f5/bf75.../pymupdf-1.28.0-cp310-abi3-manylinux_2_28_aarch64.whl
  即根目录 cp<py>_<platform>_<arch>，其下按 uv.lock 里 wheel url 中 "/packages/" 之后的
  层级（哈希目录）递归还原，wheel 放入最末层目录。

生成的 JSON 清单（<output>/cp<py>_<platform>_<arch>/index.json）示例：
  {"target":{...}, "artifact_base_url":"...", "count":N,
   "packages":[{"name","version","filename","local_path","artifact_url"}, ...]}

依赖来源（二选一）：
  - 默认：从 pyproject.toml 解析（直接依赖 + 可选分组）
  - --lock <uv.lock>：读取 uv.lock 中的【全部】包（含所有传递依赖的锁定版本），
                      并逐包下载（单个包缺对应平台 wheel 时跳过而不中断整体）

参数均可自由设置（支持别名），例如：
  --platform        linux | windows | macos   （别名：win / mac / darwin ...）
  --arch            x86 | arm64               （别名：x64 / amd64 / aarch64 ...）
  --python-version  3.12 | 312                （任意 3.x）
  --artifact-base-url  制品库基础地址，用于拼装每个包的 artifact_url

用法示例：
  # 升级并下载（uv.lock 全量，Windows x86 / cp312）
  uv lock --upgrade
  python scripts/download_backend_deps.py --lock uv.lock --platform win --arch x86 --python-version 3.12
"""

from __future__ import annotations

import argparse
import json
import shutil
import hashlib
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from packaging.utils import canonicalize_name, parse_wheel_filename


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_toml(path: Path) -> dict:
    import tomllib
    with open(path, "rb") as f:
        return tomllib.load(f)


def parse_pyproject_deps(pyproject: Path, extras: list[str]) -> list[str]:
    data = _load_toml(pyproject)
    project = data.get("project", {})
    deps: list[str] = list(project.get("dependencies", []))
    optional = project.get("optional-dependencies", {})
    for extra in extras:
        if extra not in optional:
            print(f"[warn] pyproject 中不存在可选分组: {extra}", file=sys.stderr)
            continue
        deps.extend(optional[extra])
    return deps


def parse_uv_lock(lock_path: Path) -> list[str]:
    """读取 uv.lock，返回全部【registry 来源】包的 name==version 列表（跳过 git/workspace/url 源）。"""
    data = _load_toml(lock_path)
    specs: list[str] = []
    for pkg in data.get("package", []):
        src = pkg.get("source")
        if isinstance(src, dict) and "registry" not in src:
            # git / url / workspace / editable 等非 PyPI 源跳过
            continue
        name = pkg.get("name")
        ver = pkg.get("version")
        if name and ver:
            specs.append(f"{name}=={ver}")
    return specs


def parse_uv_lock_wheels(lock_path: Path) -> dict[str, str]:
    """读取 uv.lock，返回 {wheel文件名: wheel下载url} 映射（覆盖全部平台的 wheel）。"""
    data = _load_toml(lock_path)
    mapping: dict[str, str] = {}
    for pkg in data.get("package", []):
        for w in pkg.get("wheels", []) or []:
            url = w.get("url")
            if not url:
                continue
            filename = url.rsplit("/", 1)[-1]
            mapping[filename] = url
    return mapping


def url_repo_path(url: str) -> str:
    """从 wheel url 提取制品库层级路径（"/packages/" 之后的部分，含哈希目录与文件名）。"""
    path = urlsplit(url).path
    marker = "/packages/"
    i = path.find(marker)
    if i >= 0:
        return path[i + len(marker):]
    return path.lstrip("/")


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


def normalize_pyver(v: str) -> str:
    v = v.strip().lower()
    if v.isdigit():  # 312
        return v
    return "".join(v.split(".")[:2])  # 3.12 -> 312


def pip_platform_tags(platform: str, arch: str, platform_tag: str | None = None) -> list[str]:
    """返回一组 pip --platform 候选标签，按从新到旧排列以最大化 wheel 覆盖。

    较新的包常只发布 manylinux_2_28/2_31/2_38 标签，而请求的基线
    manylinux2014(=2.17) 不会向下兼容选取它们，因此同时给出多档标签。
    """
    if platform_tag:
        return [platform_tag]
    if platform == "windows":
        return ["win_arm64"] if arch == "arm64" else ["win_amd64"]
    if platform == "macos":
        return ["macosx_11_0_arm64"] if arch == "arm64" else ["macosx_10_9_x86_64"]
    if arch == "arm64":
        return [
            "manylinux_2_38_aarch64",
            "manylinux_2_31_aarch64",
            "manylinux_2_28_aarch64",
            "manylinux2014_aarch64",
        ]
    return [
        "manylinux_2_38_x86_64",
        "manylinux_2_31_x86_64",
        "manylinux_2_28_x86_64",
        "manylinux2014_x86_64",
    ]


def organize_whls(
    artifact_root: Path,
    raw_dir: Path,
    base_url: str,
    meta: dict,
    wheel_urls: dict[str, str] | None = None,
    skipped: list[str] | None = None,
) -> tuple[Path, list[dict]]:
    """把下载好的 wheel 按制品库层级归档。

    优先使用 uv.lock 里该 wheel 的真实下载 url，还原 "/packages/" 之后的层级
    （<a>/<b>/<hash>/<文件>.whl）；找不到 url 时退化为 <包名>/<版本>/<文件>.whl。
    """
    wheel_urls = wheel_urls or {}
    packages: list[dict] = []
    for whl in sorted(raw_dir.glob("*.whl")):
        try:
            name, version, _build, _tags = parse_wheel_filename(whl.name)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] 无法解析 wheel 文件名，已跳过: {whl.name} ({e})", file=sys.stderr)
            continue
        folder = canonicalize_name(name)
        src_url = wheel_urls.get(whl.name)
        if src_url:
            rel = url_repo_path(src_url)
        else:
            # 退化策略：PyPI/制品库路径 hash 段是「文件内容的 BLAKE2b-256 摘要」
            # （digest_size=32，非 SHA256），按 前2/再2/剩余60 切分目录。
            digest = hashlib.blake2b(whl.read_bytes(), digest_size=32).hexdigest()
            rel = f"{digest[:2]}/{digest[2:4]}/{digest[4:]}/{whl.name}"
            print(f"[warn] uv.lock 中未找到 url，按文件 BLAKE2b 计算制品库路径: {whl.name}", file=sys.stderr)
        target = artifact_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(whl), str(target))
        if base_url:
            artifact_url = f"{base_url.rstrip('/')}/{rel}"
        elif src_url:
            artifact_url = src_url
        else:
            artifact_url = rel
        packages.append({
            "name": folder,
            "version": str(version),
            "filename": whl.name,
            "local_path": rel,
            "artifact_url": artifact_url,
        })
    packages.sort(key=lambda p: (p["name"], p["version"]))

    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": meta,
        "artifact_base_url": base_url,
        "count": len(packages),
        "packages": packages,
        "skipped": skipped or [],
    }
    index_path = artifact_root / "index.json"
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    return index_path, packages


def _pip_download_base(raw_dir: Path, plats: list[str], py: str, abis: list[str], index_url: str | None) -> list[str]:
    cmd = [
        sys.executable, "-m", "pip", "download",
        "-d", str(raw_dir),
        "--only-binary=:all:",
        "--python-version", py,
        "--implementation", "cp",
    ]
    for plat in plats:
        cmd += ["--platform", plat]
    for abi in abis:
        cmd += ["--abi", abi.strip()]
    if index_url:
        cmd += ["--index-url", index_url]
    return cmd


def subprocess_run(cmd: list[str]) -> "object":
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="下载后端 wheel 依赖并按制品库层级归档（生成 JSON 清单）"
    )
    parser.add_argument("--python-version", default="3.12",
                        help="目标 Python 版本，如 3.12 或 312（默认 3.12）")
    parser.add_argument("--platform", default="linux",
                        help="目标操作系统：linux / windows / macos（支持别名 win/mac/darwin 等）")
    parser.add_argument("--arch", default="x86",
                        help="目标 CPU 架构：x86 / arm64（支持别名 x64/amd64/aarch64 等）")
    parser.add_argument("--platform-tag", default=None,
                        help="可选：直接指定 pip --platform 原始标签，跳过自动映射")
    parser.add_argument("--abi", default=None,
                        help="可选：直接指定 pip --abi（可逗号分隔多个），默认 cp<py>/abi3/none")
    parser.add_argument("--artifact-base-url", default="",
                        help="制品库基础地址，用于拼装每个包的 artifact_url，例如 "
                             "https://nexus.example.com/repository/pypi-hosted")
    parser.add_argument("--lock", default=None,
                        help="uv.lock 路径；提供后下载其中的【全部】包（逐包下载，跳过缺失项）")
    parser.add_argument("--pyproject", default=None,
                        help="pyproject.toml 路径（默认 <repo>/pyproject.toml，仅在无 --lock 时生效）")
    parser.add_argument("--extras", nargs="*", default=[],
                        help="需要额外包含的可选依赖分组（仅在无 --lock 时生效），例如 dev")
    parser.add_argument("--index-url", default=None,
                        help="可选：指定 PyPI 镜像源，例如 https://pypi.tuna.tsinghua.edu.cn/simple")
    parser.add_argument("--output", default=None,
                        help="制品库根目录（默认 <repo>/offline_deps/backend）")
    args = parser.parse_args()

    try:
        platform = normalize_platform(args.platform)
        arch = normalize_arch(args.arch)
        py = normalize_pyver(args.python_version)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2

    root = repo_root()
    plats = pip_platform_tags(platform, arch, args.platform_tag)
    abis = args.abi.split(",") if args.abi else [f"cp{py}", "abi3", "none"]

    # 解析依赖来源
    wheel_urls: dict[str, str] = {}
    if args.lock:
        lock_path = Path(args.lock)
        if not lock_path.exists():
            print(f"[error] 找不到 uv.lock: {lock_path}", file=sys.stderr)
            return 1
        specs = parse_uv_lock(lock_path)
        wheel_urls = parse_uv_lock_wheels(lock_path)
        source_desc = f"uv.lock ({lock_path})"
    else:
        pyproject = Path(args.pyproject) if args.pyproject else root / "pyproject.toml"
        if not pyproject.exists():
            print(f"[error] 找不到 pyproject.toml: {pyproject}", file=sys.stderr)
            return 1
        specs = parse_pyproject_deps(pyproject, args.extras)
        source_desc = f"pyproject.toml ({pyproject})"

    if not specs:
        print(f"[error] 未能从 {source_desc} 解析到任何依赖", file=sys.stderr)
        return 1

    arch_dir = "x86_64" if arch == "x86" else "aarch64"
    artifact_root = (
        Path(args.output)
        if args.output
        else root / "offline_deps" / "backend"
    ) / f"cp{py}_{platform}_{arch_dir}"
    artifact_root.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"目标: platform={platform} arch={arch} python={args.python_version} (py={py})")
    print(f"pip --platform: {plats}  pip --abi: {abis}")
    print(f"依赖来源: {source_desc}（共 {len(specs)} 个包）")
    print(f"制品库根目录: {artifact_root}")
    print("=" * 60)

    meta = {
        "python_version": args.python_version,
        "python_tag": py,
        "platform": platform,
        "arch": arch,
        "pip_platform": plats,
        "pip_abi": abis,
        "source": source_desc,
    }

    ok_count = 0
    fail_count = 0
    skipped_specs: list[str] = []
    with tempfile.TemporaryDirectory(prefix="whl_raw_", dir=str(artifact_root)) as raw:
        raw_dir = Path(raw)
        if args.lock:
            # 逐包下载：单个包缺对应平台 wheel 时跳过，不中断整体
            for spec in specs:
                cmd = _pip_download_base(raw_dir, plats, py, abis, args.index_url)
                cmd += ["--no-deps", spec]
                r = subprocess_run(cmd)
                if r.returncode == 0:
                    ok_count += 1
                else:
                    fail_count += 1
                    skipped_specs.append(spec)
                    print(f"[warn] 跳过（无对应平台 wheel）: {spec}", file=sys.stderr)
        else:
            cmd = _pip_download_base(raw_dir, plats, py, abis, args.index_url)
            cmd += specs
            print("执行命令:", " ".join(cmd[:9]), "... (省略依赖列表)")
            r = subprocess_run(cmd)
            if r.returncode != 0:
                print(r.stderr, file=sys.stderr)
                print("\n[error] pip download 失败，详见上方报错。", file=sys.stderr)
                return r.returncode
            ok_count = len(specs)

        index_path, packages = organize_whls(
            artifact_root, raw_dir, args.artifact_base_url, meta,
            wheel_urls=wheel_urls, skipped=skipped_specs,
        )

    print(f"\n[ok] 已归档 {len(packages)} 个 wheel 到制品库层级: {artifact_root}")
    print(f"[ok] 已生成 JSON 清单: {index_path}")
    if args.lock and fail_count:
        print(f"[info] 目标平台无法获取的包（已跳过）: {fail_count} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
