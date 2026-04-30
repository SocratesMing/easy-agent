"""Database models for sessions and users"""

import json
from typing import Any

from pydantic import BaseModel


class SessionModel(BaseModel):
    session_id: str
    title: str
    messages: list[dict[str, Any]]
    created_at: str
    updated_at: str
    username: str = ""

    def to_json(self) -> str:
        return json.dumps(self.model_dump(), ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> "SessionModel":
        data = json.loads(json_str)
        return cls(**data)


class UserModel(BaseModel):
    user_id: str
    username: str
    password_hash: str = ""
    organization_id: str = ""
    email: str = ""
    bound_ip: str = ""
    created_at: str
    updated_at: str
