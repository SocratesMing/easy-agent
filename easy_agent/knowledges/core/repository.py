"""知识库 CRUD 数据访问层。"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from easy_agent.db.database import Database

logger = logging.getLogger(__name__)

_BASE_UPDATE_FIELDS = {
    "remote_dataset_id", "name", "description", "status",
    "deleted_at", "deleted_by", "access_restricted",
}
_DOCUMENT_UPDATE_FIELDS = {
    "remote_document_id", "folder_id", "status", "progress", "error_code",
    "error_message", "deleted_at", "deleted_by",
    # 业务元数据与解析计时
    "source", "source_record_id", "report_title", "report_tag",
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

# 文档查询统一带出原始文件对象状态，供服务层组装 DocumentSummary
_DOCUMENT_SELECT = """
    SELECT d.*, COALESCE(o.status, 'unmanaged') AS original_status,
           o.sha256 AS original_sha256, o.storage_key AS original_storage_key,
           o.provider AS original_provider
    FROM knowledge_documents d
    LEFT JOIN knowledge_document_objects o
      ON o.document_id=d.id AND o.object_version=1
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_dict(row: object | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class KnowledgeRepository:
    """知识库模块唯一数据访问入口（事务由 Database.get_connection 管理）。"""

    def __init__(self, db: Database):
        self.db = db

    # ---------------- 通用内部辅助 ----------------

    def _update_by_id(
        self, table: str, row_id: str, allowed: set[str], fields: Mapping[str, object]
    ) -> None:
        """按白名单过滤字段后更新单行，并刷新 updated_at。"""
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{column}=?" for column in updates)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"UPDATE {table} SET {assignments} WHERE id=?",
                (*updates.values(), row_id),
            )

    def _count(self, sql: str, params: tuple) -> int:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, sql, params)
            row = cursor.fetchone()
            return int(row["count"] if isinstance(row, dict) else row[0])

    @staticmethod
    def _document_filters(
        base_id: str,
        *,
        folder_id: str | None = None,
        direct_only: bool = False,
        status: str | None = None,
        query: str | None = None,
        publish_date_from: str | None = None,
        publish_date_to: str | None = None,
        report_tags: list[str] | None = None,
        report_second_sources: list[str] | None = None,
        report_title_contains: str | None = None,
    ) -> tuple[str, list[object]]:
        clauses = ["d.base_id=?", "d.status!='deleted'"]
        parameters: list[object] = [base_id]
        if direct_only or folder_id is not None:
            if folder_id is None:
                clauses.append("d.folder_id IS NULL")
            else:
                clauses.append("d.folder_id=?")
                parameters.append(folder_id)
        if status:
            clauses.append("d.status=?")
            parameters.append(status)
        if query:
            clauses.append("LOWER(d.name) LIKE ?")
            parameters.append(f"%{query.lower()}%")
        # 元数据硬过滤：publish_date 为 NULL 的文档不会命中任何日期区间
        if publish_date_from:
            clauses.append("d.publish_date >= ?")
            parameters.append(publish_date_from)
        if publish_date_to:
            clauses.append("d.publish_date <= ?")
            parameters.append(publish_date_to)
        if report_tags:
            placeholders = ",".join("?" for _ in report_tags)
            clauses.append(f"d.report_tag IN ({placeholders})")
            parameters.extend(report_tags)
        if report_second_sources:
            placeholders = ",".join("?" for _ in report_second_sources)
            clauses.append(f"d.report_second_source IN ({placeholders})")
            parameters.extend(report_second_sources)
        if report_title_contains:
            clauses.append("LOWER(COALESCE(d.report_title, d.name)) LIKE ?")
            parameters.append(f"%{report_title_contains.lower()}%")
        return " AND ".join(clauses), parameters

    def _select_documents_where(self, where: str, params: tuple) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, f"{_DOCUMENT_SELECT} WHERE {where}", params)
            return [dict(row) for row in cursor.fetchall()]

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
    ) -> dict[str, Any]:
        base_id = base_id or str(uuid.uuid4())
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                INSERT INTO knowledge_bases
                    (id, remote_dataset_id, name, description, space_type,
                     owner_user_id, department_id, embedding_model, status,
                     created_at, updated_at)
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    base_id, name, description, space_type, owner_user_id,
                    department_id, embedding_model, status, timestamp, timestamp,
                ),
            )
        return self.get_base(base_id)

    def get_base(self, base_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, "SELECT * FROM knowledge_bases WHERE id=?", (base_id,))
            return _row_dict(cursor.fetchone())

    def find_base_by_name(self, name: str) -> dict[str, Any] | None:
        """按名称查找未删除的知识库（同步任务幂等查找）。"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT * FROM knowledge_bases WHERE name=? "
                "AND status NOT IN ('deleted', 'deleting') "
                "ORDER BY created_at LIMIT 1",
                (name,),
            )
            return _row_dict(cursor.fetchone())

    def list_candidate_bases(
        self,
        *,
        user_id: str,
        department_id: str | None,
        include_all_team_spaces: bool = False,
    ) -> list[dict[str, Any]]:
        """列出一个用户可见的知识库。

        公共（team）空间默认对本部门账号可见（只读）；跨部门访问需要
        owner、空间管理白名单或单库权限行。access_restricted=1 的库进入
        勾选名单模式：同部门默认可见与部门授权行均被屏蔽，仅勾选人员、
        owner、空间管理白名单（含 admin）可见。
        """
        parameters: list[object] = [user_id, user_id, user_id, user_id]
        department_sql = ""
        if department_id:
            department_sql = (
                " OR (p.subject_type='department' AND p.subject_id=?"
                " AND COALESCE(b.access_restricted, 0)=0)"
            )
            parameters.append(department_id)
        admin_sql = " OR b.space_type='team'" if include_all_team_spaces else ""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT DISTINCT b.*,
                       (SELECT COUNT(*) FROM knowledge_documents d
                        WHERE d.base_id=b.id AND d.status!='deleted') AS document_count
                FROM knowledge_bases b
                LEFT JOIN knowledge_permissions p ON p.base_id=b.id
                WHERE b.status!='deleted' AND (b.owner_user_id=?
                   OR (p.subject_type='user' AND p.subject_id=?)
                   OR (b.space_type='team' AND EXISTS (
                        SELECT 1 FROM knowledge_team_space_managers m
                        JOIN users mu ON mu.user_id=m.user_id
                        WHERE m.user_id=?
                          AND COALESCE(NULLIF(mu.department_id, ''), mu.organization_id)=b.department_id))
                   OR (b.space_type='team' AND COALESCE(b.access_restricted, 0)=0 AND EXISTS (
                        SELECT 1 FROM users du
                        WHERE du.user_id=?
                          AND COALESCE(NULLIF(du.department_id, ''), du.organization_id)=b.department_id))
                   {department_sql}{admin_sql})
                ORDER BY b.updated_at DESC
                """,
                tuple(parameters),
            )
            return [dict(row) for row in cursor.fetchall()]

    def update_base(self, base_id: str, **fields: object) -> dict[str, Any] | None:
        self._update_by_id("knowledge_bases", base_id, _BASE_UPDATE_FIELDS, fields)
        return self.get_base(base_id)

    def count_documents(self, base_id: str) -> int:
        return self._count(
            "SELECT COUNT(*) AS count FROM knowledge_documents "
            "WHERE base_id=? AND status!='deleted'",
            (base_id,),
        )

    # ---------------- 公共空间白名单（管理/查看） ----------------

    def is_team_space_manager(self, user_id: str) -> bool:
        """用户是否持有立即生效的公共空间管理授权。"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT 1 FROM knowledge_team_space_managers m
                JOIN users u ON u.user_id=m.user_id
                WHERE m.user_id=?
                  AND u.account_status='active'
                  AND u.username<>'admin'
                  AND COALESCE(u.department_id, u.organization_id, '')<>''
                """,
                (user_id,),
            )
            return cursor.fetchone() is not None

    def list_active_departments(self) -> list[dict[str, Any]]:
        """列出至少有一名在职账号的部门（人员表为第一阶段的权威来源）。"""
        expression = "COALESCE(NULLIF(department_id, ''), NULLIF(organization_id, ''))"
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT {expression} AS department_id,
                       MAX(COALESCE(department_name, '')) AS department_name
                FROM users
                WHERE account_status='active' AND {expression} IS NOT NULL
                GROUP BY {expression}
                ORDER BY department_name, department_id
                """,
            )
            return [dict(row) for row in cursor.fetchall()]

    # 公共空间 manager/viewer 白名单共用同一套 grant 表结构；
    # 表名由代码内硬编码传入，不来自用户输入。
    def _list_team_space_grants(self, table: str) -> list[dict[str, Any]]:
        """列出全部授权（含已停用账号，便于管理员显式回收）。"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT g.user_id, g.granted_by, g.granted_at, g.updated_at,
                       u.username, u.display_name, u.account_status,
                       COALESCE(NULLIF(u.department_id, ''), u.organization_id) AS department_id,
                       u.department_name
                FROM {table} g JOIN users u ON u.user_id=g.user_id
                ORDER BY u.department_name, u.display_name, u.username
                """,
            )
            return [dict(row) for row in cursor.fetchall()]

    def _grant_team_space_permission(
        self,
        *,
        table: str,
        user_id: str,
        granted_by: str,
        label: str,
        require_department: bool,
    ) -> dict[str, Any]:
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT user_id, username, account_status, "
                "COALESCE(NULLIF(department_id, ''), organization_id) AS department_id "
                "FROM users WHERE user_id=?",
                (user_id,),
            )
            user = cursor.fetchone()
            if user is None:
                raise LookupError("用户不存在")
            user = dict(user)
            if user["username"] == "admin":
                raise ValueError(f"admin 已具有全局公共空间{label}权限")
            if str(user.get("account_status") or "active") != "active":
                raise ValueError(f"停用账号不能获得公共空间{label}权限")
            if require_department and not str(user.get("department_id") or "").strip():
                raise ValueError("账号缺少部门，不能获得公共空间管理权限")
            self.db._execute(cursor, f"SELECT user_id FROM {table} WHERE user_id=?", (user_id,))
            if cursor.fetchone() is None:
                self.db._execute(
                    cursor,
                    f"INSERT INTO {table} (user_id, granted_by, granted_at, updated_at) VALUES (?, ?, ?, ?)",
                    (user_id, granted_by, timestamp, timestamp),
                )
            else:
                self.db._execute(
                    cursor,
                    f"UPDATE {table} SET granted_by=?, updated_at=? WHERE user_id=?",
                    (granted_by, timestamp, user_id),
                )
        for item in self._list_team_space_grants(table):
            if str(item["user_id"]) == user_id:
                return item
        raise RuntimeError(f"公共空间{label}权限保存失败")

    def _revoke_team_space_grant(self, table: str, user_id: str) -> bool:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, f"DELETE FROM {table} WHERE user_id=?", (user_id,))
            return cursor.rowcount > 0

    def list_team_space_managers(self) -> list[dict[str, Any]]:
        return self._list_team_space_grants("knowledge_team_space_managers")

    def grant_team_space_manager(self, *, user_id: str, granted_by: str) -> dict[str, Any]:
        return self._grant_team_space_permission(
            table="knowledge_team_space_managers",
            user_id=user_id, granted_by=granted_by, label="管理", require_department=True,
        )

    def revoke_team_space_manager(self, user_id: str) -> bool:
        return self._revoke_team_space_grant("knowledge_team_space_managers", user_id)

    # ---------------- 文件夹 ----------------

    def create_folder(
        self, *, base_id: str, name: str, parent_id: str | None = None, sort_order: int = 0
    ) -> dict[str, Any]:
        folder_id = str(uuid.uuid4())
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                INSERT INTO knowledge_folders
                    (id, base_id, parent_id, name, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (folder_id, base_id, parent_id, name, sort_order, timestamp, timestamp),
            )
        return self.get_folder(folder_id)

    def get_folder(self, folder_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, "SELECT * FROM knowledge_folders WHERE id=?", (folder_id,))
            return _row_dict(cursor.fetchone())

    def find_folder_by_name(
        self, base_id: str, name: str, parent_id: str | None = None
    ) -> dict[str, Any] | None:
        """按 (base_id, parent_id, name) 查找文件夹；parent_id 为 None 时匹配根级。

        唯一约束放宽为 (base_id, parent_id, name) 后，parent_id 为 NULL 的
        根级同名不再受数据库唯一性保护（NULL 不参与唯一判断），由本方法
        供服务层显式查重兜底。
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            if parent_id is None:
                self.db._execute(
                    cursor,
                    "SELECT * FROM knowledge_folders "
                    "WHERE base_id=? AND name=? AND parent_id IS NULL",
                    (base_id, name),
                )
            else:
                self.db._execute(
                    cursor,
                    "SELECT * FROM knowledge_folders WHERE base_id=? AND name=? AND parent_id=?",
                    (base_id, name, parent_id),
                )
            return _row_dict(cursor.fetchone())

    def list_folders(self, base_id: str) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT f.*, COUNT(DISTINCT d.id) AS document_count,
                       COUNT(DISTINCT child.id) AS child_count
                FROM knowledge_folders f
                LEFT JOIN knowledge_documents d ON d.folder_id=f.id AND d.status!='deleted'
                LEFT JOIN knowledge_folders child ON child.parent_id=f.id
                WHERE f.base_id=?
                GROUP BY f.id
                ORDER BY f.sort_order, f.name
                """,
                (base_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def update_folder(self, folder_id: str, *, name: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "UPDATE knowledge_folders SET name=?, updated_at=? WHERE id=?",
                (name, _now(), folder_id),
            )
        return self.get_folder(folder_id)

    def delete_empty_folder(self, folder_id: str) -> bool:
        """仅删除既无文档也无子文件夹的空目录。"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            if self._count(
                "SELECT COUNT(*) AS count FROM knowledge_documents WHERE folder_id=?", (folder_id,)
            ):
                return False
            if self._count(
                "SELECT COUNT(*) AS count FROM knowledge_folders WHERE parent_id=?", (folder_id,)
            ):
                return False
            self.db._execute(cursor, "DELETE FROM knowledge_folders WHERE id=?", (folder_id,))
            return cursor.rowcount > 0

    # ---------------- 文档 ----------------

    def create_document(
        self,
        *,
        base_id: str,
        folder_id: str | None,
        name: str,
        content_type: str,
        size_bytes: int,
        created_by: str,
        status: str = "pending",
        document_id: str | None = None,
        source: str | None = None,
        source_record_id: str | None = None,
        report_title: str | None = None,
        report_tag: str | None = None,
        report_second_source: str | None = None,
        publish_date: str | None = None,
    ) -> dict[str, Any]:
        document_id = document_id or str(uuid.uuid4())
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                INSERT INTO knowledge_documents
                    (id, remote_document_id, base_id, folder_id, name,
                     content_type, size_bytes, status, progress, error_code,
                     error_message, created_by, created_at, updated_at,
                     source, source_record_id, report_title, report_tag,
                     report_second_source, publish_date)
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?)
                """,
                (
                    document_id, base_id, folder_id, name, content_type,
                    size_bytes, status, created_by, timestamp, timestamp,
                    source, source_record_id, report_title, report_tag,
                    report_second_source, publish_date,
                ),
            )
        return self.get_document(document_id)

    def find_document_by_source_record(
        self,
        source: str,
        source_record_id: str,
        *,
        base_id: str | None = None,
        include_deleted: bool = False,
    ) -> dict[str, Any] | None:
        """按业务来源 + 源记录 ID 查本地文档（同步幂等判定）。

        ``base_id`` 非空时只在该知识库内查找（同步目标库判定）：同一业务记录
        在旧库（已删除）的历史行不应被当作“已入库”，否则新库永远补不回文档。
        ``include_deleted`` 为真时一并返回软删除行，供调用方决定是跳过还是
        重新入库（同库内软删除行仍占用幂等键）。
        """
        clauses = ["d.source=?", "d.source_record_id=?"]
        parameters: list[object] = [source, source_record_id]
        if base_id is not None:
            clauses.append("d.base_id=?")
            parameters.append(base_id)
        if not include_deleted:
            clauses.append("d.status!='deleted'")
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"{_DOCUMENT_SELECT} WHERE {' AND '.join(clauses)} "
                "ORDER BY d.created_at DESC LIMIT 1",
                tuple(parameters),
            )
            return _row_dict(cursor.fetchone())

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor, f"{_DOCUMENT_SELECT} WHERE d.id=?", (document_id,)
            )
            return _row_dict(cursor.fetchone())

    def list_documents(
        self,
        base_id: str,
        *,
        folder_id: str | None = None,
        direct_only: bool = False,
        status: str | None = None,
        query: str | None = None,
        publish_date_from: str | None = None,
        publish_date_to: str | None = None,
        report_tags: list[str] | None = None,
        report_second_sources: list[str] | None = None,
        report_title_contains: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where, parameters = self._document_filters(
            base_id,
            folder_id=folder_id,
            direct_only=direct_only,
            status=status,
            query=query,
            publish_date_from=publish_date_from,
            publish_date_to=publish_date_to,
            report_tags=report_tags,
            report_second_sources=report_second_sources,
            report_title_contains=report_title_contains,
        )
        return self._select_documents_where(
            f"{where} ORDER BY d.updated_at DESC LIMIT ? OFFSET ?",
            (*parameters, limit, offset),
        )

    def count_documents_filtered(
        self,
        base_id: str,
        *,
        folder_id: str | None = None,
        direct_only: bool = False,
        status: str | None = None,
        query: str | None = None,
        publish_date_from: str | None = None,
        publish_date_to: str | None = None,
        report_tags: list[str] | None = None,
        report_second_sources: list[str] | None = None,
        report_title_contains: str | None = None,
    ) -> int:
        where, parameters = self._document_filters(
            base_id,
            folder_id=folder_id,
            direct_only=direct_only,
            status=status,
            query=query,
            publish_date_from=publish_date_from,
            publish_date_to=publish_date_to,
            report_tags=report_tags,
            report_second_sources=report_second_sources,
            report_title_contains=report_title_contains,
        )
        return self._count(
            f"SELECT COUNT(*) AS count FROM knowledge_documents d WHERE {where}",
            tuple(parameters),
        )

    def get_documents(self, base_id: str, document_ids: Iterable[str]) -> list[dict[str, Any]]:
        return self._get_documents_by_column(base_id, "d.id", document_ids)

    def get_documents_by_remote_ids(
        self, base_id: str, remote_document_ids: Iterable[str]
    ) -> list[dict[str, Any]]:
        return self._get_documents_by_column(base_id, "d.remote_document_id", remote_document_ids)

    def _get_documents_by_column(
        self, base_id: str, column: str, values: Iterable[str]
    ) -> list[dict[str, Any]]:
        values = list(dict.fromkeys(values))
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        return self._select_documents_where(
            f"d.base_id=? AND {column} IN ({placeholders}) AND d.status!='deleted'",
            (base_id, *values),
        )

    def list_purgeable_documents(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """仍持有远端副本的软删除文档（含所属库已删除的）。

        两类命中：文档自身 status='deleted'；所属库 status∈(deleted, deleting)。
        正常删除路径已同步删除远端副本，本方法供清理 worker 兜底同步失败与
        历史遗留数据，不再按保留期等待。
        """
        sql = """
            SELECT d.id AS document_id, d.base_id, d.remote_document_id,
                   b.remote_dataset_id AS base_remote_dataset_id
            FROM knowledge_documents d
            JOIN knowledge_bases b ON b.id = d.base_id
            WHERE d.remote_document_id IS NOT NULL
              AND d.remote_document_id != ''
              AND (d.status = 'deleted'
                   OR b.status IN ('deleted', 'deleting'))
            ORDER BY d.updated_at
            LIMIT ?
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, sql, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def update_document(self, document_id: str, **fields: object) -> dict[str, Any] | None:
        self._update_by_id("knowledge_documents", document_id, _DOCUMENT_UPDATE_FIELDS, fields)
        return self.get_document(document_id)

    # ---------------- 文档 SQL 同步台账 ----------------

    def create_or_get_sync_item(
        self, *, source: str, source_record_id: str, publish_date: str | None
    ) -> dict[str, Any]:
        """幂等创建同步台账行；已存在时直接返回现有行。"""
        item_id = str(uuid.uuid4())
        timestamp = _now()
        try:
            with self.db.get_connection() as conn:
                cursor = conn.cursor()
                self.db._execute(
                    cursor,
                    """
                    INSERT INTO knowledge_source_sync_items
                        (id, source, source_record_id, document_id, status,
                         attempts, last_error, publish_date, created_at, updated_at)
                    VALUES (?, ?, ?, NULL, 'queued', 0, NULL, ?, ?, ?)
                    """,
                    (item_id, source, source_record_id, publish_date, timestamp, timestamp),
                )
        except Exception:  # noqa: BLE001 — UNIQUE 冲突：并发/重跑时复用已有行
            existing = self.get_sync_item(source, source_record_id)
            if existing is None:
                raise
            return existing
        return self.get_sync_item(source, source_record_id) or {}

    def get_sync_item(self, source: str, source_record_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT * FROM knowledge_source_sync_items "
                "WHERE source=? AND source_record_id=?",
                (source, source_record_id),
            )
            return _row_dict(cursor.fetchone())

    def update_sync_item(self, item_id: str, **fields: object) -> None:
        allowed = {"document_id", "status", "attempts", "last_error", "publish_date"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{column}=?" for column in updates)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"UPDATE knowledge_source_sync_items SET {assignments} WHERE id=?",
                (*updates.values(), item_id),
            )

    def list_sync_items(
        self, *, source: str, statuses: Iterable[str], limit: int = 100
    ) -> list[dict[str, Any]]:
        statuses = list(dict.fromkeys(statuses))
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT * FROM knowledge_source_sync_items "
                f"WHERE source=? AND status IN ({placeholders}) "
                "ORDER BY updated_at LIMIT ?",
                (source, *statuses, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    def count_sync_items(self, *, source: str) -> dict[str, int]:
        """按状态统计同步台账（用于完整性核对：总数 = 各状态之和）。"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT status, COUNT(*) AS count FROM knowledge_source_sync_items "
                "WHERE source=? GROUP BY status",
                (source,),
            )
            return {
                str(row["status"] if isinstance(row, dict) else row[0]): int(
                    row["count"] if isinstance(row, dict) else row[1]
                )
                for row in cursor.fetchall()
            }

    # ---------------- 原始文件对象 ----------------

    def create_original_object(
        self,
        *,
        document_id: str,
        provider: str,
        storage_key: str,
        original_name: str,
        content_type: str,
        size_bytes: int,
        created_by: str,
    ) -> dict[str, Any]:
        object_id = str(uuid.uuid4())
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                INSERT INTO knowledge_document_objects
                    (id, document_id, object_version, provider, storage_key,
                     status, original_name, content_type, size_bytes, sha256,
                     error_code, error_message, created_by, created_at)
                VALUES (?, ?, 1, ?, ?, 'staging', ?, ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    object_id, document_id, provider, storage_key,
                    original_name, content_type, size_bytes, created_by, timestamp,
                ),
            )
        return self.get_original_object(document_id)

    def get_original_object(self, document_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT * FROM knowledge_document_objects
                WHERE document_id=?
                ORDER BY object_version DESC LIMIT 1
                """,
                (document_id,),
            )
            return _row_dict(cursor.fetchone())

    def update_original_object(self, document_id: str, **fields: object) -> dict[str, Any] | None:
        current = self.get_original_object(document_id)
        if current is None:
            return None
        self._update_by_id(
            "knowledge_document_objects", str(current["id"]), _OBJECT_UPDATE_FIELDS, fields
        )
        return self.get_original_object(document_id)

    def create_original_access_audit(
        self,
        *,
        user_id: str,
        session_id: str,
        agent_id: str,
        document_id: str,
        action: str,
        result: str,
        request_id: str = "",
    ) -> dict[str, Any]:
        audit_id = str(uuid.uuid4())
        created_at = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                INSERT INTO knowledge_original_access_audit
                    (id, user_id, session_id, agent_id, document_id,
                     action, result, request_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    audit_id, user_id, session_id, agent_id, document_id,
                    action, result, request_id, created_at,
                ),
            )
        return {
            "id": audit_id,
            "user_id": user_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "document_id": document_id,
            "action": action,
            "result": result,
            "request_id": request_id,
            "created_at": created_at,
        }

    # ---------------- 权限 ----------------

    def list_permissions(self, base_id: str) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT * FROM knowledge_permissions WHERE base_id=? ORDER BY subject_type, subject_id",
                (base_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def list_permissions_for_bases(self, base_ids: Iterable[str]) -> list[dict[str, Any]]:
        """批量查询多个知识库的授权行，避免列表页逐库查询。"""

        ids = list(dict.fromkeys(base_ids))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"SELECT * FROM knowledge_permissions WHERE base_id IN ({placeholders}) "
                "ORDER BY base_id, subject_type, subject_id",
                tuple(ids),
            )
            return [dict(row) for row in cursor.fetchall()]

    def replace_permissions(
        self,
        *,
        base_id: str,
        permissions: Iterable[Mapping[str, str]],
        created_by: str,
    ) -> list[dict[str, Any]]:
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, "DELETE FROM knowledge_permissions WHERE base_id=?", (base_id,))
            for item in permissions:
                self.db._execute(
                    cursor,
                    """
                    INSERT INTO knowledge_permissions
                        (id, base_id, subject_type, subject_id, role,
                         created_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()), base_id, item["subject_type"],
                        item["subject_id"], item["role"], created_by, timestamp, timestamp,
                    ),
                )
        return self.list_permissions(base_id)

    def list_permission_directory_users(
        self, *, exclude_user_id: str = ""
    ) -> list[dict[str, Any]]:
        """列出权限管理可选人员目录（在职账号，含部门/处室/团队层级）。

        exclude_user_id 用于剔除库 owner——owner 天然可见，无需出现在
        勾选名单中。部门标识优先 department_id，缺失时回退 organization_id。
        """

        department_expr = "COALESCE(NULLIF(department_id, ''), NULLIF(organization_id, ''))"
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT user_id,
                       COALESCE(username, '') AS username,
                       COALESCE(NULLIF(display_name, ''), username, '') AS display_name,
                       COALESCE({department_expr}, '') AS department_id,
                       COALESCE(NULLIF(department_name, ''), {department_expr}, '') AS department_name,
                       COALESCE(division_name, '') AS division_name,
                       COALESCE(team_name, '') AS team_name
                FROM users
                WHERE account_status='active' AND username<>'admin'
                  AND (?='' OR user_id<>?)
                ORDER BY department_name, division_name, team_name, display_name
                """,
                (exclude_user_id, exclude_user_id),
            )
            return [dict(row) for row in cursor.fetchall()]

    # ---------------- 操作记录 ----------------

    def create_operation(
        self,
        *,
        operation_type: str,
        resource_type: str,
        resource_id: str,
        phase: str,
        status: str,
        created_by: str,
        retryable: bool = False,
    ) -> dict[str, Any]:
        operation_id = str(uuid.uuid4())
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                INSERT INTO knowledge_operations
                    (id, type, resource_type, resource_id, phase, status,
                     current_count, total_count, progress, retryable, error_code,
                     error_message, created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, 1, NULL, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    operation_id, operation_type, resource_type, resource_id,
                    phase, status, int(retryable), created_by, timestamp, timestamp,
                ),
            )
        return self.get_operation(operation_id)

    def get_operation(self, operation_id: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(cursor, "SELECT * FROM knowledge_operations WHERE id=?", (operation_id,))
            return _row_dict(cursor.fetchone())

    def get_active_document_operation(self, document_id: str) -> dict[str, Any] | None:
        """文档最新一条未完成的 upload/parse 操作。"""
        return self._latest_document_operation(document_id, active_only=True)

    def get_latest_document_operation(self, document_id: str) -> dict[str, Any] | None:
        """文档最新一条操作（含已超时的历史操作）。"""
        return self._latest_document_operation(document_id, active_only=False)

    def _latest_document_operation(self, document_id: str, *, active_only: bool) -> dict[str, Any] | None:
        status_sql = " AND status IN ('accepted', 'running')" if active_only else ""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                f"""
                SELECT * FROM knowledge_operations
                WHERE resource_type='document' AND resource_id=?{status_sql}
                ORDER BY created_at DESC LIMIT 1
                """,
                (document_id,),
            )
            return _row_dict(cursor.fetchone())

    def update_operation(self, operation_id: str, **fields: object) -> dict[str, Any] | None:
        self._update_operation_rows(
            "UPDATE knowledge_operations SET {assignments} WHERE id=?",
            fields,
            (operation_id,),
        )
        return self.get_operation(operation_id)

    def update_active_document_operations(self, document_id: str, **fields: object) -> int:
        """让进行中的 upload/parse 操作与上游文档状态保持一致。"""
        return self._update_operation_rows(
            """
            UPDATE knowledge_operations SET {assignments}
            WHERE resource_type='document' AND resource_id=? AND status IN ('accepted', 'running')
            """,
            fields,
            (document_id,),
        )

    def _update_operation_rows(self, sql_template: str, fields: Mapping[str, object], where: tuple) -> int:
        updates = {key: value for key, value in fields.items() if key in _OPERATION_UPDATE_FIELDS}
        if not updates:
            return 0
        if "retryable" in updates:
            updates["retryable"] = int(bool(updates["retryable"]))
        updates["updated_at"] = _now()
        assignments = ", ".join(f"{column}=?" for column in updates)
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor, sql_template.format(assignments=assignments), (*updates.values(), *where)
            )
            return cursor.rowcount

    def list_operations(
        self, *, created_by: str, limit: int = 50, offset: int = 0
    ) -> list[dict[str, Any]]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT * FROM knowledge_operations WHERE created_by=? "
                "ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (created_by, limit, offset),
            )
            return [dict(row) for row in cursor.fetchall()]

    def count_operations(self, *, created_by: str) -> int:
        return self._count(
            "SELECT COUNT(*) AS count FROM knowledge_operations WHERE created_by=?",
            (created_by,),
        )

    # ---------------- 会话知识范围 ----------------

    def replace_session_scope(
        self, *, session_id: str, user_id: str, base_ids: Iterable[str]
    ) -> list[str]:
        base_ids = list(dict.fromkeys(base_ids))
        timestamp = _now()
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "DELETE FROM session_knowledge_scopes WHERE session_id=? AND user_id=?",
                (session_id, user_id),
            )
            for sort_order, base_id in enumerate(base_ids):
                self.db._execute(
                    cursor,
                    """
                    INSERT INTO session_knowledge_scopes
                        (session_id, base_id, user_id, created_at, sort_order)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, base_id, user_id, timestamp, sort_order),
                )
        return self.get_session_scope(session_id=session_id, user_id=user_id)

    def get_session_scope(self, *, session_id: str, user_id: str) -> list[str]:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT base_id FROM session_knowledge_scopes
                WHERE session_id=? AND user_id=?
                ORDER BY sort_order, created_at, base_id
                """,
                (session_id, user_id),
            )
            return [
                row["base_id"] if isinstance(row, dict) else row[0]
                for row in cursor.fetchall()
            ]

    def has_session_scope(self, *, session_id: str) -> bool:
        """会话是否保存过任何知识选择（fail-closed 开关：一旦存在，
        读取所选知识前必须持有严格 Bearer 身份）。"""
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT 1 FROM session_knowledge_scopes WHERE session_id=? LIMIT 1",
                (session_id,),
            )
            return cursor.fetchone() is not None

    def list_chat_session_rows(
        self, *, user_id: str, base_id: str
    ) -> list[tuple[str, str, str]]:
        """按知识范围关联反查会话，返回 (session_id, username, title)，新创建者优先。

        供面板问答定位「当前轮」会话：同一用户对同一知识库可能存在多轮
        隐藏会话（开启新对话会轮换），created_at 最新者即当前轮。
        """
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                """
                SELECT s.session_id, s.username, s.title
                FROM sessions s
                JOIN session_knowledge_scopes sc ON sc.session_id = s.session_id
                WHERE sc.user_id=? AND sc.base_id=?
                ORDER BY s.created_at DESC
                """,
                (user_id, base_id),
            )
            rows = cursor.fetchall()
        return [
            (row["session_id"], row["username"], row["title"])
            if isinstance(row, dict)
            else (row[0], row[1], row[2])
            for row in rows
        ]


__all__ = ["KnowledgeRepository"]
