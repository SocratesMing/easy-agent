"""对外响应投影：知识库 / 文档 / 操作 / 文件夹摘要与错误、进度归一化。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from easy_agent.knowledges.core.models import (
    DocumentStatus,
    DocumentSummary,
    FolderSummary,
    KnowledgeBaseRole,
    KnowledgeBaseStatus,
    KnowledgeBaseSummary,
    KnowledgeBaseVisibility,
    KnowledgeErrorResponse,
    OperationPhase,
    OperationStatus,
    OperationSummary,
    OperationType,
)
from easy_agent.knowledges.core.permissions import allowed_actions


class SummaryMixin:
    """摘要构造方法；由 KnowledgeService 组合，复用其仓储统计文档数。"""

    async def _base_summary(
        self,
        base: Mapping[str, Any],
        role: KnowledgeBaseRole,
        *,
        document_count: int | None = None,
    ) -> KnowledgeBaseSummary:
        """组装知识库摘要：含文档数与当前角色可用操作。

        document_count 由列表查询批量带出时传入，避免逐库 COUNT 查询。
        """

        count = (
            document_count
            if document_count is not None
            else await self._repo(self.repository.count_documents, str(base["id"]))
        )
        return KnowledgeBaseSummary(
            id=str(base["id"]),
            name=str(base["name"]),
            description=str(base.get("description") or ""),
            visibility=KnowledgeBaseVisibility(str(base["space_type"])),
            status=KnowledgeBaseStatus(str(base["status"])),
            role=role,
            document_count=count,
            allowed_actions=allowed_actions(role),
            created_at=base["created_at"],
            updated_at=base["updated_at"],
        )

    @staticmethod
    def _normalized_progress(value: object) -> float | None:
        """归一化进度字段：仅接受 [0, 1] 区间内的数值，其余视为未知。"""

        return value if isinstance(value, (int, float)) and 0 <= value <= 1 else None

    @staticmethod
    def _error_response(
        record: Mapping[str, Any],
        *,
        default_code: str,
        default_message: str,
        request_id: str,
        retryable: bool,
    ) -> KnowledgeErrorResponse | None:
        """把行内错误字段转换为对外错误模型；无错误时返回 None。"""

        if not (record.get("error_code") or record.get("error_message")):
            return None
        return KnowledgeErrorResponse(
            code=str(record.get("error_code") or default_code),
            message=str(record.get("error_message") or default_message),
            request_id=request_id,
            retryable=retryable,
        )

    @staticmethod
    def _document_summary(
        document: Mapping[str, Any],
        role: KnowledgeBaseRole,
        request_id: str,
    ) -> DocumentSummary:
        """将文档行转换为摘要模型，并归一化错误与进度字段。"""

        error = SummaryMixin._error_response(
            document,
            default_code="KNOWLEDGE_DOCUMENT_ERROR",
            default_message="文档处理失败",
            request_id=request_id,
            retryable=str(document.get("status")) == DocumentStatus.FAILED.value,
        )
        progress = SummaryMixin._normalized_progress(document.get("progress"))
        return DocumentSummary(
            id=str(document["id"]),
            base_id=str(document["base_id"]),
            folder_id=document.get("folder_id"),
            name=str(document["name"]),
            content_type=str(document["content_type"]),
            size_bytes=int(document["size_bytes"]),
            status=DocumentStatus(str(document["status"])),
            original_status=str(document.get("original_status") or "unmanaged"),
            original_available=str(document.get("original_status") or "") == "available",
            original_sha256=(
                str(document["original_sha256"]) if document.get("original_sha256") else None
            ),
            progress=progress,
            error=error,
            allowed_actions=allowed_actions(role),
            created_at=document["created_at"],
            updated_at=document["updated_at"],
            source=document.get("source"),
            source_record_id=document.get("source_record_id"),
            batch_id=document.get("batch_id"),
            report_title=document.get("report_title"),
            report_tag=document.get("report_tag"),
            report_second_source=document.get("report_second_source"),
            publish_date=document.get("publish_date"),
        )

    @staticmethod
    def _operation_summary(
        operation: Mapping[str, Any], request_id: str
    ) -> OperationSummary:
        """将操作记录行转换为摘要模型，并归一化错误与进度字段。"""

        error = SummaryMixin._error_response(
            operation,
            default_code="KNOWLEDGE_OPERATION_ERROR",
            default_message="知识任务失败",
            request_id=request_id,
            retryable=bool(operation.get("retryable")),
        )
        progress = SummaryMixin._normalized_progress(operation.get("progress"))
        return OperationSummary(
            id=str(operation["id"]),
            resource_type=str(operation.get("resource_type") or "") or None,
            resource_id=str(operation.get("resource_id") or "") or None,
            type=OperationType(str(operation["type"])),
            phase=OperationPhase(str(operation["phase"])),
            status=OperationStatus(str(operation["status"])),
            current=int(operation.get("current_count") or 0),
            total=int(operation.get("total_count") or 0),
            progress=progress,
            retryable=bool(operation.get("retryable")),
            error=error,
            created_at=operation["created_at"],
            updated_at=operation["updated_at"],
        )

    @staticmethod
    def _folder_summary(
        folder: Mapping[str, Any], role: KnowledgeBaseRole
    ) -> FolderSummary:
        """将文件夹行转换为摘要模型；计数缺失时按 0 处理。"""

        return FolderSummary(
            id=folder["id"],
            base_id=folder["base_id"],
            parent_id=folder.get("parent_id"),
            name=folder["name"],
            document_count=int(folder.get("document_count") or 0),
            child_count=int(folder.get("child_count") or 0),
            allowed_actions=allowed_actions(role),
            created_at=folder["created_at"],
            updated_at=folder["updated_at"],
        )
