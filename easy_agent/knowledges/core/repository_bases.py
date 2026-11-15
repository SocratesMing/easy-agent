"""知识库与部门管理数据访问：库 CRUD、可见库查询、部门管理员台账。"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from easy_agent.knowledges.core.repository_base import _now
from easy_agent.utils.auth import is_super_admin

_BASE_UPDATE_FIELDS = {
    "remote_dataset_id", "name", "description", "status",
    "deleted_at", "deleted_by", "access_restricted", "meta_backfilled",
}
_DOCUMENT_UPDATE_FIELDS = {
    "remote_document_id", "folder_id", "status", "progress", "error_code",
    "error_message", "deleted_at", "deleted_by",
    # 业务元数据与解析计时
    "source", "source_record_id", "batch_id", "report_title", "report_tag",
    "report_second_source", "publish_date",
    "parse_started_at",
}
_OBJECT_UPDATE_FIELDS = {
    "storage_key", "status", "size_bytes", "sha256", "error_code",
    "error_message", "stored_at", "deleted_at",
}
_OPERATION_UPDATE_FIELDS = {
    "phase", "status", "current_count", "total_count", "progress",
    "retryable", "error_code", "error_message",
}
_SYNC_ITEM_UPDATE_FIELDS = {
    "document_id", "status", "attempts", "last_error", "publish_date",
}

# 文档查询统一带出原始文件对象状态，供服务层组装 DocumentSummary
_DOCUMENT_SELECT = """
    SELECT d.*, COALESCE(o.status, 'unmanaged') AS original_status,
           o.sha256 AS original_sha256, o.storage_key AS original_storage_key,
           o.provider AS original_provider
    FROM knowledge_documents d
    LEFT JOIN knowledge_document_objects o
      ON o.document_id=d.id AND o.object_version=1
"""

_MANAGER_SELECT = """
    SELECT m.id,
           m.user_id,
           m.department_id,
           m.granted_by AS granted_by_user_id,
           m.granted_at,
           m.updated_at,
           u.username,
           u.display_name,
           u.account_status,
           u.department_name,
           g.username     AS granted_by_username,
           g.display_name AS granted_by_display_name
      FROM knowledge_department_managers m
      JOIN users u ON u.user_id = m.user_id
      LEFT JOIN users g ON g.user_id = m.granted_by
