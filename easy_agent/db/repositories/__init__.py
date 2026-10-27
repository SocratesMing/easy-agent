"""按领域拆分的数据库 repository（ARCH-03 Phase 2 / C1）。

- ``agent_state`` / ``files`` / ``mcp_api_keys`` / ``scheduled_tasks`` /
  ``session_runtime`` / ``sessions`` / ``users``：
  领域 SQL 函数，连接/事务由传入的 ``Database`` 对象提供；
- ``chat_repository`` / ``expert_repository`` / ``file_repository`` /
  ``scheduled_task_repository`` / ``user_repository``：C1 的域门面对象，
  API 层通过它们注入，不直接调用 ``Database`` 业务方法。

``Database`` 保留同名方法作为委托，调用方无需一次性迁移。
"""

from easy_agent.db.repositories import (  # noqa: F401
    agent_state,
    chat_repository,
    expert_repository,
    file_repository,
    files,
    mcp_api_keys,
    scheduled_task_repository,
    scheduled_tasks,
    session_runtime,
    sessions,
    user_repository,
    users,
)

__all__ = [
    "agent_state",
    "chat_repository",
    "expert_repository",
    "file_repository",
    "files",
    "mcp_api_keys",
    "scheduled_task_repository",
    "scheduled_tasks",
    "session_runtime",
    "sessions",
    "user_repository",
    "users",
]
