"""API contracts for administrator-owned personnel configuration."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import AliasChoices, BaseModel, Field, field_validator, model_validator

AccountStatus = Literal["active", "disabled"]
# 系统管理员专职部门：admin 账号固定归属该部门，作为公共空间建库的兜底部门，
# 其他账号不允许被分配到此部门。
ADMIN_DEPARTMENT_ID = "admin"
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
    # 组织结构：部门 > 处室 > 团队。处室/团队可选；部门编号可省略，
    # 省略时自动回填为部门名称（Excel 部门结构导入即按此生成）。
    department_id: str = Field(default="", max_length=255)
    department_name: str = Field(min_length=1, max_length=255)
    division_name: str = Field(default="", max_length=255)
    team_name: str = Field(default="", max_length=255)
    employee_id: str = Field(default="", max_length=64)
    email: str = Field(default="", max_length=255)
    position: str = Field(default="", max_length=255)
    # 所属系统：多个系统用 / 分隔，筛选时任一命中即可。
    systems: str = Field(default="", max_length=255)
    # 系统↔角色配对（JSON，接口同步写入）：选择系统后角色下拉只展示该系统下的角色。
    role_systems: str = Field(default="", max_length=1000)
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
        "division_name",
        "team_name",
        "employee_id",
        "email",
        "position",
        "systems",
        "role_systems",
        "mobile",
        "source",
        mode="before",
    )
    @classmethod
    def strip_text(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("systems", mode="before")
    @classmethod
    def normalize_systems(cls, value: object) -> str:
        """把常见的多值分隔符统一成 /，并去空去重。"""
        text = str(value or "").strip()
        for separator in ("、", "，", ",", "；", ";", "|", "｜"):
            text = text.replace(separator, "/")
        values: list[str] = []
        for item in text.split("/"):
            name = item.strip()
            if name and name not in values:
                values.append(name)
        return "/".join(values)

    @field_validator("account_status", mode="before")
    @classmethod
    def validate_status(cls, value: object) -> str:
        return normalize_status(value)

    @model_validator(mode="after")
    def fill_department_id(self) -> "PersonnelBase":
        if not self.department_id:
            self.department_id = self.department_name
        if self.department_id.casefold() == ADMIN_DEPARTMENT_ID:
            raise ValueError(f"部门 {ADMIN_DEPARTMENT_ID} 为系统管理员专用，不可分配给其他账号")
        return self


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
    division_name: str = ""
    team_name: str = ""
    email: str = ""
    position: str = ""
    systems: str = ""
    role_systems: str = ""
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
    # 全量可选系统（跨分页去重），供界面下拉筛选。
    system_options: list[str] = Field(default_factory=list)


class PersonnelImportResponse(BaseModel):
    total: int
    created: int
    updated: int
    source: str
    default_password: str = "123456"


class PersonnelSyncResponse(PersonnelImportResponse):
    """接口同步结果；skipped 含 admin 与员工编号冲突等被跳过的人员。"""

    skipped: int = 0
    errors: list[str] = Field(default_factory=list)
