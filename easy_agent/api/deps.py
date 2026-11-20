"""API 层统一的 repository FastAPI 依赖与 ``as_*`` 适配入口。"""

from typing import Annotated

from fastapi import Depends

from easy_agent.db import get_database
from easy_agent.db.repositories.chat_repository import ChatRepository
from easy_agent.db.repositories.expert_repository import ExpertRepository
from easy_agent.db.repositories.file_repository import FileRepository
from easy_agent.db.repositories.scheduled_task_repository import ScheduledTaskRepository
from easy_agent.db.repositories.user_repository import UserRepository
from easy_agent.db.repositories.token_usage import TokenUsageRepository
from easy_agent.db.repositories.session_repository import SessionRepository
from easy_agent.middleware.auth import get_user_repository

__all__ = [
    "as_chat_repository",
    "as_expert_repository",
    "as_file_repository",
    "as_scheduled_task_repository",
    "as_session_repository",
    "as_user_repository",
    "get_chat_repository",
    "get_expert_repository",
    "get_file_repository",
    "get_scheduled_task_repository",
    "get_session_repository",
    "get_user_repository",
    "get_token_usage_repository",
]


def _resolve_database(
    db: Annotated[object, Depends(get_database)],
) -> object:
    """API 依赖图里唯一的 ``Database`` 接入点。

    各域 repository provider 都依赖本函数，因此数据库依赖只在此处声明一次；
    ``tests/conftest.py`` 对 ``get_database`` 的 override 仍沿依赖链生效。
    """
    return db


def get_token_usage_repository(
    db: Annotated[object, Depends(_resolve_database)],
) -> TokenUsageRepository:
    """FastAPI 依赖：个人 Token 账本。"""
    return TokenUsageRepository(db)


def get_chat_repository(
    db: Annotated[object, Depends(_resolve_database)],
) -> ChatRepository:
    """FastAPI 依赖：把进程级 ``Database`` 包装成 chat 域 repository。"""
    return ChatRepository(db)


def as_chat_repository(value: object) -> ChatRepository:
    """兼容直接调用路由函数并传入 ``Database`` 的既有测试。"""
    if isinstance(value, ChatRepository):
        return value
    return ChatRepository(value)


def get_file_repository(
    db: Annotated[object, Depends(_resolve_database)],
) -> FileRepository:
    """FastAPI 依赖：把进程级 ``Database`` 包装成 files 域 repository。"""
    return FileRepository(db)


def as_file_repository(value: object) -> FileRepository:
    """兼容直接调用路由函数并传入 ``Database`` 的既有测试。"""
    if isinstance(value, FileRepository):
        return value
    return FileRepository(value)


def get_expert_repository(
    db: Annotated[object, Depends(_resolve_database)],
) -> ExpertRepository:
    """FastAPI 依赖：把进程级 ``Database`` 包装成专家域 repository。"""
    return ExpertRepository(db)


def as_expert_repository(value: object) -> ExpertRepository:
    """兼容直接调用路由函数并传入 ``Database`` 的既有测试。"""
    if isinstance(value, ExpertRepository):
        return value
    return ExpertRepository(value)


def get_scheduled_task_repository(
    db: Annotated[object, Depends(_resolve_database)],
) -> ScheduledTaskRepository:
    """FastAPI 依赖：把进程级 ``Database`` 包装成定时任务域 repository。"""
    return ScheduledTaskRepository(db)


def as_scheduled_task_repository(
    value: object,
) -> ScheduledTaskRepository:
    """兼容直接调用路由函数并传入 ``Database`` 的既有测试。"""
    if isinstance(value, ScheduledTaskRepository):
        return value
    return ScheduledTaskRepository(value)


def get_session_repository(
    db: Annotated[object, Depends(_resolve_database)],
) -> SessionRepository:
    """FastAPI 依赖：把进程级 ``Database`` 包装成 sessions 域 repository。"""
    return SessionRepository(db)


def as_session_repository(value: object) -> SessionRepository:
    """兼容直接调用路由函数的旧测试（传入 ``db=<Database>``）。"""
    if isinstance(value, SessionRepository):
        return value
    return SessionRepository(value)


def as_user_repository(value: object) -> UserRepository:
    """兼容直接调用路由函数并传入 ``Database`` 的既有测试。"""
    if isinstance(value, UserRepository):
        return value
    return UserRepository(value)
