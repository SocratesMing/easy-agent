"""C1 剩余域 API 层回归：无 Database 门面、provider 注入、依赖可替换。

只做静态/依赖注入断言，不重复各域既有业务用例（那些在
test_auth/test_files/test_scheduled_tasks/test_expert/test_chat 中）。
"""

from __future__ import annotations

import ast
from pathlib import Path

from easy_agent.app import app
from easy_agent.api.auth import get_user_repository
from easy_agent.db.repositories.user_repository import UserRepository

API_DIR = Path(__file__).resolve().parents[2] / "easy_agent" / "api"

DOMAINS = {
    "auth.py": ("get_user_repository", "UserRepository", 9),
    "files.py": ("get_file_repository", "FileRepository", 6),
    "scheduled_tasks.py": (
        "get_scheduled_task_repository",
        "ScheduledTaskRepository",
        7,
    ),
    "expert.py": ("get_expert_repository", "ExpertRepository", 5),
    "chat.py": ("get_chat_repository", "ChatRepository", 4),
}


def test_remaining_domain_files_have_no_database_facade_usage():
    for filename in DOMAINS:
        source = (API_DIR / filename).read_text(encoding="utf-8")
        assert "from ..db import Database" not in source, filename
        assert "Annotated[Database" not in source, filename
        assert source.count("Depends(get_database)") == 1, filename

        tree = ast.parse(source)
        direct_db_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "db"
        ]
        assert direct_db_calls == [], (filename, direct_db_calls)


def test_remaining_domain_routes_use_repository_provider():
    for filename, (provider, repo_name, expected_routes) in DOMAINS.items():
        source = (API_DIR / filename).read_text(encoding="utf-8")
        assert f"def {provider}(" in source, filename
        annotation = f"db: Annotated[{repo_name}, Depends({provider})]"
        assert source.count(annotation) == expected_routes, filename


def test_user_repository_dependency_can_be_overridden(client, db):
    db.register_user("testuser", "secret123")
    calls: list[str] = []

    class _SpyRepository(UserRepository):
        def get_user_by_username(self, username):
            calls.append(username)
            return super().get_user_by_username(username)

    spy = _SpyRepository(db)
    app.dependency_overrides[get_user_repository] = lambda: spy
    try:
        response = client.get("/agent/auth/profile")
    finally:
        app.dependency_overrides.pop(get_user_repository, None)

    assert response.status_code == 200
    assert response.json()["username"] == "testuser"
    assert calls == ["testuser"]
