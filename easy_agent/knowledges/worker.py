"""后台解析状态轮询 worker。

旧版 worker 是「任务队列消费者」（claim_next_task/心跳/死信/对账/过期清理），
新版上传与重试已内联在 ``KnowledgeService.upload_document`` 中同步完成，
worker 只剩一件事：周期性把上游解析进度（pending/processing）拉回本地投影。
任务队列、心跳、对账与物理清理随基础层裁剪一并移除。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from ..db import Database
from .config import KnowledgeConfig
from .ragflow import RagflowClient
from .repository import KnowledgeRepository
from .service import KnowledgeService, LocalOriginalStore

logger = logging.getLogger(__name__)

# 处于这两个状态的文档意味着解析仍在进行，需要轮询上游进度
_POLLING_STATUSES = ("pending", "processing")


class KnowledgeParsePollWorker:
    """轮询上游解析状态并刷新本地文档投影。"""

    def __init__(
        self,
        *,
        config: KnowledgeConfig,
        ragflow: RagflowClient,
        original_store: LocalOriginalStore | None = None,
        db_provider: Callable[[], Database | None],
        poll_interval_seconds: float = 5.0,
    ):
        self.config = config
        self.ragflow = ragflow
        self.original_store = original_store
        # 数据库在宿主 lifespan 中晚于本 worker 初始化，必须惰性获取
        self._db_provider = db_provider
        self.poll_interval_seconds = poll_interval_seconds
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _pending_base_ids(self, db: Database) -> list[str]:
        """找出仍有待解析/解析中文档的知识库（repository 未提供列表方法，走直查）。"""

        placeholders = ", ".join("?" for _ in _POLLING_STATUSES)
        with db.get_connection() as conn:
            cursor = conn.cursor()
            db._execute(
                cursor,
                "SELECT DISTINCT base_id FROM knowledge_documents "
                f"WHERE status IN ({placeholders})",
                _POLLING_STATUSES,
            )
            return [str(row["base_id"] if isinstance(row, dict) else row[0]) for row in cursor.fetchall()]

    async def poll_once(self) -> bool:
        """单轮轮询；返回是否处理了任何知识库。数据库未就绪时静默跳过。"""

        db = self._db_provider()
        if db is None:
            return False
        try:
            base_ids = await asyncio.to_thread(self._pending_base_ids, db)
        except Exception as exc:
            logger.warning("知识库轮询查询待解析文档失败: %s", exc)
            return False
        if not base_ids:
            return False
        repository = KnowledgeRepository(db)
        service = KnowledgeService(
            repository, self.ragflow, self.config, original_store=self.original_store
        )
        processed = 0
        for base_id in base_ids:
            try:
                base = await asyncio.to_thread(repository.get_base, base_id)
            except Exception as exc:
                logger.warning("知识库轮询读取 base=%s 失败: %s", base_id, exc)
                continue
            if base is None or not base.get("remote_dataset_id"):
                continue
            try:
                await service.refresh_base_documents(base)
                processed += 1
            except Exception as exc:
                # 单库失败不阻断其余知识库的轮询
                logger.warning("知识库轮询刷新 base=%s 失败: %s", base_id, exc)
        return processed > 0

    async def run_forever(self) -> None:
        logger.info("知识库解析轮询 worker 已启动 | interval=%ss", self.poll_interval_seconds)
        try:
            while not self._stop.is_set():
                try:
                    await self.poll_once()
                except Exception as exc:
                    logger.warning("知识库轮询单轮执行失败: %s", exc)
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.poll_interval_seconds
                    )
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("知识库解析轮询 worker 已停止")

    def start(self) -> None:
        """在当前事件循环中启动后台轮询任务（幂等）。"""

        if self.is_running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self.run_forever(), name="knowledge-parse-poll-worker"
        )

    async def stop(self) -> None:
        """请求停止并等待后台任务收尾。"""

        self._stop.set()
        task = self._task
        if task is None:
            return
        self._task = None
        if not task.done():
            task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


__all__ = ["KnowledgeParsePollWorker"]
