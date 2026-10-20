"""C1 剩余域 API 层回归：无 Database 门面、provider 注入、依赖可替换。

只做静态/依赖注入断言，不重复各域既有业务用例（那些在
test_auth/test_files/test_scheduled_tasks/test_expert/test_chat 中）。

C1b 起 `get_user_repository` provider 收敛到 `middleware.auth`：auth / files /
scheduled_tasks 三个 API 模块共用同一 provider，活跃时间与 SSO token 校验不再
向下取 `repo.database`。

批次 4 起 repository provider 统一到 `api.deps`：API 文件不再声明
`Depends(get_database)`（统一后为 0），只从 `api.deps` 导入 provider 并在模块级
re-export 旧名字。本文件把「已消除的边界」和「登记保留的边界」都写成断言，
防止悄悄回退。
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

from easy_agent.api import deps
from easy_agent.api.auth import get_user_repository
from easy_agent.app import app
from easy_agent.db.repositories.user_repository import UserRepository
from easy_agent.middleware.auth import (
    get_user_repository as middleware_user_provider,
)

API_DIR = Path(__file__).resolve().parents[2] / "easy_agent" / "api"

DOMAINS = {
    "files.py": ("get_file_repository", "FileRepository", 6),
    "scheduled_tasks.py": (
        "get_scheduled_task_repository",
        "ScheduledTaskRepository",
        7,
    ),
    "expert.py": ("get_expert_repository", "ExpertRepository", 5),
    "chat.py": ("get_chat_repository", "ChatRepository", 2),
    "chat_live.py": ("get_chat_repository", "ChatRepository", 2),
    "sessions.py": ("get_session_repository", "SessionRepository", 10),
}

# C1b 之后每个 API 文件仍保留的 `repo.database` 调用点数；C1b 的每一条都在
# docs/archive/audit-reports/2026-10-18-increment61-c1b-report.md「仍保留边界」有登记，
# Phase 2B 新增的 run 登记 ×2 见 docs/archive/audit-reports/2026-10-18-increment63-phase2b-report.md。
RETAINED_DATABASE_BOUNDARIES = {
    "auth.py": 1,  # KnowledgeRepository（知识域独立 bounded context）
    "files.py": 0,
    "scheduled_tasks.py": 0,
    "expert.py": 3,  # 流式引擎 + Phase 2 stream_events 写入/回放边界
    # 知识域鉴权/编排 ×2 + 专家绑定 ×1 + 流式引擎 ×2 + 事件回放/状态 ×4
    # + Phase 2B run 登记 ×2（chat_stream / chat_resume 的 start_run）
    "chat.py": 9,
    "chat_live.py": 2,
    "sessions.py": 2,  # 会话上传清单 / session_uploads 构造边界
}


def test_remaining_domain_files_have_no_database_facade_usage():
    for filename in DOMAINS:
        source = (API_DIR / filename).read_text(encoding="utf-8")
        assert "from ..db import Database" not in source, filename
        assert "Annotated[Database" not in source, filename
        # 批次 4：provider 统一到 api.deps 后，API 文件不再声明数据库依赖。
        assert source.count("Depends(get_database)") == 0, filename

        tree = ast.parse(source)
        direct_db_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "db"
        ]
        assert direct_db_calls == [], (filename, direct_db_calls)


def test_user_repository_provider_is_shared_from_middleware():
    """auth.py 不再自带 provider：与 files/scheduled 共用 middleware 的实现。"""
    source = (API_DIR / "auth.py").read_text(encoding="utf-8")
    auth_module = importlib.import_module("easy_agent.api.auth")

    assert get_user_repository is middleware_user_provider
    assert get_user_repository is deps.get_user_repository
    assert "from easy_agent.middleware.auth import (" in source
    assert "get_user_repository," in source
    assert "from easy_agent.middleware import get_current_username" in source
    assert "Depends(get_database)" not in source
    assert "Annotated[Database" not in source
    assert "def _as_user_repository(" not in source
    assert auth_module._as_user_repository is deps.as_user_repository
    annotation = "db: Annotated[UserRepository, Depends(get_user_repository)]"
    assert source.count(annotation) == 9, source.count(annotation)


def test_remaining_domain_routes_use_repository_provider():
    for filename, (provider, repo_name, expected_routes) in DOMAINS.items():
        module_name = Path(filename).stem
        module = importlib.import_module(f"easy_agent.api.{module_name}")
        source = (API_DIR / filename).read_text(encoding="utf-8")

        # 批次 4：provider 只在 api.deps 定义，API 模块只 re-export。
        assert f"def {provider}(" not in source, filename
        assert getattr(module, provider) is getattr(deps, provider), filename
        assert "from easy_agent.api.deps import" in source, filename

        annotation = f"db: Annotated[{repo_name}, Depends({provider})]"
        assert source.count(annotation) == expected_routes, filename


def test_repo_database_usage_shrinks_to_documented_boundaries():
    for filename, expected in RETAINED_DATABASE_BOUNDARIES.items():
        source = (API_DIR / filename).read_text(encoding="utf-8")
        assert source.count("repo.database") == expected, filename


def test_user_repository_dependency_can_be_overridden(client, db):
    db.register_user("testuser", "secret123")
    calls: list[str] = []

    class _SpyRepository(UserRepository):
        def get_user_by_username(self, username):
            calls.append(username)
            return super().get_user_by_username(username)

        def touch_user_activity(self, username, timestamp):
            calls.append(f"touch:{username}")
            return super().touch_user_activity(username, timestamp)

    spy = _SpyRepository(db)
    app.dependency_overrides[get_user_repository] = lambda: spy
    try:
        response = client.get("/agent/auth/profile")
    finally:
        app.dependency_overrides.pop(get_user_repository, None)

    assert response.status_code == 200
    assert response.json()["username"] == "testuser"
    assert calls == ["testuser"]
