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


@dataclass
class UserModel:
    user_id: str
    username: str
    password_hash: str
    organization_id: str = ""
    email: str = ""
    bound_ip: str = ""
    created_at: str = ""
    updated_at: str = ""
