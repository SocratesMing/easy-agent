"""用户相关 SQL repository（ARCH-03 Phase 2）。

从 Database 抽出的 users 领域：注册/查询/更新、token_version、活跃时间、
默认用户/免密用户。personnel 字段扩展仍在函数内惰性 import，避免模块级循环。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Optional

from ...models.db import UserModel
from ...utils.auth import hash_password, verify_password

logger = logging.getLogger("easy_agent.db.users")


def create_user(db, user_data: UserModel) -> UserModel:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            INSERT INTO users (user_id, username, password_hash, organization_id, email, bound_ip, token_version, employee_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_data.user_id,
                user_data.username,
                user_data.password_hash,
                user_data.organization_id,
                user_data.email,
                user_data.bound_ip,
                user_data.token_version,
                # 空工号统一存 NULL：唯一索引允许多个 NULL，空串却只能有一个
                user_data.employee_id or None,
                user_data.created_at,
                user_data.updated_at,
            ),
        )
        from ..user_fields_registry import save_user_fields

        save_user_fields(db, cursor, user_data)
    return user_data

def row_to_user(row) -> UserModel:
    """users 表行 → UserModel。缺列时给默认值，兼容未迁移的旧库。"""
    row = dict(row) if not isinstance(row, dict) else row
    from ..user_fields_registry import read_user_fields

    return UserModel(
        user_id=row["user_id"],
        username=row["username"],
        password_hash=row["password_hash"],
        organization_id=row.get("organization_id") or "",
        email=row.get("email") or "",
        bound_ip=row.get("bound_ip") or "",
        token_version=row.get("token_version") or 0,
        employee_id=row.get("employee_id") or "",
        display_name=row.get("display_name") or "",
        department_id=row.get("department_id") or row.get("organization_id") or "",
        department_name=row.get("department_name") or "",
        division_name=row.get("division_name") or "",
        team_name=row.get("team_name") or "",
        position=row.get("position") or "",
        mobile=row.get("mobile") or "",
        account_status=row.get("account_status") or "active",
        personnel_source=row.get("personnel_source") or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )

def get_user_by_username(db, username: str) -> Optional[UserModel]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, "SELECT * FROM users WHERE username=?", (username,))
        row = cursor.fetchone()
    return row_to_user(row) if row is not None else None

def get_user_by_id(db, user_id: str) -> Optional[UserModel]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, "SELECT * FROM users WHERE user_id=?", (user_id,))
        row = cursor.fetchone()
    return row_to_user(row) if row is not None else None

def get_user_by_employee_id(db, employee_id: str) -> Optional[UserModel]:
    """按工号查用户（工号全局唯一；未设置工号的用户落库为 NULL）。"""
    if not employee_id:
        return None
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor, "SELECT * FROM users WHERE employee_id=?", (employee_id,)
        )
        row = cursor.fetchone()
    return row_to_user(row) if row is not None else None

def get_user_by_account(db, account: str) -> Optional[UserModel]:
    """按「用户名或工号」定位用户，供登录界面使用。

    先精确匹配用户名，未命中再按工号匹配；工号全局唯一，无需额外消歧。
    """
    if not account:
        return None
    return get_user_by_username(db, account) or get_user_by_employee_id(db, 
        account
    )

def update_user(db, user_data: UserModel):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """
            UPDATE users SET username=?, password_hash=?, organization_id=?, email=?, bound_ip=?, employee_id=?, updated_at=?
            WHERE user_id=?
            """,
            (
                user_data.username,
                user_data.password_hash,
                user_data.organization_id,
                user_data.email,
                user_data.bound_ip,
                user_data.employee_id or None,
                datetime.now().isoformat(),
                user_data.user_id,
            ),
        )

        from ..user_fields_registry import save_user_fields

        save_user_fields(db, cursor, user_data)

def bind_user_ip(db, username: str, ip: str) -> bool:
    user = get_user_by_username(db, username)
    if not user:
        return False
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE users SET bound_ip=?, updated_at=? WHERE username=?",
            (ip, datetime.now().isoformat(), username),
        )
        return cursor.rowcount > 0

def get_user_bound_ip(db, username: str) -> str:
    user = get_user_by_username(db, username)
    if not user:
        return ""
    return user.bound_ip

def increment_user_token_version(db, username: str, *, require_active: bool = False
) -> int:
    """单点登录：递增用户的 token 版本号并返回新值。

    每次登录调用，使该用户此前签发的 JWT（v 不等于新值）在下次鉴权时失效，
    从而实现「新登录踢掉旧登录」。
    """
    with db.get_connection() as conn:
        cursor = conn.cursor()
        active_clause = " AND account_status<>'disabled'" if require_active else ""
        db._execute(
            cursor,
            "UPDATE users SET token_version=COALESCE(token_version, 0)+1, "
            f"updated_at=? WHERE username=?{active_clause}",
            (datetime.now().isoformat(), username),
        )
        if cursor.rowcount <= 0:
            return 0
        db._execute(
            cursor,
            "SELECT token_version FROM users WHERE username=?",
            (username,),
        )
        row = cursor.fetchone()
    if not row:
        return 0
    value = dict(row) if not isinstance(row, dict) else row
    return int(value["token_version"])

def get_user_token_version(db, username: str) -> int:
    user = get_user_by_username(db, username)
    return user.token_version if user else 0

def touch_user_activity(db, username: str, timestamp: float) -> bool:
    """更新用户最近活跃时间（Unix 时间戳），供 idle-logout 滑动续期。

    数据落在共享数据库，多 worker 下任意进程都能读到同一个登录活跃状态。
    """
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE users SET last_activity_at=? WHERE username=?",
            (timestamp, username),
        )
        return cursor.rowcount > 0

def get_user_activity_time(db, username: str) -> Optional[float]:
    """读取用户最近活跃时间（Unix 时间戳），无记录返回 None。"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT last_activity_at FROM users WHERE username=?",
            (username,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    value = row["last_activity_at"] if isinstance(row, dict) else row[0]
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clear_user_activity(db, username: str) -> bool:
    """清空用户活跃时间（登出时调用）。"""
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE users SET last_activity_at=NULL WHERE username=?",
            (username,),
        )
        return cursor.rowcount > 0

