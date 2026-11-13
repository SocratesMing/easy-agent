"""文档解析状态：上游刷新、状态投影写入、超时判定与 chunk 关键词回填。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from easy_agent.knowledges.core.chunks import chunk_id
from easy_agent.knowledges.core.config import UPSTREAM_DOCUMENT_STATUS_MAPPING
from easy_agent.knowledges.core.models import (
    DocumentStatus,
    OperationPhase,
    OperationStatus,
)
from easy_agent.knowledges.ragflow import RagflowError
from easy_agent.knowledges.service.contracts import KnowledgeServiceError

logger = logging.getLogger(__name__)

# 共享库解析状态刷新：并发拉取多篇文档，单篇慢请求只拖住自己，不阻塞同批其它文档。
_REFRESH_DOCUMENT_CONCURRENCY = 5
# 本地解析超时：绝对超时即失败终态；重试/恢复会清空计时重新开始。
_PARSE_TIMEOUT_ERROR_CODE = "KNOWLEDGE_PARSE_TIMEOUT"


def _map_upstream_status(run: object) -> str:
    return UPSTREAM_DOCUMENT_STATUS_MAPPING.get(str(run or "")) or DocumentStatus.UNKNOWN.value


class DocumentStatusMixin:
    """解析状态方法；由 KnowledgeService 组合，复用其仓储与上游客户端。"""

    async def _parse_actually_running(self, base: dict, remote_id: str) -> bool:
        """核查远端文档解析是否实际已启动。

        Ragflow 触发解析存在竞态误报：任务已入队（文档 run=RUNNING）但响应
        返回 code=100 IndexError。此时应按成功处理，避免误标失败、留下孤儿文档。
        """
        try:
            data = await self.ragflow.list_documents(
                await self._dataset_id_for(base), document_id=remote_id
            )
        except RagflowError:
            return False
        return upstream_document_running(data, remote_id)

    @staticmethod
    def _match_upstream_document(
        documents: list[Any], remote_id: str
    ) -> Mapping[str, Any] | None:
        """从上游返回的文档列表中按 ID 精确取出本文档。

        不同 RAGFlow 版本的文档过滤参数名不一致；即使上游忽略过滤返回整页，
        也必须按 ID 匹配，避免同批文档都显示成列表首位（进度最慢那篇）的状态。
        """

        for document in documents:
            if (
                isinstance(document, Mapping)
                and str(document.get("id") or "") == remote_id
            ):
                return document
        return None

    async def refresh_base_documents(self, base: Mapping[str, Any]) -> None:
        """用上游文档状态刷新本地投影；仅后台解析轮询 worker 调用。

        共享数据集（remote_dataset_id 为空）需先解析共享数据集 ID，存量自有
        数据集直接用自己的 remote_dataset_id；随后逐篇查询本库
        pending/processing 文档。终态文档不再轮询，稳态零上游调用，也不受
        上游列表分页（page_size 上限 100）影响。
        """

        remote_dataset_id = str(base.get("remote_dataset_id") or "").strip()
        if remote_dataset_id:
            await self._refresh_documents_by_id(base, remote_dataset_id)
            return
        try:
            dataset_id = await self._dataset_id_for(base)
        except RagflowError as exc:
            logger.warning(
                "共享数据集解析失败 base=%s: %s", base.get("id"), exc
            )
            return
        await self._refresh_documents_by_id(base, dataset_id)

    async def _refresh_documents_by_id(
        self, base: Mapping[str, Any], dataset_id: str
    ) -> None:
        """逐篇轮询本库 pending/processing 文档的上游状态。"""

        active_docs = await self._repo(
            self.repository.list_refreshable_documents,
            str(base["id"]),
            limit=self.config.limits.max_documents_per_query,
        )
        semaphore = asyncio.Semaphore(_REFRESH_DOCUMENT_CONCURRENCY)

        async def refresh_one(local: Mapping[str, Any]) -> None:
            remote_id = str(local.get("remote_document_id") or "").strip()
            if not remote_id:
                return
            async with semaphore:
                try:
                    upstream = await self.ragflow.list_documents(
                        dataset_id, document_id=remote_id
                    )
                    docs = upstream.get("docs") if isinstance(upstream, dict) else None
                    if not isinstance(docs, list) or not docs:
                        return
                    upstream_doc = self._match_upstream_document(docs, remote_id)
                    if upstream_doc is not None:
                        await self._apply_upstream_doc_status(local, upstream_doc)
                except RagflowError as exc:
                    logger.warning(
                        "刷新文档状态失败 base=%s doc=%s: %s",
                        base.get("id"), local.get("id"), exc,
                    )
                except Exception as exc:  # noqa: BLE001 - 单篇失败不能拖累同批其它文档
                    logger.warning(
                        "刷新文档状态异常 base=%s doc=%s: %s",
                        base.get("id"), local.get("id"), exc,
                    )

        await asyncio.gather(*(refresh_one(local) for local in active_docs))

    async def _apply_upstream_doc_status(
        self, local: Mapping[str, Any], upstream_doc: Mapping[str, Any]
    ) -> None:
        """按上游文档状态更新本地投影（状态/进度/超时判定/操作记录）。"""
        # 本地已暂停的文档不受上游覆盖：stop 后上游 run 回落 UNSTART，
        # 不拦截会被刷新循环拉回 pending，暂停态就丢了
        if str(local.get("status")) == DocumentStatus.PAUSED.value:
            return
        status = DocumentStatus(_map_upstream_status(upstream_doc.get("run")))
        progress = self._normalized_progress(upstream_doc.get("progress"))
        failure_message = str(upstream_doc.get("progress_msg") or "文档解析失败")
        now_iso = datetime.now(UTC).isoformat()
        parse_started_at = local.get("parse_started_at")
        if status == DocumentStatus.PROCESSING and not parse_started_at:
            # 首次观察到上游真正开始解析：超时从此处计时，不含排队时间
            parse_started_at = now_iso
        parse_started_changed = bool(
            parse_started_at and parse_started_at != local.get("parse_started_at")
        )
        # 超时判定先于“无变化跳过”：进度停滞的文档也必须能被发现
        if await self._fail_parse_timeout(local, status, parse_started_at):
            return
        changed = (
            str(local.get("status")) != status.value
            or local.get("progress") != progress
            or parse_started_changed
        )
        # 状态、进度与解析计时均无变化：跳过后续 DB 写入（稳态轮询省往返）
        if not changed:
            return
        await self._repo(
            self.repository.update_document,
            local["id"],
            **self._document_status_values(
                status, progress, parse_started_at, parse_started_changed, failure_message
            ),
        )
        await self._repo(
            self.repository.update_active_document_operations,
            str(local["id"]),
            **self._operation_status_values(status, progress, failure_message),
        )
        if (
            status == DocumentStatus.READY
            and str(local.get("status")) != DocumentStatus.READY.value
        ):
            # 仅在新文档首次解析完成时写入 chunk 关键词；历史已 READY 文档
            # 不会再次触发，批量回填另见 backfill_base_meta 脚本。
            await self._apply_chunk_keywords(local)

    async def _fail_parse_timeout(
        self,
        local: Mapping[str, Any],
        status: DocumentStatus,
        parse_started_at: object,
    ) -> bool:
        """解析超时则写失败终态并返回 True（先于“无变化跳过”判定）。"""

        timeout_seconds = float(self.config.limits.parse_timeout_seconds or 0)
        is_sync_source = str(local.get("source") or "") == "source_sync"
        if not (
            status == DocumentStatus.PROCESSING
            and not is_sync_source
            and timeout_seconds > 0
            and parse_started_at
        ):
            return False
        try:
            started = datetime.fromisoformat(str(parse_started_at))
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            elapsed = (datetime.now(UTC) - started).total_seconds()
        except (TypeError, ValueError):
            elapsed = 0.0
        if elapsed <= timeout_seconds:
            return False
        timeout_message = (
            f"解析超过 {int(timeout_seconds)} 秒仍未完成，请拆分文件后重试"
        )
        await self._repo(
            self.repository.update_document,
            str(local["id"]),
            status=DocumentStatus.FAILED.value,
            progress=None,
            error_code=_PARSE_TIMEOUT_ERROR_CODE,
            error_message=timeout_message,
        )
        await self._repo(
            self.repository.update_active_document_operations,
            str(local["id"]),
            phase=OperationPhase.FAILED.value,
            status=OperationStatus.FAILED.value,
            progress=None,
            retryable=True,
            error_code=_PARSE_TIMEOUT_ERROR_CODE,
            error_message=timeout_message,
        )
        return True

    @staticmethod
    def _document_status_values(
        status: DocumentStatus,
        progress: object,
        parse_started_at: object,
        parse_started_changed: bool,
        failure_message: str,
    ) -> dict[str, object]:
        """文档投影字段：状态/进度/计时 + 各终态的错误码与错误信息。"""

        values: dict[str, object] = {"status": status.value, "progress": progress}
        if parse_started_changed:
            values["parse_started_at"] = parse_started_at
        if status == DocumentStatus.FAILED:
            values.update(
                {
                    "progress": None,
                    "error_code": "KNOWLEDGE_PARSE_FAILED",
                    "error_message": failure_message,
                }
            )
        elif status == DocumentStatus.READY:
            values.update(
                {"progress": 1.0, "error_code": None, "error_message": None}
            )
        elif status == DocumentStatus.CANCELLED:
            values.update(
                {"progress": None, "error_code": None, "error_message": None}
            )
        elif status in {DocumentStatus.PENDING, DocumentStatus.PROCESSING}:
            values.update({"error_code": None, "error_message": None})
        return values

    @staticmethod
    def _operation_status_values(
        status: DocumentStatus, progress: object, failure_message: str
    ) -> dict[str, object]:
        """操作投影字段：与文档终态保持一致的操作阶段与重试标记。"""

        values: dict[str, object] = {"progress": progress}
        if status == DocumentStatus.READY:
            values.update(
                {
                    "phase": OperationPhase.COMPLETED.value,
                    "status": OperationStatus.SUCCEEDED.value,
                    "current_count": 1,
                    "progress": 1.0,
                    "retryable": False,
                    "error_code": None,
                    "error_message": None,
                }
            )
        elif status == DocumentStatus.FAILED:
            values.update(
                {
                    "phase": OperationPhase.FAILED.value,
                    "status": OperationStatus.FAILED.value,
                    "progress": None,
                    "retryable": True,
                    "error_code": "KNOWLEDGE_PARSE_FAILED",
                    "error_message": failure_message,
                }
            )
        elif status == DocumentStatus.CANCELLED:
            values.update(
                {
                    "phase": OperationPhase.COMPLETED.value,
                    "status": OperationStatus.CANCELLED.value,
                    "progress": None,
                    "retryable": True,
                    "error_code": None,
                    "error_message": None,
                }
            )
        return values

    async def _apply_chunk_keywords(self, document: Mapping[str, Any]) -> None:
        """把业务元数据写入该文档各 chunk 的 important_keywords。

        标题/标签进入关键词通道后，检索对专有名词、机构名、报告名的命中率
        更高。失败只记日志，不影响文档解析状态。
        """

        keywords: list[str] = []
        for key in ("report_title", "report_tag", "report_second_source"):
            value = str(document.get(key) or "").strip()
            if value and value not in keywords:
                keywords.append(value[:127])
        keywords = keywords[:8]
        remote_id = str(document.get("remote_document_id") or "").strip()
        if not keywords or not remote_id:
            return
        base = await self._repo(self.repository.get_base, str(document["base_id"]))
        if base is None:
            return
        try:
            dataset_id = await self._dataset_id_for(base)
        except (RagflowError, KnowledgeServiceError) as exc:
            logger.warning(
                "写入 chunk 关键词前解析数据集失败 doc=%s: %s", remote_id, exc
            )
            return
        semaphore = asyncio.Semaphore(_REFRESH_DOCUMENT_CONCURRENCY)

        async def _update(chunk: Mapping[str, Any]) -> None:
            chunk_key = chunk_id(chunk)
            if not chunk_key:
                return
            async with semaphore:
                try:
                    await self.ragflow.update_chunk(
                        dataset_id,
                        remote_id,
                        chunk_key,
                        important_keywords=keywords,
                    )
                except RagflowError as exc:
                    logger.warning(
                        "写入 chunk 关键词失败 doc=%s chunk=%s: %s",
                        remote_id,
                        chunk_key,
                        exc,
                    )

        try:
            page = 1
            while page <= 10:
                payload = await self.ragflow.list_chunks(
                    dataset_id, remote_id, page=page, page_size=100
                )
                batch = payload.get("chunks") if isinstance(payload, dict) else None
                if not isinstance(batch, list) or not batch:
                    return
                await asyncio.gather(
                    *(_update(chunk) for chunk in batch if isinstance(chunk, dict))
                )
                total = payload.get("total")
                if isinstance(total, int) and page * 100 >= total:
                    return
                if len(batch) < 100:
                    return
                page += 1
        except RagflowError as exc:
            logger.warning("写入 chunk 关键词失败 doc=%s: %s", remote_id, exc)


def upstream_document_running(payload: Any, remote_id: str) -> bool:
    """上游文档响应中，指定文档的解析是否已实际启动（RUNNING/DONE）。

    ``payload`` 兼容 ``{"docs": [...]}`` 与直接列表；优先按 ID 精确匹配，
    上游忽略过滤只返回一条时退化为该条。供解析触发竞态兜底与迁移脚本共用。
    """

    docs = payload.get("docs") if isinstance(payload, dict) else payload
    if not isinstance(docs, list) or not docs:
        return False
    matched = DocumentStatusMixin._match_upstream_document(docs, remote_id)
    if matched is None and len(docs) == 1:
        matched = docs[0]
    if not isinstance(matched, Mapping):
        return False
    return str(matched.get("run", "")).upper() in {"RUNNING", "DONE"}
