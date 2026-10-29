"""批次 4：``api/deps.py`` provider 统一后的边界回归。

覆盖：

- 各 API 模块的模块级旧名字（``get_*_repository`` / ``_as_*_repository``）
  与 ``api.deps`` 是同一对象（不是拷贝），旧导入/override 继续生效；
- 只有 ``api.deps`` 声明一次 ``Depends(get_database)``，API 文件均为 0；
- ``api.deps`` 不 import 任何 ``easy_agent.api.*``；
- provider 仍能构造各域 repository，``as_*`` 保持 isinstance 窄包装语义；
- FastAPI 依赖链仍以 ``easy_agent.db.get_database`` 为叶子，测试 override 可达；
- 受影响路由的 URL 集合与全量 OpenAPI 路径数量不变。
"""

from __future__ import annotations

import ast
import importlib
import inspect
from pathlib import Path
from typing import get_args

import pytest

from easy_agent.api import deps
from easy_agent.app import app
from easy_agent.db import get_database
from easy_agent.db.repositories.chat_repository import ChatRepository
from easy_agent.db.repositories.expert_repository import ExpertRepository
from easy_agent.db.repositories.file_repository import FileRepository
from easy_agent.db.repositories.scheduled_task_repository import (
    ScheduledTaskRepository,
)
from easy_agent.db.repositories.user_repository import UserRepository
from easy_agent.db.session_repository import SessionRepository
from easy_agent.middleware.auth import get_user_repository as middleware_user_provider

REPO_ROOT = Path(__file__).resolve().parents[2]
API_DIR = REPO_ROOT / "easy_agent" / "api"

# module 名 -> (provider 名, as_* 名, repository 类)
MODULE_PROVIDERS = {
    "chat": ("get_chat_repository", "as_chat_repository", ChatRepository),
    "chat_live": ("get_chat_repository", "as_chat_repository", ChatRepository),
    "files": ("get_file_repository", "as_file_repository", FileRepository),
    "expert": ("get_expert_repository", "as_expert_repository", ExpertRepository),
    "scheduled_tasks": (
        "get_scheduled_task_repository",
        "as_scheduled_task_repository",
        ScheduledTaskRepository,
    ),
    "sessions": ("get_session_repository", "as_session_repository", SessionRepository),
    "auth": ("get_user_repository", "as_user_repository", UserRepository),
}

# 本批次触及的 6 组路由（chat_live 的 2 条路由并入 chat router）。
EXPECTED_ROUTER_PATHS = {
    "chat": {
        "/agent/chat/stream",
        "/agent/chat/cancel",
        "/agent/chat/resume",
        "/agent/chat/stream/status",
        "/agent/chat/stream/live",
    },
    "sessions": {
        "/agent/sessions",
        "/agent/sessions/count",
        "/agent/sessions/{session_id}",
        "/agent/sessions/{session_id}/title",
        "/agent/sessions/{session_id}/pin",
        "/agent/sessions/{session_id}/history",
        "/agent/sessions/{session_id}/messages",
        "/agent/sessions/{session_id}/upload",
    },
    "files": {
        "/agent/files/session/{session_id}",
        "/agent/files/upload",
        "/agent/files/download/{file_path:path}",
        "/agent/files/{file_id}",
        "/agent/files/parse",
        "/agent/files/preview",
        "/agent/files/workspace/tree",
    },
    "auth": {
        "/agent/auth/register",
        "/agent/auth/login",
        "/agent/auth/login-passwordless",
        "/agent/auth/logout",
        "/agent/auth/profile",
        "/agent/auth/reset-password",
        "/agent/auth/admin/users",
        "/agent/auth/admin/users/{username}/reset-password",
        "/agent/auth/config",
    },
    "scheduled_tasks": {
        "/agent/scheduled-tasks",
        "/agent/scheduled-tasks/{task_id}",
        "/agent/scheduled-tasks/{task_id}/runs",
        "/agent/scheduled-tasks/{task_id}/toggle",
        "/agent/scheduled-tasks/{task_id}/run",
        "/agent/scheduled-tasks/{task_id}/workspace",
        "/agent/scheduled-tasks/{task_id}/workspace/file",
    },
    "expert": {
        "/agent/add",
        "/agent/update",
        "/agent/list",
        "/agent/public-list",
        "/agent/del",
        "/agent/expert/required-skills",
        "/agent/expert-md",
        "/agent/session-experts",
    },
}

# 批次 4 前后 app.openapi()["paths"] 的基线数量。
# 2026-10-22：/agent/stream_export_run 删除（专家对话并入 /agent/chat/stream），
# /agent/expert/required-skills 新增，一删一增，路径总数仍是 121。
# 2026-10-28：资产页下线删掉 /agent/files/list、新增 /agent/settings/knowledge（e59c5a6d），
# 专家工作区新增 /agent/expert-md、/agent/session-experts（cc4b1e9d），121 - 1 + 3 = 123。
EXPECTED_OPENAPI_PATH_COUNT = 123


