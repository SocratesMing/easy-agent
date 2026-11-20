#!/usr/bin/env python3
"""注释卫生基线（只减不增）。

目标：防止「日期戳 / 沿革记录 / 失效文档引用」这类干扰性注释回流。
规则只覆盖最常见的三类噪声，代码语义层面的注释质量仍靠评审把关：

- ``DATE``：注释里出现 ``2026-11-07`` 这类日期戳（变更历史应进提交信息 / docs）；
- ``RATCHET_HISTORY``：``按实测值更新`` / ``口径调整`` / ``合并 xx 分支`` 等沿革叙事；
- ``BROKEN_DOC``：注释引用的 ``docs/**.md`` 文件不存在。

用法::

    python scripts/quality/check_comment_hygiene.py --check   # 门禁：只减不增
    python scripts/quality/check_comment_hygiene.py --write   # 重建基线（有意放宽时）

基线：``scripts/quality/comment_hygiene_baseline.json``，按 文件 → 规则 → 数量 记录。
某文件的违规数超过基线、或出现基线里没有的新违规文件即失败。
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys
import tokenize
from collections import Counter
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

SKIP_DIRS = {
    ".git", ".venv", "node_modules", "dist", "dist-sdk", "build", "vendor",
    "__pycache__", "workspace", "data", "logs", "memories", "sessions",
    "offline_deps", "easy_agent.egg-info", ".pytest_cache", ".ruff_cache",
    ".codebuddy", ".agents", ".claude", ".playwright-cli", "coverage",
}
TARGETS = (
    "easy_agent", "tests", "scripts", "frontend/src", "frontend-vue2/src",
    "frontend-vue2/tests", "frontend-shared", "skills", "e2e", "mcp",
    "mcp-server",
)
EXTS = {".py", ".js", ".mjs", ".cjs", ".ts", ".vue"}

DATE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}|20\d{2}年\d{1,2}月")
RATCHET_RE = re.compile(r"按实测值更新|按实测值校正|口径调整|合并\s*\S{0,12}分支后按实测")
DOC_REF_RE = re.compile(r"docs/[\w./\-\u4e00-\u9fff]+\.md")

# 前端共享目录是生成副本，违规与源文件同步变化，单独计数但不要求先改源
GENERATED_MARKERS = ("frontend/src/shared/", "frontend-vue2/src/packages/easyAgent/shared/")


def iter_files():
    for target in TARGETS:
        base = _ROOT / target
        if not base.exists():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                if os.path.splitext(fn)[1] in EXTS and not fn.endswith(".min.js"):
                    yield Path(dirpath) / fn


def py_comments(path: Path) -> list[str]:
    out = []
    try:
        src = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                body = tok.string.lstrip("#").strip()
                if body and not body.startswith("!"):
                    out.append(body)
    except (tokenize.TokenError, IndentationError):
        pass
    return out


JS_LINE_RE = re.compile(r"^\s*//[ \t]?(.*)$")
JS_BLOCK_RE = re.compile(r"/\*([\s\S]*?)\*/")


def js_comments(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out = []
    for m in JS_BLOCK_RE.finditer(text):
        body = re.sub(r"^\s*\* ?", "", m.group(1), flags=re.M)
        body = " ".join(body.split())
        if body:
            out.append(body)
    body_no_block = JS_BLOCK_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)
    for line in body_no_block.splitlines():
        m = JS_LINE_RE.match(line)
        if not m:
            continue
        body = m.group(1).strip()
        if body and not body.startswith(("http://", "https://", "eslint", "prettier")):
            out.append(body)
    return out


def violations(comments: list[str]) -> Counter:
    c: Counter = Counter()
    for body in comments:
        if DATE_RE.search(body):
            c["DATE"] += 1
        if RATCHET_RE.search(body):
            c["RATCHET_HISTORY"] += 1
        for m in DOC_REF_RE.finditer(body):
            if not (_ROOT / m.group(0)).exists():
                c["BROKEN_DOC"] += 1
    return c


def current_counts() -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for path in iter_files():
        rel = path.relative_to(_ROOT).as_posix()
        ext = path.suffix
        comments = py_comments(path) if ext == ".py" else js_comments(path)
        v = violations(comments)
        if v:
            counts[rel] = {k: n for k, n in sorted(v.items())}
    return dict(sorted(counts.items()))


def check(baseline_path: Path) -> list[str]:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))["files"]
    current = current_counts()
    problems = []
    for rel, rules in sorted(current.items()):
        old = baseline.get(rel, {})
        for rule, n in rules.items():
            if n > old.get(rule, 0):
                problems.append(f"{rel}: {rule} {old.get(rule, 0)} → {n}（只减不增）")
        if not old:
            problems.append(f"{rel}: 基线中不存在的新违规文件 {rules}")
    return problems


def write(baseline_path: Path) -> None:
    data = {
        "comment": "注释卫生基线：按 文件 → 规则 → 数量 记录，只允许下调；"
        "有意放宽时运行 python scripts/quality/check_comment_hygiene.py --write 重建。",
        "files": current_counts(),
    }
    baseline_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="对照基线检查（默认动作）")
    parser.add_argument("--write", action="store_true", help="重建基线文件")
    parser.add_argument(
        "--baseline",
        default=str(_ROOT / "scripts/quality/comment_hygiene_baseline.json"),
    )
    args = parser.parse_args()
    baseline_path = Path(args.baseline)
    if args.write:
        write(baseline_path)
        total = sum(sum(r.values()) for r in json.loads(baseline_path.read_text(encoding="utf-8"))["files"].values())
        print(f"[comment-hygiene] 基线已重建：{baseline_path}（共 {total} 条存量违规）")
        return 0
    problems = check(baseline_path)
    if problems:
        print("[comment-hygiene] FAIL — 注释违规只减不增：")
        for p in problems:
            print(f"  - {p}")
        print("修复方式：精简对应注释；确需保留新噪声时运行 --write 重建基线并在评审中说明。")
        return 1
    print("[comment-hygiene] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
