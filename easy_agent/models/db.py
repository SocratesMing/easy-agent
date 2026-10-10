"""Database Models"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionModel:
    session_id: str                      # 会话ID
    title: str                           # 会话标题
    messages: list[dict[str, Any]] = field(default_factory=list)  # 对话消息列表
    created_at: str = ""                 # 创建时间
    updated_at: str = ""                 # 更新时间
    username: str = ""                   # 归属用户名
    workspace_name: str = ""             # 工作目录名
    todos: list[dict[str, Any]] = field(default_factory=list)  # 任务待办列表
    pinned: int = 0                      # 是否置顶（0/1）


@dataclass
class UserModel:
    user_id: str                         # 用户ID
    username: str                        # 用户名
    password_hash: str                   # 密码哈希（bcrypt）
    organization_id: str = ""            # 所属组织ID
    email: str = ""                      # 邮箱
    bound_ip: str = ""                   # 绑定IP（登录限制用）
    token_version: int = 0               # 令牌版本号（递增以使旧令牌失效）
    # 工号：注册必填、全局唯一；登录时可替代用户名。免密自动注册的用户没有工号，
    # 此时为空串（落库为 NULL，以便唯一索引允许多个空值）。
    employee_id: str = ""                # 工号
    # 人员信息列（knowledge 鉴权前置）：由迁移脚本/同步任务维护，注册时均为空
    display_name: str = ""               # 显示姓名
    department_id: str = ""              # 部门ID
    department_name: str = ""            # 部门名称
    # 组织结构：部门 > 处室 > 团队
    division_name: str = ""              # 处室名称
    team_name: str = ""                  # 团队名称
    position: str = ""                   # 岗位/职位
    mobile: str = ""                     # 手机号
    account_status: str = "active"       # 账号状态（active=正常 / disabled=禁用）
    personnel_source: str = ""           # 人员数据来源（Excel 导入/手动录入等）
    created_at: str = ""                 # 创建时间
    updated_at: str = ""                 # 更新时间


@dataclass
class ScheduledTaskModel:
    task_id: str                         # 任务ID
    username: str                        # 归属用户名
    session_id: str = ""                 # 关联会话ID
    workspace_name: str = ""             # 工作目录名（默认 workspace/{username}/{workspace_name}/）
    name: str = ""                       # 任务名称
    description: str = ""                # 任务描述
    schedule_cron: str = ""              # 调度表达式（Cron 格式）
    task_prompt: str = ""                # 任务执行提示词
    enabled: int = 1                     # 是否启用（0/1）
    created_at: str = ""                 # 创建时间
    updated_at: str = ""                 # 更新时间
    last_run_at: str = ""                # 上次运行时间
    next_run_at: str = ""                # 下次运行时间


@dataclass
class ScheduledTaskRunModel:
    run_id: str                          # 运行记录ID
    task_id: str                         # 所属任务ID
    session_id: str = ""                 # 关联会话ID
    status: str = "running"              # 运行状态（running/succeeded/failed）
    started_at: str = ""                 # 开始时间
    finished_at: str = ""                # 结束时间
    result_summary: str = ""             # 结果摘要
    error_message: str = ""              # 错误信息
