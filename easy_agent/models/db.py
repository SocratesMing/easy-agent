"""Database Models"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionModel:
    session_id: str
    title: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    username: str = ""
    workspace_name: str = ""
    todos: list[dict[str, Any]] = field(default_factory=list)
    pinned: int = 0


@dataclass
class UserModel:
    user_id: str
    username: str
    password_hash: str
    organization_id: str = ""
    email: str = ""
    bound_ip: str = ""
    token_version: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class ScheduledTaskModel:
    task_id: str
    username: str
    session_id: str = ""
    workspace_name: str = ""      # 工作目录名（默认 workspace/{username}/{workspace_name}/）
    name: str = ""
    description: str = ""
    schedule_cron: str = ""
    task_prompt: str = ""
    enabled: int = 1
    created_at: str = ""
    updated_at: str = ""
    last_run_at: str = ""
    next_run_at: str = ""


@dataclass
class ScheduledTaskRunModel:
    run_id: str
    task_id: str
    session_id: str = ""
    status: str = "running"
    started_at: str = ""
    finished_at: str = ""
    result_summary: str = ""
    error_message: str = ""
