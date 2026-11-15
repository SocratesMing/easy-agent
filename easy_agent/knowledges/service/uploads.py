"""文档上传管线：校验、原文落盘、提交上游解析与失败回滚。"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

from easy_agent.knowledges.core.config import UploadSecurityConfig
from easy_agent.knowledges.core.file_validation import (
    UploadValidationError,
    canonical_content_type,
    validate_upload,
)
from easy_agent.knowledges.core.models import (
    AllowedAction,
    DocumentMetadata,
    DocumentStatus,
    DocumentUploadAccepted,
    OperationPhase,
    OperationStatus,
    OperationType,
)
from easy_agent.knowledges.core.original_store import OriginalStorageError
from easy_agent.knowledges.ragflow import RagflowError
from easy_agent.knowledges.service.contracts import KnowledgeServiceError
from easy_agent.knowledges.service.folders import _split_folder_segments

if TYPE_CHECKING:
    from easy_agent.knowledges.core.models import KnowledgePrincipal

logger = logging.getLogger(__name__)


# RAGFlow 暂不支持 PowerPoint 解析；即使管理员误把扩展名加回白名单也给出可操作提示
UNSUPPORTED_PRESENTATION_EXTENSIONS = frozenset({"ppt", "pptx"})
UNSUPPORTED_PRESENTATION_MESSAGE = (
    "暂不支持 PPT/PPTX 格式上传，请转换为 PDF 或 DOCX 后再上传"
)

# 数据集创建与解析策略固定参数（扁平配置不承载，代码内固定）
DATASET_PERMISSION = "me"
CHUNK_METHOD = "naive"
PARSER_CONFIG: dict[str, object] = {
    "chunk_token_num": 512,
    "layout_recognize": "DeepDOC",
    "html4excel": False,
    "delimiter": "\n!?;。；！？",
    "raptor": {"use_raptor": False},
}

_UPLOAD_SECURITY = UploadSecurityConfig()

class UploadMixin:
    """上传管线方法；由 KnowledgeService 组合，复用其仓储、原文存储与上游客户端。"""


    async def _submit_to_ragflow(
        self,
        *,
        base: Mapping[str, Any],
        document: Mapping[str, Any],
        dataset_id: str,
        source: BinaryIO,
        operation_id: str,
        failure_message: str,
        original_error_message: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """上传远端 → 配置解析器 → 触发解析，返回 pending 投影行。

        本方法只把文档推进到 ``pending``（已提交上游、等待执行）；真正的
        ``processing`` 由解析轮询在观察到上游 RUNNING 后写入。上传与重试共用
        该流程；失败时统一标记文档/操作为失败并抛出映射后的业务错误。
        调用方负责关闭 source。
        """

        try:
            remote_documents = await self.ragflow.upload_documents(
                dataset_id,
                [(str(document["name"]), source, str(document["content_type"]))],
            )
            if not remote_documents or not remote_documents[0].get("id"):
                raise KeyError("id")
            remote_id = str(remote_documents[0]["id"])
            document = await self._repo(
                self.repository.update_document,
                str(document["id"]),
                remote_document_id=remote_id,
                status=DocumentStatus.PENDING.value,
                progress=None,
                error_code=None,
                error_message=None,
                parse_started_at=None,
            )
            await self._repo(
                self.repository.update_operation,
                operation_id,
                phase=OperationPhase.CONFIGURING.value,
            )
            # 个人库打 owner_user_id 标签；业务文档同时写 metadata 副本
            await self.ragflow.update_document(
                dataset_id,
                remote_id,
                chunk_method=CHUNK_METHOD,
                parser_config=PARSER_CONFIG,
                meta_fields=self._document_meta_fields(base, document),
            )
            try:
                await self.ragflow.parse_documents(dataset_id, [remote_id])
            except RagflowError as exc:
                if not await self._parse_actually_running(base, remote_id):
                    raise
                logger.warning(
                    "RAGFlow 触发解析误报(%s)，文档实际已开始解析，按成功处理",
                    exc,
                )
            operation = await self._repo(
                self.repository.update_operation,
                operation_id,
                phase=OperationPhase.PARSING.value,
            )
        except (RagflowError, KeyError, OriginalStorageError) as exc:
            await self._fail_submit(
                document_id=str(document["id"]),
                operation_id=operation_id,
                exc=exc,
                failure_message=failure_message,
                original_error_message=original_error_message,
            )
        return document, operation

    async def _fail_submit(
        self,
        *,
        document_id: str,
        operation_id: str,
        exc: BaseException,
        failure_message: str,
        original_error_message: str,
    ) -> None:
        """标记文档与操作为失败，并抛出映射后的业务错误（始终抛出）。"""

        code = getattr(exc, "code", "RAGFLOW_CONTRACT_MISMATCH")
        retryable = bool(getattr(exc, "retryable", False))
        await self._repo(
            self.repository.update_document,
            document_id,
            status=DocumentStatus.FAILED.value,
            progress=None,
            parse_started_at=None,
            error_code=code,
            error_message=failure_message,
        )
        await self._repo(
            self.repository.update_operation,
            operation_id,
            phase=OperationPhase.FAILED.value,
            status=OperationStatus.FAILED.value,
            progress=None,
            retryable=retryable,
            error_code=code,
            error_message=failure_message,
        )
        if isinstance(exc, RagflowError):
            raise self._upstream_error(exc) from exc
        if isinstance(exc, OriginalStorageError):
            raise KnowledgeServiceError(
                code, original_error_message, status_code=503, retryable=retryable
            ) from exc
        raise KnowledgeServiceError(
            "KNOWLEDGE_UPSTREAM_ERROR",
            "知识底座返回了无效文档数据",
            status_code=502,
        )

    @staticmethod
    def _sha256_digest(source: BinaryIO) -> str:
        """流式计算原文 sha256，并把指针复位到开头。"""

        source.seek(0)
        digest = hashlib.sha256()
        while True:
            block = source.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
        source.seek(0)
        return digest.hexdigest()

    async def upload_document(
        self,
        *,
        base_id: str,
        folder_id: str | None,
        filename: str,
        content_type: str,
        size_bytes: int,
        content: BinaryIO,
        principal: KnowledgePrincipal,
        request_id: str,
        relative_path: str | None = None,
        metadata: DocumentMetadata | None = None,
    ) -> DocumentUploadAccepted:
        """上传文档：校验 → 本地落盘原文 → 上传 RAGFlow → 配置解析器 → 触发解析。

        relative_path 为浏览器文件夹上传的原始相对路径（webkitRelativePath），
        非空时按其目录段逐级 find-or-create 文件夹后落入叶子目录。
        metadata 为文档 SQL 同步附带的业务元数据（人工上传时为 None）。
        """
        base, role = await self._authorized_base(
            base_id, principal, AllowedAction.UPLOAD
        )
        folder_id = await self._resolve_upload_folder(base_id, folder_id, relative_path)
        safe_name = await self._validate_upload_request(base_id, filename, size_bytes)
        content_type = await self._validate_upload_content(
            safe_name, content_type, size_bytes, content
        )
        # 同库重复内容拦截（仅人工上传；文档 SQL 同步有自己的业务幂等键）
        if metadata is None:
            await self._reject_duplicate_content(base_id, content)

        document = await self._repo(
            self.repository.create_document,
            base_id=base_id,
            folder_id=folder_id,
            name=safe_name,
            content_type=content_type,
            size_bytes=size_bytes,
            created_by=principal.user_id,
            status=DocumentStatus.PENDING.value,
            source=metadata.source if metadata else None,
            source_record_id=metadata.source_record_id if metadata else None,
            batch_id=metadata.batch_id if metadata else None,
            report_title=metadata.report_title if metadata else None,
            report_tag=metadata.report_tag if metadata else None,
            report_second_source=metadata.report_second_source if metadata else None,
            publish_date=metadata.publish_date if metadata else None,
        )
        operation = await self._repo(
            self.repository.create_operation,
            operation_type=OperationType.DOCUMENT_UPLOAD.value,
            resource_type="document",
            resource_id=document["id"],
            phase=OperationPhase.STORING_ORIGINAL.value,
            status=OperationStatus.RUNNING.value,
            created_by=principal.user_id,
            retryable=True,
        )
        storage_key = self.original_store.build_key(
            base_id, str(document["id"]), safe_name
        )
        await self._repo(
            self.repository.create_original_object,
            document_id=str(document["id"]),
            provider=self.original_store.provider,
            storage_key=storage_key,
            original_name=safe_name,
            content_type=content_type,
            size_bytes=size_bytes,
            created_by=principal.user_id,
        )
        # ---- 本地落盘原文 ----
        try:
            stored_size, stored_sha = await self._repo(
                self.original_store.put_atomic, storage_key, content, size_bytes
            )
            await self._repo(
                self.repository.update_original_object,
                str(document["id"]),
                status="available",
                size_bytes=stored_size,
                sha256=stored_sha,
                stored_at=datetime.now(UTC).isoformat(),
                error_code=None,
                error_message=None,
            )
            await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.UPLOADING.value,
            )
        except OriginalStorageError as exc:
            await self._repo(
                self.repository.update_original_object,
                str(document["id"]),
                status="failed",
                error_code=exc.code,
                error_message=exc.message,
            )
            document = await self._repo(
                self.repository.update_document,
                str(document["id"]),
                status=DocumentStatus.FAILED.value,
                progress=None,
                parse_started_at=None,
                error_code=exc.code,
                error_message="原文存储失败，未提交知识底座",
            )
            await self._repo(
                self.repository.update_operation,
                operation["id"],
                phase=OperationPhase.FAILED.value,
                status=OperationStatus.FAILED.value,
                progress=None,
                retryable=exc.retryable,
                error_code=exc.code,
                error_message="原文存储失败，未提交知识底座",
            )
            raise KnowledgeServiceError(
                exc.code,
                "原文存储暂时不可用",
                status_code=503 if exc.retryable else 422,
                retryable=exc.retryable,
            ) from exc
        # ---- 上传 RAGFlow 并触发解析 ----
        source = self.original_store.open(storage_key)
        binary = source.open_binary()
        try:
            document, operation = await self._submit_to_ragflow(
                base=base,
                document=document,
                dataset_id=await self._dataset_id_for(base),
                source=binary,
                operation_id=str(operation["id"]),
                failure_message="文档上传或解析启动失败",
                original_error_message="原文读取失败",
            )
        finally:
            binary.close()
        assert document is not None and operation is not None
        return DocumentUploadAccepted(
            document=self._document_summary(document, role, request_id),
            operation=self._operation_summary(operation, request_id),
        )

    async def _resolve_upload_folder(
        self, base_id: str, folder_id: str | None, relative_path: str | None
    ) -> str | None:
        """校验目标文件夹；文件夹上传时按相对路径逐级 find-or-create。"""

        if folder_id:
            await self._require_folder(base_id, folder_id)
        if relative_path:
            return await self._ensure_folder_dirs(
                base_id, folder_id, _split_folder_segments(relative_path)[:-1]
            )
        return folder_id

    async def _validate_upload_request(
        self, base_id: str, filename: str, size_bytes: int
    ) -> str:
        """文档数量/大小/文件名/后缀校验，返回安全文件名。

        校验顺序即错误码优先级：文档上限 → 文件大小 → 文件名 → 后缀。
        """

        max_per_base = self.config.limits.max_documents_per_base
        if (
            max_per_base > 0
            and await self._repo(self.repository.count_documents, base_id)
            >= max_per_base
        ):
            raise KnowledgeServiceError(
                "KNOWLEDGE_DOCUMENT_LIMIT",
                f"知识库文档数量已达上限（{max_per_base}）",
                status_code=409,
            )
        if size_bytes > self.config.limits.max_file_size_mb * 1024 * 1024:
            raise KnowledgeServiceError(
                "KNOWLEDGE_FILE_TOO_LARGE", "文件超过大小限制", status_code=413
            )
        safe_name = Path(filename).name
        if (
            not safe_name
            or len(safe_name.encode("utf-8")) > self.config.limits.max_filename_utf8_bytes
        ):
            raise KnowledgeServiceError(
                "KNOWLEDGE_INVALID_FILENAME", "文件名无效或过长", status_code=422
            )
        extension = safe_name.rsplit(".", 1)[-1].lower() if "." in safe_name else ""
        if extension in UNSUPPORTED_PRESENTATION_EXTENSIONS:
            raise KnowledgeServiceError(
                "KNOWLEDGE_FILE_TYPE_NOT_ALLOWED",
                UNSUPPORTED_PRESENTATION_MESSAGE,
                status_code=422,
            )
        if extension not in {
            value.lower() for value in self.config.limits.allowed_extensions
        }:
            raise KnowledgeServiceError(
                "KNOWLEDGE_FILE_TYPE_NOT_ALLOWED",
                f"不支持该文件类型（后缀 .{extension or '无'}）",
                status_code=422,
            )
        return safe_name

    async def _validate_upload_content(
        self,
        safe_name: str,
        content_type: str,
        size_bytes: int,
        content: BinaryIO,
    ) -> str:
        """运行上传安全校验，并返回按扩展名归一后的内容类型。"""

        try:
            await self._repo(
                validate_upload,
                content,
                filename=safe_name,
                content_type=content_type or "application/octet-stream",
                size_bytes=size_bytes,
                config=_UPLOAD_SECURITY,
            )
        except UploadValidationError as exc:
            raise KnowledgeServiceError(
                exc.code,
                exc.message,
                status_code=503 if exc.retryable else 422,
                retryable=exc.retryable,
            ) from exc
        # 浏览器可能按宿主注册表报出旧版/新版 Office MIME；落库与上游统一用
        # 扩展名对应的标准 MIME，避免 RAGFlow 按声明类型拒收。
        return canonical_content_type(safe_name, content_type)

    async def _reject_duplicate_content(
        self, base_id: str, content: BinaryIO
    ) -> None:
        """同库重复内容拒绝：已存在相同 sha256 的未删除文档时直接 409。

        哈希放线程池，避免大文件阻塞事件循环。
        """

        digest = await asyncio.to_thread(self._sha256_digest, content)
        duplicate = await self._repo(
            self.repository.find_duplicate_document, base_id, digest
        )
        if duplicate is not None:
            raise KnowledgeServiceError(
                "KNOWLEDGE_DUPLICATE_DOCUMENT",
                f"知识库中已存在相同内容的文档：{duplicate['name']}",
                status_code=409,
            )
