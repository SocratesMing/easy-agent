"""API contracts for administrator-owned personnel configuration."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator

AccountStatus = Literal["active", "disabled"]
# Usernames become directory names in the legacy EasyAgent workspace layout.
# A dot is deliberately excluded because the current directory sanitizer removes
# it, which would make e.g. ``john.doe`` collide with ``johndoe``.
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,64}$")


def normalize_status(value: object) -> str:
    text = str(value or "active").strip().lower()
    mapping = {
        "active": "active",
        "enabled": "active",
        "启用": "active",
        "正常": "active",
        "disabled": "disabled",
        "inactive": "disabled",
        "停用": "disabled",
        "禁用": "disabled",
    }
    if text not in mapping:
        raise ValueError("状态只能是启用/停用或 active/disabled")
    return mapping[text]


class PersonnelBase(BaseModel):
    display_name: str = Field(min_length=1, max_length=127)
    department_id: str = Field(min_length=1, max_length=255)
    department_name: str = Field(min_length=1, max_length=255)
    employee_id: str = Field(default="", max_length=64)
    email: str = Field(default="", max_length=255)
    position: str = Field(default="", max_length=127)
    mobile: str = Field(default="", max_length=64)
    account_status: AccountStatus = "active"
    source: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("source", "personnel_source"),
    )

    @field_validator(
        "display_name",
        "department_id",
        "department_name",
        "employee_id",
        "email",
        "position",
        "mobile",
        "source",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("account_status", mode="before")
    @classmethod
    def validate_status(cls, value: object) -> str:
        return normalize_status(value)


class PersonnelCreateRequest(PersonnelBase):
    username: str = Field(min_length=3, max_length=64)

    @field_validator("username", mode="before")
    @classmethod
    def validate_username(cls, value: object) -> str:
        username = str(value or "").strip()
        if not _USERNAME_RE.fullmatch(username):
            raise ValueError("账号仅支持 3-64 位字母、数字、下划线或连字符")
        return username


class PersonnelUpdateRequest(PersonnelBase):
    pass


class PersonnelRecord(BaseModel):
    user_id: str
    username: str
    employee_id: str = ""
    display_name: str = ""
    department_id: str = ""
    department_name: str = ""
    email: str = ""
    position: str = ""
    mobile: str = ""
    account_status: AccountStatus = "active"
    source: str = ""
    created_at: str
    updated_at: str


class PersonnelListResponse(BaseModel):
    total: int
    items: list[PersonnelRecord]
    page: int = 1
    page_size: int = 50


class PersonnelImportResponse(BaseModel):
    total: int
    created: int
    updated: int
    source: str
    default_password: str = "123456"
