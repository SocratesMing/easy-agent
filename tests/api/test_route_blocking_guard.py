"""路由不在事件循环上做同步数据库调用。

数据库层全是同步的。一个从不 ``await`` 的 ``async def`` 路由，查库时会占住事件循环，所有并发
的 SSE 流跟着停顿；写成 ``def``，FastAPI 会把它放进线程池。下面两条保证：

- 已经改成 ``def`` 的纯数据库路由不再改回 ``async def``；
- 不再新增「从不 await、却直接查库」的 ``async def`` 路由，除非写进白名单并说明原因。
  白名单只减不增：条目不再命中（已改成 def 或已有 await）时也会失败，提醒删掉它。

依赖同理：鉴权依赖 ``get_current_username`` 每个需要登录的请求都要查库，必须保持同步函数。

路由以外的代码（服务、工具、帮助函数），以及 async 路由里夹在 await 之间的同步查库，由
``scripts/quality/check_async_blocking.py``（CI 架构门禁）按基线检查。
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[2] / "easy_agent" / "api"

CONVERTED = {
    "easy_agent.api.auth": [
        "register",
        "login",
        "login_passwordless",
        "logout",
        "get_profile",
        "update_profile",
        "reset_password",
        "list_users",
        "admin_reset_password",
    ],
    "easy_agent.api.scheduled_tasks": [
        "list_scheduled_tasks",
        "list_task_runs",
        "get_task_workspace",
        "get_task_workspace_file",
    ],
    "easy_agent.api.sessions": [
        "create_session",
        "list_sessions",
        "count_sessions",
        "update_title",
        "toggle_pin",
        "add_message",
    ],
    "easy_agent.api.files": [
        "get_session_generated_files",
        "get_workspace_tree",
    ],
}

#: 仍为 async、不 await 却查库的路由；必须在事件循环线程上执行的才放这里。
#: 改进程内状态（Agent 缓存、调度任务）的路由保持 async，查库用 asyncio.to_thread 放进线程。
ALLOWED: dict[tuple[str, str], str] = {}
#: 整个模块暂不检查：另一路会话正在修改，提交后按同一规则处理
MODULES_PENDING: dict[str, str] = {}

_DB_HINTS = ("get_database", "Repository", "repo.", "database", "db.")
_ROUTE_METHODS = {"get", "post", "put", "delete", "patch"}


def _blocking_async_routes() -> set[tuple[str, str]]:
    found = set()
    for path in sorted(API_DIR.glob("*.py")):
        if path.name in MODULES_PENDING:
            continue
        source = path.read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            if not any(
                isinstance(d, ast.Call) and getattr(d.func, "attr", "") in _ROUTE_METHODS
                for d in node.decorator_list
            ):
                continue
            if any(isinstance(x, (ast.Await, ast.AsyncFor, ast.AsyncWith)) for x in ast.walk(node)):
                continue
            body = ast.get_source_segment(source, node) or ""
            if any(hint in body for hint in _DB_HINTS):
                found.add((path.name, node.name))
    return found


def test_converted_routes_stay_sync():
    for module_name, names in CONVERTED.items():
        module = importlib.import_module(module_name)
        for name in names:
            assert not inspect.iscoroutinefunction(getattr(module, name)), f"{module_name}.{name}"


def test_checkpoint_projection_routes_offload_business_queries():
    """历史投影异步读取 SQLite saver，同步业务查询必须放入工作线程。"""
    from easy_agent.api import sessions

    for route in (sessions.get_session, sessions.get_chat_history):
        assert inspect.iscoroutinefunction(route)
        source = inspect.getsource(route)
        assert "await asyncio.to_thread(repo.get_owned_session" in source
        assert "await compaction_service.records" in source


def test_no_new_async_routes_that_block_on_the_database():
    found = _blocking_async_routes()

    unexpected = sorted(found - set(ALLOWED))
    assert not unexpected, (
        f"这些 async 路由从不 await 却直接查库，会占住事件循环：{unexpected}。"
        "改成 def（FastAPI 自动放进线程池），或用 run_blocking 包住数据库调用"
    )
    stale = sorted(set(ALLOWED) - found)
    assert not stale, f"白名单条目已不再命中，请删掉：{stale}"


def test_auth_dependency_runs_in_the_threadpool():
    """改造前它是 async def：每个登录后的请求都在事件循环上同步查 2～3 次库。"""
    from easy_agent.middleware.auth import get_current_username

    assert not inspect.iscoroutinefunction(get_current_username)
