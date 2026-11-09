"""进程级 ``Database`` → 领域 repository 的 provider（T3 数据访问层收敛）。

分工：

- API 层：仍走 :mod:`easy_agent.api.deps` 的 FastAPI ``Depends``（provider 只在
  ``api/deps.py`` 声明一次 ``get_database`` 依赖）；
- 服务 / 工具层：没有依赖注入容器，确实需要某个领域 repository 时从这里取。

**本模块是 ``easy_agent/db/`` 之外唯一允许间接拿到进程级 ``Database`` 的接缝**：
调用方拿到的是领域 repository（会话/消息/运行/流运行时/流事件），构造只保存
``Database`` 引用，不缓存、不改变任何 SQL 与事务边界。这样
``scripts/quality/check_double_write.py`` 的「``db/`` 之外 ``get_database()`` 调用数」
才能只减不增；新增调用方请导入领域 repository 或本模块的 provider，不要直接拿
``Database``。

约定：每次调用都返回新的薄包装对象（无状态、零开销），不要跨请求/跨线程持有。
"""

from __future__ import annotations

from easy_agent.db.database import get_database
from easy_agent.db.repositories.agent_runs import AgentRunRepository
from easy_agent.db.repositories.agent_state import AgentStateRepository
from easy_agent.db.repositories.expert_repository import ExpertRepository
from easy_agent.db.repositories.file_repository import FileRepository
from easy_agent.db.repositories.rate_limits import RateLimitRepository
from easy_agent.db.repositories.scheduled_task_repository import (
    ScheduledTaskRepository,
)
from easy_agent.db.repositories.message_repository import MessageRepository
from easy_agent.db.repositories.session_repository import SessionRepository
from easy_agent.db.repositories.session_runtime import SessionRuntimeRepository
from easy_agent.db.repositories.stream_events import StreamEventRepository
from easy_agent.db.repositories.user_repository import UserRepository

__all__ = [
    "get_agent_run_repository",
    "get_agent_state_repository",
    "get_expert_repository",
    "get_file_repository",
    "get_message_repository",
    "get_session_repository",
    "get_session_runtime_repository",
    "get_rate_limit_repository",
    "get_scheduled_task_repository",
    "get_stream_event_repository",
    "get_user_repository",
]


def get_session_repository() -> SessionRepository:
    """进程级 ``Database`` 上的会话领域 repository。"""
    return SessionRepository(get_database())


def get_message_repository() -> MessageRepository:
    """进程级 ``Database`` 上的消息领域 repository。"""
    return MessageRepository(get_database())


def get_agent_run_repository() -> AgentRunRepository:
    """进程级 ``Database`` 上的 run 记录领域 repository。"""
    return AgentRunRepository(get_database())


def get_session_runtime_repository() -> SessionRuntimeRepository:
    """进程级 ``Database`` 上的会话运行期状态（流锁/连接器/待修改专家）repository。"""
    return SessionRuntimeRepository(get_database())


def get_stream_event_repository() -> StreamEventRepository:
    """进程级 ``Database`` 上的 SSE 事件持久化 repository。"""
    return StreamEventRepository(get_database())

def get_file_repository() -> FileRepository:
    """进程级 ``Database`` 上的文件域 repository（上传文件 / 生成文件 / 归属）。"""
    return FileRepository(get_database())


def get_user_repository() -> UserRepository:
    """进程级 ``Database`` 上的用户域 repository（服务层 / 工具层用）。"""
    return UserRepository(get_database())


def get_expert_repository() -> ExpertRepository:
    """进程级 ``Database`` 上的专家域 repository（专家记录 / 归属 / 专家会话）。"""
    return ExpertRepository(get_database())


def get_scheduled_task_repository() -> ScheduledTaskRepository:
    """进程级 ``Database`` 上的定时任务领域 repository（任务 / 执行记录 / 工作目录）。"""
    return ScheduledTaskRepository(get_database())


def get_agent_state_repository() -> AgentStateRepository:
    """进程级 ``Database`` 上的 LangGraph store 键值 repository。"""
    return AgentStateRepository(get_database())


def get_rate_limit_repository() -> RateLimitRepository:
    """进程级 ``Database`` 上的限流共享计数 repository。"""
    return RateLimitRepository(get_database())
