"""Database operations owned by the personnel module."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

import pymysql

from ..db import Database
from ..utils.auth import hash_password
from .models import PersonnelCreateRequest, PersonnelUpdateRequest

DEFAULT_PASSWORD = "123456"


class PersonnelConflictError(ValueError):
    pass


class PersonnelNotFoundError(LookupError):
    pass


def _row_to_dict(row) -> dict:
    value = dict(row) if not isinstance(row, dict) else row
    return {
        "user_id": value["user_id"],
        "username": value["username"],
        "employee_id": value.get("employee_id", "") or "",
        "display_name": value.get("display_name", "") or "",
        "department_id": value.get("department_id", "")
        or value.get("organization_id", "")
        or "",
        "department_name": value.get("department_name", "") or "",
        "email": value.get("email", "") or "",
        "position": value.get("position", "") or "",
        "mobile": value.get("mobile", "") or "",
        "account_status": value.get("account_status", "active") or "active",
        "source": value.get("personnel_source", "") or "",
        "created_at": value["created_at"],
        "updated_at": value["updated_at"],
    }


def list_personnel(
    db: Database,
    *,
    keyword: str = "",
    account_status: str = "",
    department_id: str = "",
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[dict]]:
    clauses: list[str] = []
    params: list[object] = []
    if keyword:
        clauses.append(
            "(username LIKE ? OR display_name LIKE ? OR employee_id LIKE ? OR "
            "department_name LIKE ? OR email LIKE ? OR mobile LIKE ?)"
        )
        wildcard = f"%{keyword}%"
        params.extend([wildcard] * 6)
    if account_status:
        clauses.append("account_status=?")
        params.append(account_status)
    if department_id:
        clauses.append("department_id=?")
        params.append(department_id)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    with db.get_connection() as connection:
        cursor = connection.cursor()
        db._execute(cursor, f"SELECT COUNT(*) AS total FROM users{where}", tuple(params))
        count_row = cursor.fetchone()
        total = int((dict(count_row) if not isinstance(count_row, dict) else count_row)["total"])
        db._execute(
            cursor,
            f"SELECT * FROM users{where} "
            "ORDER BY department_name, display_name, username LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        rows = cursor.fetchall()
    return total, [_row_to_dict(row) for row in rows]


def _assert_employee_id_available(db: Database, employee_id: str, user_id: str = "") -> None:
    if not employee_id:
        return
    with db.get_connection() as connection:
        cursor = connection.cursor()
        if user_id:
            db._execute(
                cursor,
                "SELECT username FROM users WHERE employee_id=? AND user_id<>?",
                (employee_id, user_id),
            )
        else:
            db._execute(cursor, "SELECT username FROM users WHERE employee_id=?", (employee_id,))
        row = cursor.fetchone()
    if row:
        owner = (dict(row) if not isinstance(row, dict) else row)["username"]
        raise PersonnelConflictError(f"员工编号已被账号 {owner} 使用")


def create_personnel(db: Database, item: PersonnelCreateRequest) -> dict:
    if item.username == "admin":
        raise PersonnelConflictError("admin 账号为系统管理员，不可手动覆盖")
    if db.get_user_by_username(item.username):
        raise PersonnelConflictError("账号已存在")
    _assert_employee_id_available(db, item.employee_id)
    now = datetime.now().isoformat()
    try:
        with db.get_connection() as connection:
            cursor = connection.cursor()
            db._execute(
                cursor,
                """INSERT INTO users (
                    user_id, username, password_hash, organization_id, email, bound_ip,
                    token_version, employee_id, display_name, department_id,
                    department_name, position, mobile, account_status, personnel_source,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, '', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(uuid.uuid4()), item.username, hash_password(DEFAULT_PASSWORD),
                    item.department_id, item.email, item.employee_id or None,
                    item.display_name, item.department_id, item.department_name,
                    item.position, item.mobile, item.account_status, item.source,
                    now, now,
                ),
            )
    except (sqlite3.IntegrityError, pymysql.IntegrityError) as exc:
        raise PersonnelConflictError("账号或员工编号已存在") from exc
    user = db.get_user_by_username(item.username)
    return _row_to_dict(user.__dict__)


