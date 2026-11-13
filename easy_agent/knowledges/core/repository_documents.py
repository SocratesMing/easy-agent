"""文档数据访问：增删改查、过滤条件拼装、刷新与清理查询。"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from easy_agent.knowledges.core.models import REFRESHABLE_DOCUMENT_STATUSES
from easy_agent.knowledges.core.repository_base import _now

_DOCUMENT_UPDATE_FIELDS = {
    "remote_document_id", "folder_id", "status", "progress", "error_code",
    "error_message", "deleted_at", "deleted_by",
    # 业务元数据与解析计时
    "source", "source_record_id", "batch_id", "report_title", "report_tag",
    "report_second_source", "publish_date",
    "parse_started_at",
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


class DocumentRepositoryMixin:
    """文档相关查询与写入；由 KnowledgeRepository 组合。"""

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
        return self._fetch_all(f"{_DOCUMENT_SELECT} WHERE {where}", params)

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
        batch_id: str | None = None,
        report_title: str | None = None,
        report_tag: str | None = None,
        report_second_source: str | None = None,
        publish_date: str | None = None,
    ) -> dict[str, Any]:
        document_id = document_id or str(uuid.uuid4())
        timestamp = _now()
        self._execute_write(
            """
            INSERT INTO knowledge_documents
                (id, remote_document_id, base_id, folder_id, name,
                 content_type, size_bytes, status, progress, error_code,
                 error_message, created_by, created_at, updated_at,
                 source, source_record_id, batch_id, report_title, report_tag,
                 report_second_source, publish_date)
            VALUES (?, NULL, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id, base_id, folder_id, name, content_type,
                size_bytes, status, created_by, timestamp, timestamp,
                source, source_record_id, batch_id, report_title, report_tag,
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
        return self._fetch_one(
            f"{_DOCUMENT_SELECT} WHERE {' AND '.join(clauses)} "
            "ORDER BY d.created_at DESC LIMIT 1",
            tuple(parameters),
        )

    def find_duplicate_document(
        self, base_id: str, sha256: str
    ) -> dict[str, Any] | None:
        """同一知识库内按原文 sha256 查找未删除文档（重复上传拦截）。"""

        return self._fetch_one(
            f"{_DOCUMENT_SELECT} "
            "WHERE d.base_id=? AND d.status!='deleted' AND o.sha256=? "
            "ORDER BY d.created_at LIMIT 1",
            (base_id, sha256),
        )

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        return self._fetch_one(
            f"{_DOCUMENT_SELECT} WHERE d.id=?", (document_id,)
        )

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

    def list_documents_in_folders(
        self, base_id: str, folder_ids: Iterable[str]
    ) -> list[dict[str, Any]]:
        """返回指定文件夹子树内未删除的文档（供整棵目录删除时清理远端副本）。"""
        ids = list(dict.fromkeys(str(folder_id) for folder_id in folder_ids))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        return self._select_documents_where(
            f"d.base_id=? AND d.folder_id IN ({placeholders}) AND d.status!='deleted'",
            (base_id, *ids),
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
        document_ids = list(dict.fromkeys(document_ids))
        if not document_ids:
            return []
        placeholders = ",".join("?" for _ in document_ids)
        return self._select_documents_where(
            f"d.base_id=? AND d.id IN ({placeholders}) AND d.status!='deleted'",
            (base_id, *document_ids),
        )

    def list_refreshable_base_ids(self) -> list[str]:
        """仍有待刷新解析状态文档的知识库 ID。

        只统计已经有远端副本且处于 pending/processing 的文档；本地 pending
        但尚未拿到远端 ID（上传中断）不属于可轮询范围。
        """

        placeholders = ", ".join("?" for _ in REFRESHABLE_DOCUMENT_STATUSES)
        rows = self._fetch_all(
            "SELECT DISTINCT base_id FROM knowledge_documents "
            "WHERE remote_document_id IS NOT NULL AND remote_document_id != '' "
            f"AND status IN ({placeholders}) ORDER BY base_id",
            REFRESHABLE_DOCUMENT_STATUSES,
        )
        return [str(row["base_id"]) for row in rows]

    def list_refreshable_documents(
        self, base_id: str, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        """某知识库中需要向上游刷新状态的文档（已有远端副本的 pending/processing）。"""

        placeholders = ", ".join("?" for _ in REFRESHABLE_DOCUMENT_STATUSES)
        return self._select_documents_where(
            "d.base_id=? AND d.remote_document_id IS NOT NULL "
            "AND d.remote_document_id != '' "
            f"AND d.status IN ({placeholders}) "
            "ORDER BY d.updated_at DESC LIMIT ?",
            (base_id, *REFRESHABLE_DOCUMENT_STATUSES, limit),
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
        return self._fetch_all(sql, (limit,))

    def update_document(self, document_id: str, **fields: object) -> dict[str, Any] | None:
        self._update_by_id("knowledge_documents", document_id, _DOCUMENT_UPDATE_FIELDS, fields)
        return self.get_document(document_id)
