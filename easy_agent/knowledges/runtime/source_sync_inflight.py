"""研报同步的在途回收：台账状态回收、单条重试与并发窗口限流。"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from easy_agent.knowledges.core.models import DocumentStatus
from easy_agent.knowledges.runtime.source_sync_support import (
    INFLIGHT_POLL_INTERVAL_SECONDS,
    MAX_UPLOAD_ATTEMPTS,
    _RETRYABLE_DOCUMENT_STATUSES,
)

logger = logging.getLogger(__name__)


class SourceSyncInflightMixin:
    """在途回收方法；由 SourceSyncEngine 组合，复用其仓储、服务与配置。"""

    # ---------------- 在途回收 ----------------

    async def refresh_inflight(
        self, items: list[dict[str, Any]], *, base_id: str | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """检查一批“在途”台账行对应的文档状态，做一次状态回收。

        “在途”指台账状态停在 uploading / parsing 的行：可能正在上传、
        正在解析，也可能因为进程重启而“卡住”。本函数逐条查看文档的真实状态，
        再决定下一步：

        ==============================  ==========================================
        文档的真实状态                   本函数做的事
        ==============================  ==========================================
        文档不存在（还没落库）            台账置 failed，下一轮重新上传
        文档 DELETED                     台账置 deleted，尊重人工删除，永不重传
        文档 READY                       台账置 ready，同步完成
        文档 PENDING 且没有远端 ID        上次上传半途中断，台账置 failed 等重试
        文档 FAILED/CANCELLED/UNKNOWN     未超过固定重试上限就重试，否则置 failed
        其他（解析中/暂停等）              原样留在 remaining，下一轮继续等
        ==============================  ==========================================

        参数：
            items:   待检查的台账行列表
            base_id: 目标知识库 ID（用于判断文档是否属于当前库）
        返回：
            (remaining, stats)
            - remaining：还没结束、需要继续等待/重试的台账行
            - stats：ready / retried / failed / staged_deleted / staged_cleanup_failed
        """
        stats = {
            "ready": 0,
            "retried": 0,
            "failed": 0,
            "staged_deleted": 0,
            "staged_cleanup_failed": 0,
        }
        remaining: list[dict[str, Any]] = []  # 本轮结束后仍需继续跟踪的台账行
        for item in items:
            if await self._refresh_one_item(item, base_id=base_id, stats=stats):
                remaining.append(item)
        return remaining, stats

    async def _refresh_one_item(
        self, item: dict[str, Any], *, base_id: str | None, stats: dict[str, int]
    ) -> bool:
        """回收单条在途台账行；返回 True 表示留在 remaining 继续跟踪。"""
        item_id = str(item["id"])
        record_id = str(item["source_record_id"])
        document_id = str(item.get("document_id") or "")
        # 优先按台账里记录的文档 ID 查；没有就按“来源 + 源记录 ID”反查
        document = (
            await self._repo(self.repository.get_document, document_id)
            if document_id
            else await self._find_document(record_id, base_id)
        )
        if document is not None and base_id and str(document.get("base_id")) != base_id:
            # 台账指向旧库（已删除）的历史文档：按“未入库”处理，重新入当前库
            document = None
        if document is None:
            # 上传过程中进程中断且没有落库：重新排队
            await self._update_item(item_id, status="failed")
            stats["failed"] += 1
            return False  # 本轮不再跟踪这一行，下一轮会重新上传
        status = str(document.get("status") or "")
        if status == DocumentStatus.DELETED.value:
            # 文档在当前库内已被删除：尊重删除动作，不再重传；暂存副本已无用
            await self._update_item(
                item_id,
                status="deleted",
                last_error="文档已被删除，不再重传",
            )
            await self._cleanup_staged(document=document, stats=stats)
            stats["failed"] += 1
            return False
        if status == DocumentStatus.READY.value:
            # 解析完成：台账收尾为 ready，结束跟踪；删除暂存副本
            await self._update_item(
                item_id,
                document_id=str(document["id"]),
                status="ready",
            )
            await self._cleanup_staged(document=document, stats=stats)
            stats["ready"] += 1
            return False
        if (
            status == DocumentStatus.PENDING.value
            and not str(document.get("remote_document_id") or "")
        ):
            # 上次上传在“已建本地记录、还没拿到远端 ID”之间中断：
            # 标记失败等待重试（状态机不允许 PENDING 直接重试）
            await self._update_item(
                item_id,
                status="failed",
                attempts=self._next_attempt(item),
                last_error="上次上传未完成",
            )
            stats["failed"] += 1
            return False
        if status in _RETRYABLE_DOCUMENT_STATUSES:
            attempts = self._next_attempt(item)  # 这次算第几次尝试
            if attempts <= MAX_UPLOAD_ATTEMPTS:
                try:
                    # 让知识库服务重新提交解析（复用本地原文，不重新上传文件）
                    await self.service.retry_document(
                        document_id=str(document["id"]),
                        principal=self.principal,
                        # 带上次数，审计日志里能区分是第几次重试
                        request_id=f"source-sync-retry:{record_id}:{attempts}",
                    )
                    # 重试成功：台账改为 parsing，并加回 remaining 继续跟踪
                    await self._update_item(
                        item_id,
                        status="parsing",
                        attempts=attempts,
                        last_error=str(document.get("error_message") or "")[:1000],
                    )
                    item["attempts"] = attempts
                    item["status"] = "parsing"
                    stats["retried"] += 1
                    return True
                except Exception as exc:  # noqa: BLE001
                    # 重试动作本身又失败了：记下错误，等下一轮
                    await self._update_item(
                        item_id,
                        status="failed",
                        attempts=attempts,
                        last_error=str(exc)[:1000],
                    )
                    stats["failed"] += 1
            else:
                # 超过最大重试次数：彻底放弃，保留最后一次的错误信息方便排查
                await self._update_item(
                    item_id,
                    status="failed",
                    attempts=attempts,
                    last_error=str(document.get("error_message") or "超过重试次数")[:1000],
                )
                stats["failed"] += 1
            return False
        # 其余状态（解析中 / 暂停 / 已拿到远端 ID 的 PENDING）：
        # 还没结束，原样留在 remaining，下一轮继续检查
        return True

    async def _wait_for_inflight_slot(
        self,
        inflight: list[dict[str, Any]],
        *,
        base_id: str,
        stats: dict[str, int],
    ) -> list[dict[str, Any]]:
        """在途文档达到上限时回收状态，直到腾出空位。

        扫描记录（重试）和上传新记录前都要经过这里，避免两处重复写限流循环。
        """
        while len(inflight) >= self.sync_config.max_inflight:
            inflight, batch_stats = await self.refresh_inflight(
                inflight, base_id=base_id
            )
            self._merge_stats(stats, batch_stats)
            if len(inflight) >= self.sync_config.max_inflight:
                await asyncio.sleep(INFLIGHT_POLL_INTERVAL_SECONDS)
        return inflight
