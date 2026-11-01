#!/usr/bin/env python3
"""``async def`` 里同步数据库调用的新增检测（事件循环阻塞门禁）。

repository / ``Database`` 的方法都是同步的（SQLite 是一个连接加一把 RLock，MySQL 走连接池），
在 ``async def`` 里直接调用会占住事件循环：一次排队等锁或慢查询，整个进程的 SSE 流都停住。
应改成 ``def`` 路由（FastAPI 放进线程池），或在异步代码里
``await asyncio.to_thread(...)`` / ``await run_blocking(...)``（``easy_agent.utils.blocking``）。

识别规则（静态 AST、按名字匹配，宁可多报，例外进基线）：

* 数据库句柄：名字是 ``db`` / ``database`` / ``repo`` / ``repository``，或以 ``_db`` /
  ``_repo`` / ``_repository`` 结尾，或 ``get_database()``、``XxxRepository(...)`` 的返回值；
* 直接调用：``<句柄>.方法(...)``；
* 间接调用：调用项目里「会碰数据库」的同步函数。函数体里有直接调用就算，调用了其他会碰数据库
  的同步函数也算（按函数名求传递闭包，模块级别名 ``x = 函数名`` 跟着算）；常见的通用方法名
  不参与传递（``GENERIC_NAMES``）；
* lambda 的函数体算在外层：传给同步函数的 lambda 往往当场就被调用；
* 不算：被 await 的调用、``async for`` / ``async with`` 的对象、嵌套的 def / class 里的调用
  （它们在别处执行）、传给 ``to_thread`` / ``run_blocking`` 的函数和 lambda（在线程里执行）。

用法::

    python scripts/quality/check_async_blocking.py --check    # CI 门禁
    python scripts/quality/check_async_blocking.py --list     # 列出当前所有命中
    python scripts/quality/check_async_blocking.py --update   # 刷新基线

基线文件：``scripts/quality/async_blocking_whitelist.json``，按「文件 → ``异步函数() -> 被调用项`` →
次数」记账，行号变化不影响。新增条目必须写清原因；能挪进线程的不要加白名单。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = PROJECT_ROOT / "easy_agent"
BASELINE_PATH = Path(__file__).with_name("async_blocking_whitelist.json")

# 运行时技能/静态资源不是后端服务代码，跳过。
SKIP_DIRS = {"__pycache__", "skills", "static"}

HANDLE_NAME = re.compile(r"^(?:db|database|repo|repository|\w*_db|\w*_repo|\w*_repository)$")
HANDLE_FACTORIES = {"get_database"}

# 这些名字在项目里有会碰数据库的同名函数，但更常见的是 dict / list / 文件 / 锁等对象的方法，
# 按名字传递会把无关调用全算进来；它们只在「句柄.方法(...)」的直接规则里生效。
# acquire / release / locked：数据库分布式锁与 threading / asyncio 锁同名；_run / _execute：
# 工具和执行器里到处都有。
GENERIC_NAMES = {
    "_execute", "_run", "acquire", "add", "append", "clear", "close", "copy", "count",
    "delete", "discard", "extend", "get", "insert", "items", "keys", "list", "load",
    "locked", "pop", "put", "read", "release", "remove", "run", "save", "set",
    "setdefault", "start", "stop", "update", "values", "write",
}

_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
# 把函数放进线程 / 执行器的调用：作为参数传进去的 lambda 在线程里执行
OFFLOAD_CALLS = {"run_blocking", "run_in_executor", "run_in_threadpool", "run_sync", "to_thread"}


def iter_modules(package_dir: Path = PACKAGE_DIR) -> list[tuple[str, ast.Module]]:
    """返回 (相对项目根的路径, AST)；读不了或语法错误的文件打印告警后跳过。"""
    root = package_dir.parent
    modules: list[tuple[str, ast.Module]] = []
    for path in sorted(package_dir.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.relative_to(package_dir).parts):
            continue
        rel = path.relative_to(root).as_posix()
        try:
            modules.append((rel, ast.parse(path.read_text(encoding="utf-8"), filename=rel)))
        except (OSError, SyntaxError) as exc:
            print(f"[WARN] 无法解析 {rel}: {exc}", file=sys.stderr)
    return modules


def _callee_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_handle(expr: ast.expr) -> bool:
    if isinstance(expr, ast.Name):
        return bool(HANDLE_NAME.match(expr.id))
    if isinstance(expr, ast.Attribute):
        # self._db / repo.database 算；config.mysql.database 这类配置字段（字符串）不算
        owner = expr.value
        owner_ok = (isinstance(owner, ast.Name) and owner.id in ("self", "cls")) or _is_handle(owner)
        return owner_ok and bool(HANDLE_NAME.match(expr.attr))
    if isinstance(expr, ast.Call):
        name = _callee_name(expr)
        return name in HANDLE_FACTORIES or name.endswith("Repository")
    return False


def _is_direct_db_call(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and _is_handle(call.func.value)


def _body_nodes(func: ast.FunctionDef | ast.AsyncFunctionDef):
    """函数体里的节点及其父节点。

    不进入嵌套的 def / class（它们在别处执行）。lambda 的函数体算在外层里——传给同步函数的
    lambda 往往当场就被调用；传给 to_thread / run_blocking 等的除外。
    """
    stack: list[ast.AST] = list(func.body)
    parents: dict[ast.AST, ast.AST] = {stmt: func for stmt in func.body}
    while stack:
        node = stack.pop()
        yield node, parents[node]
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _NESTED_SCOPES):
                continue
            if isinstance(child, ast.Lambda):
                owner = parents.get(node) if isinstance(node, ast.keyword) else node
                if isinstance(owner, ast.Call) and _callee_name(owner) in OFFLOAD_CALLS:
                    continue
            parents[child] = node
            stack.append(child)


def db_touching_functions(modules: list[tuple[str, ast.Module]]) -> set[str]:
    """会碰数据库的同步函数名：直接调用句柄，或（按名字传递）调用了这样的函数。

    模块级的 ``别名 = 函数名`` 也算（``load_expert_prompt = _load_expert_prompt`` 这类公开别名）。
    """
    direct: set[str] = set()
    calls: dict[str, set[str]] = {}
    for _rel, tree in modules:
        for stmt in tree.body:
            if (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Name)
            ):
                calls.setdefault(stmt.targets[0].id, set()).add(stmt.value.id)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            callees = calls.setdefault(node.name, set())
            for child, _parent in _body_nodes(node):
                if not isinstance(child, ast.Call):
                    continue
                if _is_direct_db_call(child):
                    direct.add(node.name)
                name = _callee_name(child)
                if name and name not in GENERIC_NAMES and name not in HANDLE_FACTORIES:
                    callees.add(name)
    touching = set(direct)
    changed = True
    while changed:
        changed = False
        for name, callees in calls.items():
            if name not in touching and callees & touching:
                touching.add(name)
                changed = True
    # get_database() 只取进程内单例（初始化在 lifespan 里完成），本身不算阻塞
    return touching - GENERIC_NAMES - HANDLE_FACTORIES


def find_blocking_calls(
    modules: list[tuple[str, ast.Module]],
) -> list[tuple[str, int, str]]:
    """返回 (相对路径, 行号, ``异步函数() -> 被调用项``)。"""
    touching = db_touching_functions(modules)
    found: list[tuple[str, int, str]] = []
    for rel, tree in modules:
        for func in ast.walk(tree):
            if not isinstance(func, ast.AsyncFunctionDef):
                continue
            for node, parent in _body_nodes(func):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(parent, (ast.Await, ast.AsyncFor)) or (
                    isinstance(parent, ast.withitem) and node is parent.context_expr
                ):
                    continue
                if _is_direct_db_call(node) or _callee_name(node) in touching:
                    found.append((rel, node.lineno, f"{func.name}() -> {ast.unparse(node.func)}"))
    return sorted(found)


def current_baseline(modules: list[tuple[str, ast.Module]] | None = None) -> dict[str, dict[str, int]]:
    counts: dict[str, Counter[str]] = {}
    for rel, _lineno, key in find_blocking_calls(modules if modules is not None else iter_modules()):
        counts.setdefault(rel, Counter())[key] += 1
    return {rel: dict(sorted(counter.items())) for rel, counter in sorted(counts.items())}


def _load_raw() -> dict:
    if not BASELINE_PATH.is_file():
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def load_baseline() -> dict[str, dict[str, int]]:
    return _load_raw().get("allowed", {})


def load_reasons() -> dict[str, str]:
    """基线里每个文件留在事件循环上的原因（``reasons``：文件 → 原因）。"""
    return _load_raw().get("reasons", {})


def compare(
    current: dict[str, dict[str, int]],
    baseline: dict[str, dict[str, int]],
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    stale: list[str] = []
    for rel, entries in current.items():
        for key, count in entries.items():
            if count > baseline.get(rel, {}).get(key, 0):
                errors.append(f"{rel}: 新增 async 里的同步数据库调用 -> {key}")
    for rel, entries in baseline.items():
        for key, count in entries.items():
            if current.get(rel, {}).get(key, 0) < count:
                stale.append(f"{rel}: 基线条目可删除 -> {key}")
    return errors, stale


def write_baseline(current: dict[str, dict[str, int]]) -> None:
    """写基线；``reasons`` 里仍有条目的文件保留原因，新文件留空待补（测试会拦下空原因）。"""
    reasons = load_reasons()
    payload = {
        "_comment": (
            "async def 里同步数据库调用的基线：allowed 是 文件 → 「异步函数() -> 被调用项」→ 次数，"
            "reasons 是每个文件留在事件循环上的原因。只减不增；"
            "能挪进线程（def 路由 / to_thread / run_blocking）的不要加。"
        ),
        "allowed": current,
        "reasons": {rel: reasons.get(rel, "") for rel in current},
    }
    BASELINE_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="有基线外的新增调用时退出 1")
    parser.add_argument("--list", action="store_true", help="列出当前所有命中（含行号）")
    parser.add_argument("--update", action="store_true", help="刷新基线文件")
    args = parser.parse_args(argv)

    modules = iter_modules()
    if args.list:
        for rel, lineno, key in find_blocking_calls(modules):
            print(f"{rel}:{lineno}  {key}")
        return 0

    current = current_baseline(modules)
    if args.update:
        write_baseline(current)
        total = sum(sum(entries.values()) for entries in current.values())
        print(f"[OK] 已刷新基线：{BASELINE_PATH}（{total} 处）")
        return 0

    errors, stale = compare(current, load_baseline())
    for item in stale:
        print(f"[INFO] {item}")
    for item in errors:
        print(f"[FAIL] {item}", file=sys.stderr)
    if errors:
        print(
            f"[FAIL] 检测到 {len(errors)} 处新增的 async 内同步数据库调用；"
            "请改成 def 路由，或用 asyncio.to_thread / run_blocking 放进线程，"
            "确有必要再执行 --update 并说明原因。",
            file=sys.stderr,
        )
        return 1
    print("[OK] 未发现新增的 async 内同步数据库调用")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