"""
def _normalize_manager_row(row: Mapping[str, object]) -> dict[str, Any]:
    """补前端展示字段：granted_by 优先姓名，department_name 兜底用 id。"""

    d = dict(row)
    d["granted_by"] = (
        d.get("granted_by_display_name")
        or d.get("granted_by_username")
        or d.get("granted_by_user_id", "")
    )
    if not d.get("department_name"):
        d["department_name"] = d.get("department_id", "")
    return d


class BaseRepositoryMixin:
    """知识库/部门相关查询与写入；由 KnowledgeRepository 组合。"""

    # ---------------- 知识库（个人/公共空间） ----------------

    def create_base(
        self,
        *,
        name: str,
        description: str,
        space_type: str,
        owner_user_id: str,
        department_id: str | None,
        embedding_model: str,
        status: str = "creating",
        base_id: str | None = None,
        meta_backfilled: int = 0,
    ) -> dict[str, Any]:
        base_id = base_id or str(uuid.uuid4())
        timestamp = _now()
        self._execute_write(
            """
            INSERT INTO knowledge_bases
                (id, remote_dataset_id, name, description, space_type,
                 owner_user_id, department_id, embedding_model, status,
                 meta_backfilled, created_at, updated_at)
            VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                base_id, name, description, space_type, owner_user_id,
                department_id, embedding_model, status, meta_backfilled,
                timestamp, timestamp,
            ),
        )
        return self.get_base(base_id)

    def get_base(self, base_id: str) -> dict[str, Any] | None:
        return self._fetch_one("SELECT * FROM knowledge_bases WHERE id=?", (base_id,))

    def find_base_by_name(self, name: str) -> dict[str, Any] | None:
        """按名称查找未删除的知识库（同步任务幂等查找）。"""
        return self._fetch_one(
            "SELECT * FROM knowledge_bases WHERE name=? "
            "AND status NOT IN ('deleted', 'deleting') "
            "ORDER BY created_at LIMIT 1",
            (name,),
        )

    def list_candidate_bases(
        self,
        *,
        user_id: str,
        department_id: str | None,
        include_all_team_spaces: bool = False,
    ) -> list[dict[str, Any]]:
        """列出一个用户可见的知识库。

        公共（team）空间默认对本部门账号可见（只读）；跨部门访问需要
        owner 或单库权限行。access_restricted=1 的库进入勾选名单模式：
        同部门默认可见与部门授权行均被屏蔽，仅勾选人员、owner、admin 可见。
        """
        parameters: list[object] = [user_id, user_id, user_id]
        department_sql = ""
        if department_id:
            department_sql = (
                " OR (p.subject_type='department' AND p.subject_id=?"
                " AND COALESCE(b.access_restricted, 0)=0)"
            )
            parameters.append(department_id)
        admin_sql = " OR b.space_type='team'" if include_all_team_spaces else ""
        return self._fetch_all(
            f"""
            SELECT DISTINCT b.*,
                   (SELECT COUNT(*) FROM knowledge_documents d
                    WHERE d.base_id=b.id AND d.status!='deleted') AS document_count
            FROM knowledge_bases b
            LEFT JOIN knowledge_permissions p ON p.base_id=b.id
            WHERE b.status!='deleted' AND (b.owner_user_id=?
               OR (p.subject_type='user' AND p.subject_id=?)
               OR (b.space_type='team' AND COALESCE(b.access_restricted, 0)=0 AND EXISTS (
                    SELECT 1 FROM users du
                    WHERE du.user_id=?
                      AND COALESCE(NULLIF(du.department_id, ''), du.organization_id)=b.department_id))
               {department_sql}{admin_sql})
            ORDER BY b.updated_at DESC
            """,
            tuple(parameters),
        )

    def update_base(self, base_id: str, **fields: object) -> dict[str, Any] | None:
        self._update_by_id("knowledge_bases", base_id, _BASE_UPDATE_FIELDS, fields)
        return self.get_base(base_id)

    def count_documents(self, base_id: str) -> int:
        return self._count(
            "SELECT COUNT(*) AS count FROM knowledge_documents "
            "WHERE base_id=? AND status!='deleted'",
            (base_id,),
        )

    # ---------------- 部门 / 知识库管理员 ----------------

    def list_active_departments(self) -> list[dict[str, Any]]:
        """列出至少有一名在职账号的部门（人员表为第一阶段的权威来源）。"""
        expression = "COALESCE(NULLIF(department_id, ''), NULLIF(organization_id, ''))"
        return self._fetch_all(
            f"""
            SELECT {expression} AS department_id,
                   MAX(COALESCE(department_name, '')) AS department_name
            FROM users
            WHERE account_status='active' AND {expression} IS NOT NULL
            GROUP BY {expression}
            ORDER BY department_name, department_id
            """
        )

    # ---------------- 知识库管理员 ----------------

    def is_department_manager(self, user_id: str, department_id: str) -> bool:
        """用户是否是指定部门（且账号仍在该部门）的知识库管理员。"""
        row = self._fetch_one(
            """
            SELECT 1 FROM knowledge_department_managers m
            JOIN users u ON u.user_id = m.user_id
            WHERE m.user_id=? AND m.department_id=?
              AND u.account_status='active'
              AND COALESCE(u.department_id, u.organization_id, '') = m.department_id
            """,
            (user_id, department_id),
        )
        return row is not None

    def grant_department_manager(
        self, *, user_id: str, department_id: str, granted_by: str
    ) -> dict[str, Any]:
        """授予知识库管理员权限，返回包含用户信息的完整行（用于构造 API 响应）。"""
        timestamp = _now()
        department_id = department_id.strip()
        if not department_id:
            raise ValueError("department_id 不能为空")
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT user_id, username, account_status, "
                "COALESCE(NULLIF(department_id, ''), organization_id) AS user_dept "
                "FROM users WHERE user_id=?",
                (user_id,),
            )
            user = cursor.fetchone()
            if user is None:
                raise LookupError("用户不存在")
            user = dict(user)
            if is_super_admin(user["username"]):
                raise ValueError("超级管理员已具有全局管理权限")
            if str(user.get("account_status") or "active") != "active":
                raise ValueError("停用账号不能获得知识库管理员权限")
            if str(user.get("user_dept") or "").strip() != department_id:
                raise ValueError(
                    f"账号所属部门与授权部门不一致（账号部门: {user.get('user_dept')}）"
                )
            self.db._execute(
                cursor,
                "SELECT id FROM knowledge_department_managers WHERE user_id=? AND department_id=?",
                (user_id, department_id),
            )
            existing = cursor.fetchone()
            if existing is None:
                self.db._execute(
                    cursor,
                    """INSERT INTO knowledge_department_managers
                        (id, user_id, department_id, granted_by, granted_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4()), user_id, department_id, granted_by, timestamp, timestamp),
                )
            else:
                self.db._execute(
                    cursor,
                    "UPDATE knowledge_department_managers SET granted_by=?, updated_at=? "
                    "WHERE user_id=? AND department_id=?",
                    (granted_by, timestamp, user_id, department_id),
                )
            self.db._execute(
                cursor,
                f"""{_MANAGER_SELECT}
                   WHERE m.user_id=? AND m.department_id=?""",
                (user_id, department_id),
            )
            row = cursor.fetchone()
            if row is None:
                raise RuntimeError("知识库管理员权限保存失败")
            return _normalize_manager_row(row)

    def revoke_department_manager(self, user_id: str, department_id: str) -> bool:
        return (
            self._execute_write(
                "DELETE FROM knowledge_department_managers WHERE user_id=? AND department_id=?",
                (user_id, department_id),
            )
            > 0
        )

    def list_department_managers(
        self, department_id: str | None = None
    ) -> list[dict[str, Any]]:
        """列出知识库管理员；不传 department_id 时返回全部。"""
        base_sql = _MANAGER_SELECT
        if department_id:
            base_sql += " WHERE m.department_id=? ORDER BY u.display_name, u.username"
            rows = self._fetch_all(base_sql, (department_id,))
        else:
            base_sql += " ORDER BY m.department_id, u.display_name, u.username"
            rows = self._fetch_all(base_sql)
        return [_normalize_manager_row(row) for row in rows]