def register_user(db,
    username: str,
    password: str,
    employee_id: str = "",
    bound_ip: str = "",
) -> Optional[UserModel]:
    """注册用户；用户名或工号任一已存在都返回 None（错误提示由调用方区分）。

    机构ID 与邮箱不是注册项：注册时写空，前者可由管理员维护（用于系统提示词注入），
    后者可由用户登录后通过资料接口补填。
    """
    if get_user_by_username(db, username):
        return None
    if employee_id and get_user_by_employee_id(db, employee_id):
        return None

    now = datetime.now().isoformat()
    password_hash = hash_password(password)
    user = UserModel(
        user_id=str(uuid.uuid4()),
        username=username,
        password_hash=password_hash,
        organization_id="",
        email="",
        bound_ip=bound_ip or "",
        employee_id=employee_id or "",
        created_at=now,
        updated_at=now,
    )
    try:
        create_user(db, user)
    except Exception as e:  # noqa: BLE001
        # 并发场景由唯一索引兜底（用户名/工号撞车）
        logger.warning(f"注册用户失败（唯一约束冲突）| username={username}: {e}")
        return None
    return user

def verify_user_password(db, account: str, password: str) -> Optional[UserModel]:
    """校验密码登录。``account`` 可以是用户名或工号（工号全局唯一）。"""
    user = get_user_by_account(db, account)
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user

def get_or_create_passwordless_user(db,
    username: str,
    user_id: str = "",
) -> tuple[Optional[UserModel], bool]:
    """免密登录：按用户名查找用户，不存在则注册。

    - 用户名已存在：直接返回该用户，user_id 不参与匹配；
    - 用户名不存在：user_id 为空或 "0" 时自动生成唯一 ID，否则使用传入值，
      传入值已被其他用户占用时返回 (None, False)。

    返回 (user, created)。
    """
    existing = get_user_by_username(db, username)
    if existing:
        return existing, False

    if not user_id or user_id == "0":
        user_id = str(uuid.uuid4())
    elif get_user_by_id(db, user_id):
        return None, False

    now = datetime.now().isoformat()
    user = UserModel(
        user_id=user_id,
        username=username,
        # 免密用户不通过密码登录，写入随机密码哈希防止被猜测
        password_hash=hash_password(uuid.uuid4().hex),
        organization_id="",
        email="",
        bound_ip="",
        created_at=now,
        updated_at=now,
    )
    create_user(db, user)
    return user, True

def update_user_password(db, username: str, new_password_hash: str) -> bool:
    user = get_user_by_username(db, username)
    if not user:
        return False

    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE users SET password_hash=?, updated_at=? WHERE username=?",
            (new_password_hash, datetime.now().isoformat(), username),
        )
        return cursor.rowcount > 0

def delete_user(db, username: str) -> bool:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, "DELETE FROM users WHERE username=?", (username,))
        return cursor.rowcount > 0

def get_or_create_default_user(db) -> UserModel:
    from ..user_fields_registry import get_or_create_default_admin

    return get_or_create_default_admin(db)

def list_users(db, limit: int = 50, offset: int = 0) -> list[UserModel]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        rows = cursor.fetchall()
    return [row_to_user(row) for row in rows]

def count_users(db) -> int:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, "SELECT COUNT(*) AS total FROM users")
        row = cursor.fetchone()
    return int(row["total"]) if row else 0
