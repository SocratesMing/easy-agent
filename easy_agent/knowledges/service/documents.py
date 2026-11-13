"""文档：查询、移动、下载、删除与解析控制（暂停/恢复/重试）。"""

from __future__ import annotations

import io
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, BinaryIO

from easy_agent.knowledges.core.models import (
    AllowedAction,
    DocumentListResponse,
    DocumentStatus,
    DocumentSummary,
    DocumentUploadAccepted,
    KnowledgeBaseRole,
    OperationPhase,
    OperationStatus,
    OperationType,
    PageInfo,
)
from easy_agent.knowledges.core.original_store import (
    OriginalReadHandle,
    OriginalStorageError,
)
from easy_agent.knowledges.ragflow import RagflowError
from easy_agent.knowledges.service.contracts import (
    KnowledgeDocumentBinary,
    KnowledgeServiceError,
    _not_found,
)

if TYPE_CHECKING:
    from easy_agent.knowledges.core.models import KnowledgePrincipal

logger = logging.getLogger(__name__)


class DocumentMixin:
    """文档方法；由 KnowledgeService 组合，复用其鉴权、原文存储与远端删除能力。"""

    async def _require_document(
        self, document_id: str, *, include_deleted: bool = True
    ) -> dict[str, Any]:
        """读取文档记录；不存在（或已软删除且 include_deleted=False）按 404 处理。"""

        document = await self._repo(self.repository.get_document, document_id)
        if document is None or (
            not include_deleted
            and str(document.get("status")) == DocumentStatus.DELETED.value
        ):
            raise _not_found()
        return document

    async def _authorized_document(
        self,
        document_id: str,
        principal: KnowledgePrincipal,
        action: AllowedAction = AllowedAction.VIEW,
        *,
        include_deleted: bool = True,
    ) -> tuple[dict[str, Any], dict[str, Any], KnowledgeBaseRole]:
        """读取文档并校验其所属知识库的操作权限，返回 (文档, 库记录, 角色)。"""

        document = await self._require_document(
            document_id, include_deleted=include_deleted
        )
        base, role = await self._authorized_base(
            str(document["base_id"]), principal, action
        )
        return document, base, role

    async def list_documents(
        self,
        *,
        base_id: str,
        principal: KnowledgePrincipal,
        page: int,
        page_size: int,
        folder_id: str | None,
        direct_only: bool,
        status: str | None,
        query: str | None,
        request_id: str,
    ) -> DocumentListResponse:
        """分页列出知识库文档。

        只读取本地投影，不等待上游全量刷新：解析状态由后台
        ``KnowledgeParsePollWorker`` 按文档逐个拉取并即时落库。这样同一批
        上传的文档不会因为某个慢任务而卡住整页状态更新。

        关键字搜索与状态筛选始终覆盖整个知识库：传了 ``query`` 或 ``status``
        时忽略 ``folder_id`` / ``direct_only``，避免结果被当前目录层级限制。
        """

        query = (query or "").strip() or None
        if query or status:
            folder_id = None
            direct_only = False
        _, role = await self._authorized_base(base_id, principal)
        rows = await self._repo(
            self.repository.list_documents,
            base_id,
            folder_id=folder_id,
            direct_only=direct_only,
            status=status,
            query=query,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        total = await self._repo(
            self.repository.count_documents_filtered,
            base_id,
            folder_id=folder_id,
            direct_only=direct_only,
            status=status,
            query=query,
        )
        return DocumentListResponse(
            items=[self._document_summary(row, role, request_id) for row in rows],
            page=PageInfo(page=page, page_size=page_size, total=total),
        )

    async def move_document(
        self,
        *,
        document_id: str,
        folder_id: str | None,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> DocumentSummary:
        """移动文档到目标文件夹（目标须属于同一知识库）。"""

        document, _, role = await self._authorized_document(
            document_id, principal, AllowedAction.MOVE_DOCUMENT, include_deleted=False
        )
        if folder_id:
            await self._require_folder(str(document["base_id"]), folder_id)
        document = await self._repo(
            self.repository.update_document, document_id, folder_id=folder_id
        )
        assert document is not None
        return self._document_summary(document, role, request_id)

    async def download_document(
        self,
        *,
        document_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> tuple[dict[str, Any], KnowledgeDocumentBinary]:
        """优先返回本地原文；不可用时回退下载上游副本。"""
        document, base, _ = await self._authorized_document(
            document_id, principal, AllowedAction.DOWNLOAD, include_deleted=False
        )
        original = await self._repo(
            self.repository.get_original_object, document_id
        )
        if original is not None and str(original.get("status")) == "available":
            try:
                handle: OriginalReadHandle = await self._repo(
                    self.original_store.open, str(original["storage_key"])
                )
            except OriginalStorageError as exc:
                # 本地原文记录仍在但磁盘文件丢失/不可读：不再直接 503，
                # 降级为上游（RAGFlow）副本下载，保证预览与下载可用。
                logger.warning(
                    "本地原文不可用，回退上游副本 | document_id=%s code=%s",
                    document_id,
                    exc.code,
                )
            else:
                # 原文访问一律留痕（不受 audit.enabled 控制，与并表前一致）。
                await self._repo(
                    self.operations_repository.record_audit,
                    request_id=request_id,
                    actor_user_id=principal.user_id,
                    actor_username=principal.username,
                    action="knowledge.original.download",
                    object_type="document",
                    object_id=document_id,
                    base_id=str(document.get("base_id") or "") or None,
                    outcome="opened",
                    details={"session_id": "", "agent_id": "api"},
                )
                return document, KnowledgeDocumentBinary(
                    size_bytes=handle.size_bytes,
                    content_type=str(original.get("content_type") or document["content_type"]),
                    sha256=str(original.get("sha256")) if original.get("sha256") else None,
                    iterator_factory=handle.iter_bytes,
                )
        if not document.get("remote_document_id"):
            raise KnowledgeServiceError(
                "KNOWLEDGE_DOCUMENT_NOT_AVAILABLE", "文档原文件暂不可用", status_code=409
            )
        try:
            binary = await self.ragflow.download_document(
                dataset_id=await self._dataset_id_for(base),
                document_id=str(document["remote_document_id"]),
            )
        except RagflowError as exc:
            raise self._upstream_error(exc) from exc
        content_bytes = binary.content
        return document, KnowledgeDocumentBinary(
            size_bytes=len(content_bytes),
            content_type=binary.content_type,
            sha256=None,
            iterator_factory=lambda start=0, end=None: iter(
                [content_bytes[start : (len(content_bytes) if end is None else end + 1)]]
            ),
        )

    async def delete_document(
        self,
        *,
        document_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> None:
        """删除文档：立即同步删除远端（Ragflow）副本，本地保留软删除记录与原文。

        远端删除失败时直接抛错、本地保持原状，用户重试即可；远端已不存在
        按 404 幂等处理。
        """
        del request_id
        document = await self._require_document(document_id)
        if str(document.get("status")) == DocumentStatus.DELETED.value:
            return
        base, _role = await self._authorized_base(
            str(document["base_id"]), principal, AllowedAction.DELETE_DOCUMENT
        )
        remote_id = str(document.get("remote_document_id") or "").strip()
        if remote_id:
            await self._delete_remote_documents(
                await self._dataset_id_for(base), [remote_id]
            )
        await self._repo(
            self.repository.update_document,
            document_id,
            status=DocumentStatus.DELETED.value,
            deleted_at=datetime.now(UTC).isoformat(),
            deleted_by=principal.user_id,
            remote_document_id=None,
            progress=None,
        )

    async def purge_deleted_documents(self, *, limit: int = 100) -> int:
        """兜底清理软删除文档的远端副本，返回本次清理数。

        正常删除路径已同步删除远端副本；本方法由后台清理 worker 周期调用，
        兜底同步删除失败与历史遗留（仍持有远端副本的软删除文档/已删除库）。
        远端删除成功（或远端已不存在，幂等）后清空本地 remote_document_id；
        本地软删除记录与原文保留。单文档失败不阻断其余清理，留待下轮重试。
        """
        rows = await self._repo(
            self.repository.list_purgeable_documents, limit=limit
        )
        if not rows:
            return 0
        purged = 0
        for row in rows:
            dataset_id = str(row.get("base_remote_dataset_id") or "").strip()
            if not dataset_id:
                # 共享数据集模式：路由到 EA_SHARED（含进程级缓存）
                dataset_id = await self._ensure_shared_dataset()
            error = await self._delete_remote_documents_quietly(
                dataset_id, [str(row["remote_document_id"])]
            )
            if error is not None:
                logger.warning(
                    "清理到期文档远端副本失败（下轮重试） | doc=%s: %s",
                    row["document_id"],
                    error,
                )
                continue
            await self._repo(
                self.repository.update_document,
                str(row["document_id"]),
                remote_document_id=None,
            )
            purged += 1
        if purged:
            logger.info("兜底清理软删除文档远端副本完成 | 清理远端文档数=%s", purged)
        return purged

    async def pause_document(
        self,
        *,
        document_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> Mapping[str, Any]:
        """暂停未就绪文档的解析任务：退出解析队列，保留原文可随时恢复。"""
        del request_id
        document, base, _role = await self._authorized_document(
            document_id, principal, AllowedAction.RETRY_DOCUMENT
        )
        if str(document["status"]) not in {
            DocumentStatus.PENDING.value,
            DocumentStatus.PROCESSING.value,
        }:
            raise KnowledgeServiceError(
                "KNOWLEDGE_PAUSE_NOT_ALLOWED",
                "仅未就绪（等待解析/解析中）的文档支持暂停",
                status_code=409,
            )
        remote_id = str(document.get("remote_document_id") or "").strip()
        if not remote_id:
            raise KnowledgeServiceError(
                "KNOWLEDGE_PAUSE_NO_REMOTE",
                "文档尚未同步到解析服务，无法暂停",
                status_code=409,
            )
        dataset_id = await self._dataset_id_for(base)
        try:
            await self.ragflow.stop_parsing(dataset_id, [remote_id])
        except RagflowError as exc:
            raise KnowledgeServiceError(
                "KNOWLEDGE_PAUSE_FAILED", f"暂停解析失败: {exc}", status_code=502
            ) from exc
        return await self._repo(
            self.repository.update_document,
            document_id,
            status=DocumentStatus.PAUSED.value,
            progress=None,
            parse_started_at=None,
        )

    async def resume_document(
        self,
        *,
        document_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> Mapping[str, Any]:
        """恢复已暂停文档：重新提交解析队列，回到待解析状态。"""
        del request_id
        document, base, _role = await self._authorized_document(
            document_id, principal, AllowedAction.RETRY_DOCUMENT
        )
        if str(document["status"]) != DocumentStatus.PAUSED.value:
            raise KnowledgeServiceError(
                "KNOWLEDGE_RESUME_NOT_ALLOWED",
                "仅已暂停的文档支持恢复",
                status_code=409,
            )
        remote_id = str(document.get("remote_document_id") or "").strip()
        if not remote_id:
            raise KnowledgeServiceError(
                "KNOWLEDGE_RESUME_NO_REMOTE",
                "文档尚未同步到解析服务，无法恢复",
                status_code=409,
            )
        dataset_id = await self._dataset_id_for(base)
        try:
            await self.ragflow.parse_documents(dataset_id, [remote_id])
        except RagflowError as exc:
            if not await self._parse_actually_running(base, remote_id):
                raise KnowledgeServiceError(
                    "KNOWLEDGE_RESUME_FAILED",
                    f"恢复解析失败: {exc}",
                    status_code=502,
                ) from exc
        return await self._repo(
            self.repository.update_document,
            document_id,
            status=DocumentStatus.PENDING.value,
            progress=None,
            parse_started_at=None,
            error_code=None,
            error_message=None,
        )

    async def retry_document(
        self,
        *,
        document_id: str,
        principal: KnowledgePrincipal,
        request_id: str,
    ) -> DocumentUploadAccepted:
        """重试失败文档：删除上游旧副本后，用本地原文重新上传并解析。"""
        document, base, role = await self._authorized_document(
            document_id, principal, AllowedAction.RETRY_DOCUMENT
        )
        if str(document["status"]) not in {
            DocumentStatus.FAILED.value,
            DocumentStatus.CANCELLED.value,
            DocumentStatus.UNKNOWN.value,
        }:
            raise KnowledgeServiceError(
                "KNOWLEDGE_RETRY_NOT_ALLOWED",
                "当前文档状态不允许重试",
                status_code=409,
            )
        remote_id = document.get("remote_document_id")
        original_record = await self._repo(
            self.repository.get_original_object, document_id
        )
        has_local_original = (
            original_record is not None
            and str(original_record.get("status")) == "available"
        )
        if not remote_id and not has_local_original:
            raise KnowledgeServiceError(
                "KNOWLEDGE_DOCUMENT_NOT_AVAILABLE", "文档原文件不可用", status_code=409
            )
        operation = await self._repo(
            self.repository.create_operation,
            operation_type=OperationType.DOCUMENT_PARSE.value,
            resource_type="document",
            resource_id=document_id,
            phase=OperationPhase.UPLOADING.value,
            status=OperationStatus.RUNNING.value,
            created_by=principal.user_id,
            retryable=True,
        )
        failure_message = "文档重试失败"
        original_error_message = "文档原文件不可用"
        source: BinaryIO | None = None
        try:
            dataset_id = await self._dataset_id_for(base)
            if has_local_original:
                handle = await self._repo(
                    self.original_store.open, str(original_record["storage_key"])
                )
                source = handle.open_binary()
            else:
                downloaded = await self.ragflow.download_document(
                    dataset_id=dataset_id,
                    document_id=str(remote_id),
                )
                source = io.BytesIO(downloaded.content)
            # 注意：不在这里删上游旧副本，也不清 remote_document_id。
            # 必须等 _submit_to_ragflow 成功后再做，否则一旦中途抛异常，
            # 旧副本已被删除、remote_id 也丢了，下次重试既没有本地原文
            # 又无法从 RAGFlow 下载，只能报"文档原文件不可用" (bug 根因)。
        except (RagflowError, KeyError, OriginalStorageError) as exc:
            await self._fail_submit(
                document_id=document_id,
                operation_id=str(operation["id"]),
                exc=exc,
                failure_message=failure_message,
                original_error_message=original_error_message,
            )
        try:
            document, operation = await self._submit_to_ragflow(
                base=base,
                document=document,
                dataset_id=dataset_id,
                source=source,
                operation_id=str(operation["id"]),
                failure_message=failure_message,
                original_error_message=original_error_message,
            )
        finally:
            if source is not None:
                source.close()
        assert document is not None and operation is not None
        # 新文档已提交成功，再尽力清理旧副本。删除失败不影响新文档，
        # 只记 warning；后台 purge_deleted_documents 兜底。
        if remote_id and str(document.get("remote_document_id") or "") != str(remote_id):
            error = await self._delete_remote_documents_quietly(
                dataset_id, [str(remote_id)]
            )
            if error is not None:
                logger.warning(
                    "重试后清理 RAGFlow 旧副本失败（不影响新文档） | doc=%s "
                    "dataset=%s old_remote=%s code=%s",
                    document_id,
                    dataset_id,
                    remote_id,
                    getattr(error, "code", type(error).__name__),
                )
        return DocumentUploadAccepted(
            document=self._document_summary(document, role, request_id),
            operation=self._operation_summary(operation, request_id),
        )