def _module(name: str):
    return importlib.import_module(f"easy_agent.api.{name}")


@pytest.mark.parametrize("module_name", sorted(MODULE_PROVIDERS))
def test_module_level_providers_are_reexports_of_deps(module_name):
    provider, as_name, _ = MODULE_PROVIDERS[module_name]
    module = _module(module_name)

    assert getattr(module, provider) is getattr(deps, provider), (module_name, provider)
    assert getattr(module, "_" + as_name) is getattr(deps, as_name), (
        module_name,
        as_name,
    )


def test_old_import_paths_keep_working():
    """旧模块导入路径必须指向同一对象，否则外部依赖 override 会失效。"""
    from easy_agent.api import chat as chat_module
    from easy_agent.api import chat_live as chat_live_module
    from easy_agent.api import sessions as sessions_module

    assert chat_module.get_chat_repository is chat_live_module.get_chat_repository
    assert chat_module.get_chat_repository is deps.get_chat_repository
    assert sessions_module.get_session_repository is deps.get_session_repository
    assert deps.get_user_repository is middleware_user_provider
    assert deps.get_database is get_database


def test_providers_construct_domain_repositories(db):
    for provider, _, repository_cls in MODULE_PROVIDERS.values():
        assert isinstance(getattr(deps, provider)(db), repository_cls), provider


def test_as_helpers_keep_repository_identity_and_wrap_database(db):
    for _, as_name, repository_cls in MODULE_PROVIDERS.values():
        helper = getattr(deps, as_name)
        repository = repository_cls(db)

        assert helper(repository) is repository, as_name
        assert isinstance(helper(db), repository_cls), as_name


def test_get_database_is_declared_once_in_deps_only():
    declarations = {}
    for path in sorted(API_DIR.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        count = source.count("Depends(get_database)")
        if count:
            declarations[path.name] = count
        if path.name != "deps.py":
            assert "from ..db import get_database" not in source, path.name

    assert declarations == {"deps.py": 1}, declarations


def test_deps_does_not_import_api_modules():
    tree = ast.parse((API_DIR / "deps.py").read_text(encoding="utf-8"))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # level == 1 表示 ``from .xxx`` / ``from . import xxx``（同属 api 包）。
            assert node.level != 1, ast.dump(node)
            assert not (node.module or "").startswith("easy_agent.api"), node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("easy_agent.api"), alias.name


def test_deps_providers_share_one_database_resolver():
    """provider 只依赖 _resolve_database，后者才依赖 get_database（唯一入口）。"""
    resolver = deps._resolve_database
    for provider in (
        "get_chat_repository",
        "get_file_repository",
        "get_expert_repository",
        "get_scheduled_task_repository",
        "get_session_repository",
    ):
        annotated = get_args(
            inspect.signature(getattr(deps, provider))
            .parameters["db"]
            .annotation
        )
        assert annotated[0] is object, provider
        dependency = annotated[1].dependency
        assert dependency is resolver, provider

    annotated = get_args(
        inspect.signature(resolver).parameters["db"].annotation
    )
    assert annotated[1].dependency is get_database


def test_legacy_module_provider_override_still_applies(client, db):
    """通过旧模块路径覆盖 provider（同一对象）必须对路由生效。"""
    from easy_agent.api import sessions as sessions_module

    calls: list[dict] = []

    class _SpySessionRepository(SessionRepository):
        def list_sessions(self, *args, **kwargs):
            calls.append(kwargs)
            return []

    spy = _SpySessionRepository(db)
    app.dependency_overrides[sessions_module.get_session_repository] = lambda: spy
    try:
        response = client.get("/agent/sessions")
    finally:
        app.dependency_overrides.pop(
            sessions_module.get_session_repository, None
        )

    assert response.status_code == 200
    assert response.json() == []
    assert calls == [{"limit": 50, "offset": 0, "username": "testuser"}]


def test_openapi_route_count_and_affected_paths_unchanged():
    schema = app.openapi()
    paths = set(schema["paths"])

    assert len(paths) == EXPECTED_OPENAPI_PATH_COUNT

    affected = set()
    for module_name, expected_paths in EXPECTED_ROUTER_PATHS.items():
        router = _module(module_name).router
        actual_paths = {route.path for route in router.routes}
        assert actual_paths == expected_paths, module_name
        affected |= {
            path.replace("{file_path:path}", "{file_path}") for path in actual_paths
        }

    assert affected <= paths
