"""按领域拆分的数据库 repository。

- 领域 SQL 函数：``agent_state`` / ``files`` / ``mcp_api_keys`` /
  ``scheduled_tasks`` / ``session_runtime`` / ``sessions`` / ``stream_events`` /
  ``users``；连接/事务由传入的 ``Database`` 对象提供。
- 领域 repository 对象：``session_repository``（会话）、
  ``message_repository``（消息）、``agent_runs`` 里的 ``AgentRunRepository``、
  ``session_runtime`` 里的 ``SessionRuntimeRepository``、``stream_events`` 里的
  ``StreamEventRepository``。API 层经 ``api/deps.py`` 注入，服务层经
  ``easy_agent.db.providers`` 取用。
- 按 API 划分的门面 ``chat_repository`` / ``expert_repository`` /
  ``file_repository`` / ``scheduled_task_repository`` / ``user_repository`` 是
  API 层入口；``chat_repository`` 组合上面的领域 repository。

``Database`` 只为尚未迁走的域保留同名委托。会话、消息、run、流运行时不要再走 ``Database`` 业务方法。
"""

from easy_agent.db.repositories import (  # noqa: F401
    agent_runs,
    agent_state,
    chat_repository,
    expert_repository,
    file_repository,
    files,
    mcp_api_keys,
    message_repository,
    scheduled_task_repository,
    session_repository,
    session_runtime,
    sessions,
    stream_events,
    user_repository,
    users,
)

__all__ = [
    "agent_runs",
    "agent_state",
    "chat_repository",
    "expert_repository",
    "file_repository",
    "files",
    "mcp_api_keys",
    "message_repository",
    "scheduled_task_repository",
    "session_repository",
    "session_runtime",
    "sessions",
    "stream_events",
    "user_repository",
    "users",
]