#!/usr/bin/env python3
"""裸 ``os.getenv`` / ``os.environ.get`` 新增检测（E2）。

统一读取入口是 ``easy_agent.env``（env_str / env_bool / env_int / env_list）。
历史代码里仍有一批裸读，直接改语义风险大，因此用基线冻结：

* 基线内、计数不变的裸读 -> 通过；
* 新增裸读（含同一文件里新增一行）-> 失败；
* 基线里已删除的行 -> 仅提示“基线可收缩”，不阻塞。

用法::

    python scripts/check_env_usage.py --check    # CI 门禁
    python scripts/check_env_usage.py --update   # 开发者新增白名单后刷新基线

白名单文件：``scripts/bare_getenv_whitelist.json``。新增白名单必须写清原因；
能迁移到 ``easy_agent.env`` 的不要再加白名单。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = PROJECT_ROOT / "easy_agent"
BASELINE_PATH = Path(__file__).with_name("bare_getenv_whitelist.json")

# 运行时技能/静态资源不属于后端配置读取，跳过。
SKIP_DIRS = {"__pycache__", "skills", "static"}

PATTERN = re.compile(r"\bos\.(?:getenv|environ\.get)\s*\(")


def iter_occurrences() -> list[tuple[str, int, str]]:
    """返回 (相对路径, 行号, strip 后的源码行)。"""
    occurrences: list[tuple[str, int, str]] = []
    for path in sorted(PACKAGE_DIR.rglob("*.py")):
        rel = path.relative_to(PROJECT_ROOT).as_posix()
        parts = path.relative_to(PACKAGE_DIR).parts
        if any(part in SKIP_DIRS for part in parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            print(f"[WARN] 无法读取 {rel}: {exc}", file=sys.stderr)
            continue
        for lineno, line in enumerate(lines, 1):
            if PATTERN.search(line):
                occurrences.append((rel, lineno, line.strip()))
    return occurrences


def current_baseline() -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = {}
    for rel, _lineno, line in iter_occurrences():
        counts.setdefault(rel, Counter())[line] += 1
    return {rel: dict(counter) for rel, counter in sorted(counts.items())}


def load_baseline() -> dict[str, dict[str, int]]:
    if not BASELINE_PATH.is_file():
        return {}
    raw = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return raw.get("allowed", {})


def compare(
    current: dict[str, dict[str, int]],
    baseline: dict[str, dict[str, int]],
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    stale: list[str] = []
    for rel, entries in current.items():
        for line, count in entries.items():
            allowed = baseline.get(rel, {}).get(line, 0)
            if count > allowed:
                errors.append(
                    f"{rel}: 新增裸 os.getenv/os.environ.get -> {line!r}"
                )
    for rel, entries in baseline.items():
        for line, count in entries.items():
            current_count = current.get(rel, {}).get(line, 0)
            if current_count < count:
                stale.append(f"{rel}: 基线条目可删除 -> {line!r}")
    return errors, stale


def write_baseline(current: dict[str, dict[str, int]]) -> None:
    payload = {
        "_comment": (
            "E2 裸 os.getenv 基线：每条记录文件 + 源码行 + 次数。"
            "新增条目必须写清原因；优先迁移到 easy_agent.env。"
        ),
        "allowed": current,
    }
    BASELINE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="有新增裸读时退出 1")
    parser.add_argument("--update", action="store_true", help="刷新基线文件")
    args = parser.parse_args(argv)

    current = current_baseline()
    if args.update:
        write_baseline(current)
        print(f"[OK] 已刷新基线：{BASELINE_PATH}（{sum(sum(e.values()) for e in current.values())} 处）")
        return 0

    errors, stale = compare(current, load_baseline())
    for item in stale:
        print(f"[INFO] {item}")
    for item in errors:
        print(f"[FAIL] {item}", file=sys.stderr)
    if errors:
        print(
            f"[FAIL] 检测到 {len(errors)} 处新增裸 os.getenv；"
            "请改用 easy_agent.env（env_str/env_bool/env_int/env_list），"
            "确有必要再执行 --update 并说明原因。",
            file=sys.stderr,
        )
        return 1
    print("[OK] 未发现新增裸 os.getenv")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
