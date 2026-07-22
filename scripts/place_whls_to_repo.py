#!/usr/bin/env python3
"""
扫描指定目录下的所有 .whl，按「制品库（PyPI）路径结构」归档到该目录下的 _repo_layout 子目录。

原理（已通过实测验证）：
  PyPI / Warehouse 的下载 URL 形如
    /packages/<a>/<b>/<hash>/<file>.whl
  其中 <hash> 段 = 文件内容的 **BLAKE2b-256** 摘要
  （hashlib.blake2b(data, digest_size=32).hexdigest()，64 位 hex），
  不是 SHA256。目录按 hash 的前 2 / 再 2 / 剩余 60 位切分。

用法：
  1. 修改下方 WHEELS_DIR 为你的 whl 所在目录（可含子目录）。
  2. 直接运行：
       python scripts/place_whls_to_repo.py
  结果写到 <WHEELS_DIR>/_repo_layout/ 下，并生成 index.json 清单。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from packaging.utils import canonicalize_name, parse_wheel_filename

# ===================== 配置（按需修改） =====================
# 要解析的 .whl 所在目录
WHEELS_DIR = Path("/home/sututu/code/easy-agent/whls")
# 归档子目录名（生成在 WHEELS_DIR 下）
REPO_SUBDIR = "_repo_layout"
# 是否递归扫描子目录
RECURSIVE = True
# 是否移动文件（True=移动并删除源；False=复制，保留源文件）
MOVE = False
# 制品库 base url（写入 index.json 的 artifact_url；留空则用相对 local_path）
ARTIFACT_BASE_URL = ""
# ===========================================================


def blake2b_hex(data: bytes) -> str:
    """PyPI 制品库路径用的摘要：BLAKE2b-256（digest_size=32）。"""
    return hashlib.blake2b(data, digest_size=32).hexdigest()


def repo_rel_path(filename: str, data: bytes) -> str:
    """返回 <a>/<b>/<blake2b>/<filename> 形式的制品库相对路径。"""
    h = blake2b_hex(data)
    return f"{h[:2]}/{h[2:4]}/{h[4:]}/{filename}"


def main() -> int:
    if not WHEELS_DIR.is_dir():
        print(f"[error] 目录不存在: {WHEELS_DIR}", file=sys.stderr)
        return 1

    output_root = WHEELS_DIR / REPO_SUBDIR
    output_root.mkdir(parents=True, exist_ok=True)

    wheels = (
        sorted(WHEELS_DIR.rglob("*.whl"))
        if RECURSIVE
        else sorted(WHEELS_DIR.glob("*.whl"))
    )
    # 排除已归档到 output_root 下的文件，避免二次归档
    wheels = [w for w in wheels if output_root not in w.parents]

    if not wheels:
        print(f"[warn] 在 {WHEELS_DIR} 未找到任何 .whl 文件", file=sys.stderr)
        return 0

    action = "移动" if MOVE else "复制"
    print(f"扫描到 {len(wheels)} 个 whl，开始{action}到: {output_root}")

    packages: list[dict] = []
    errors: list[str] = []
    for whl in wheels:
        try:
            data = whl.read_bytes()
            name, version, _build, _tags = parse_wheel_filename(whl.name)
            folder = canonicalize_name(name)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{whl.name}: 解析失败 ({e})")
            print(f"[warn] 跳过（无法解析 wheel 文件名）: {whl.name} ({e})", file=sys.stderr)
            continue

        rel = repo_rel_path(whl.name, data)
        target = output_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if MOVE:
            shutil.move(str(whl), str(target))
        else:
            shutil.copy2(str(whl), str(target))

        artifact_url = (
            f"{ARTIFACT_BASE_URL.rstrip('/')}/{rel}" if ARTIFACT_BASE_URL else rel
        )
        packages.append({
            "name": folder,
            "version": str(version),
            "filename": whl.name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "blake2b": blake2b_hex(data),
            "local_path": rel,
            "artifact_url": artifact_url,
        })
        print(f"  {action}: {whl.name} -> {rel}")

    packages.sort(key=lambda p: (p["name"], p["version"]))

    index = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": str(WHEELS_DIR),
        "artifact_base_url": ARTIFACT_BASE_URL,
        "count": len(packages),
        "errors": errors,
        "packages": packages,
    }
    index_path = output_root / "index.json"
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"已生成清单: {index_path}（{len(packages)} 个包）")

    if errors:
        print(f"[done] 完成，但有 {len(errors)} 个文件解析失败，详见上方警告。", file=sys.stderr)
        return 2
    print(f"[done] 成功归档 {len(packages)} 个 whl。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