def update_personnel(db: Database, user_id: str, item: PersonnelUpdateRequest) -> dict:
    current = db.get_user_by_id(user_id)
    if not current:
        raise PersonnelNotFoundError("用户不存在")
    if current.username == "admin":
        raise PersonnelConflictError("admin 账号不可在人员管理中修改")
    _assert_employee_id_available(db, item.employee_id, user_id)
    now = datetime.now().isoformat()
    try:
        with db.get_connection() as connection:
            cursor = connection.cursor()
            db._execute(
                cursor,
                """UPDATE users SET organization_id=?, email=?, employee_id=?, display_name=?,
                    department_id=?, department_name=?, position=?, mobile=?, account_status=?,
                    personnel_source=?, token_version=CASE WHEN ?='disabled' THEN token_version+1 ELSE token_version END,
                    updated_at=? WHERE user_id=?""",
                (
                    item.department_id, item.email, item.employee_id or None,
                    item.display_name, item.department_id, item.department_name,
                    item.position, item.mobile, item.account_status, item.source,
                    item.account_status, now, user_id,
                ),
            )
    except (sqlite3.IntegrityError, pymysql.IntegrityError) as exc:
        raise PersonnelConflictError("员工编号已被其他账号使用") from exc
    updated = db.get_user_by_id(user_id)
    return _row_to_dict(updated.__dict__)


def _import_personnel_transaction(
    db: Database, items: list[PersonnelCreateRequest]
) -> tuple[int, int]:
    """Upsert a fully validated workbook in one database transaction."""

    with db.get_connection() as connection:
        cursor = connection.cursor()
        db._execute(cursor, "SELECT user_id, username, employee_id FROM users")
        existing_rows = [dict(row) if not isinstance(row, dict) else row for row in cursor.fetchall()]
        by_username = {row["username"]: row for row in existing_rows}
        employee_owner = {
            row.get("employee_id"): row["username"]
            for row in existing_rows
            if row.get("employee_id")
        }
        workbook_employee_owner: dict[str, str] = {}
        conflicts: list[str] = []
        for item in items:
            if not item.employee_id:
                continue
            owner = employee_owner.get(item.employee_id)
            if owner and owner != item.username:
                conflicts.append(f"员工编号 {item.employee_id} 已被账号 {owner} 使用")
            sheet_owner = workbook_employee_owner.get(item.employee_id)
            if sheet_owner and sheet_owner != item.username:
                conflicts.append(
                    f"员工编号 {item.employee_id} 被账号 {sheet_owner} 和 {item.username} 重复使用"
                )
            workbook_employee_owner[item.employee_id] = item.username
        if conflicts:
            raise PersonnelConflictError("；".join(dict.fromkeys(conflicts)))

        created = 0
        updated = 0
        now = datetime.now().isoformat()
        # All newly imported accounts intentionally share the documented initial
        # password. Hash it once per workbook instead of running thousands of
        # serial bcrypt operations while a database transaction is open.
        default_password_hash = hash_password(DEFAULT_PASSWORD)
        for item in items:
            existing = by_username.get(item.username)
            if existing:
                db._execute(
                    cursor,
                    """UPDATE users SET organization_id=?, email=?, employee_id=?, display_name=?,
                        department_id=?, department_name=?, position=?, mobile=?, account_status=?,
                        personnel_source=?, token_version=CASE WHEN ?='disabled' THEN token_version+1 ELSE token_version END,
                        updated_at=? WHERE username=?""",
                    (
                        item.department_id, item.email, item.employee_id or None, item.display_name,
                        item.department_id, item.department_name, item.position, item.mobile,
                        item.account_status, item.source, item.account_status, now, item.username,
                    ),
                )
                updated += 1
            else:
                db._execute(
                    cursor,
                    """INSERT INTO users (
                        user_id, username, password_hash, organization_id, email, bound_ip,
                        token_version, employee_id, display_name, department_id,
                        department_name, position, mobile, account_status, personnel_source,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, '', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()), item.username, default_password_hash,
                        item.department_id, item.email, item.employee_id or None, item.display_name,
                        item.department_id, item.department_name, item.position, item.mobile,
                        item.account_status, item.source, now, now,
                    ),
                )
                created += 1
        return created, updated


def import_personnel(db: Database, items: list[PersonnelCreateRequest]) -> tuple[int, int]:
    """Upsert a workbook atomically and translate database races to HTTP 409."""

    try:
        return _import_personnel_transaction(db, items)
    except (sqlite3.IntegrityError, pymysql.IntegrityError) as exc:
        raise PersonnelConflictError("账号或员工编号与现有人员冲突") from exc
