"""后台 worker：解析状态轮询 + 软删除保留期清理。

上传与重试内联在 ``KnowledgeService.upload_document`` 中同步完成，
解析轮询 worker 只负责把上游解析进度（pending/processing）拉回本地投影；
清理 worker 负责在软删除保留期（30 天）到期后删除远端（Ragflow）文档副本，
避免共享数据集无限膨胀。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from ...db import Database
from ..core.config import KnowledgeConfig
from ..ragflow import RagflowClient
from ..core.repository import KnowledgeRepository
from ..service import KnowledgeService, LocalOriginalStore

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
        """初始化解析轮询 worker；poll_interval_seconds 为轮询间隔（默认 5 秒）。"""

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
            if base is None:
                continue
            try:
                await service.refresh_base_documents(base)
                processed += 1
            except Exception as exc:
                # 单库失败不阻断其余知识库的轮询
                logger.warning("知识库轮询刷新 base=%s 失败: %s", base_id, exc)
        return processed > 0

    async def run_forever(self) -> None:
        """轮询主循环：每 poll_interval_seconds（默认 5 秒）拉取一轮上游进度。

        单轮异常只记日志不退出循环；间隔等待可被 stop 事件立即唤醒，保证
        关停及时。仍处于 pending/processing 的文档由后续轮次持续跟进。
        """

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


class KnowledgePurgeWorker:
    """软删除保留期到期清理：周期删除远端（Ragflow）文档副本。

    删除文档/知识库为软删除（本地保留记录与原文），远端副本暂存
    SOFT_DELETE_RETENTION_DAYS（30 天）后由本 worker 清理，期间可恢复。
    """

    def __init__(
        self,
        *,
        config: KnowledgeConfig,
        ragflow: RagflowClient,
        db_provider: Callable[[], Database | None],
        purge_interval_seconds: float = 3600.0,
        batch_limit: int = 100,
    ):
        """初始化清理 worker；默认每 3600 秒执行一轮，每轮最多清 batch_limit（默认 100）条。"""

        self.config = config
        self.ragflow = ragflow
        # 数据库在宿主 lifespan 中晚于本 worker 初始化，必须惰性获取
        self._db_provider = db_provider
        self.purge_interval_seconds = purge_interval_seconds
        self.batch_limit = batch_limit
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def purge_once(self) -> int:
        """单轮清理；返回本次清理的远端文档数。数据库未就绪时静默跳过。"""

        db = self._db_provider()
        if db is None:
            return 0
        service = KnowledgeService(
            KnowledgeRepository(db), self.ragflow, self.config
        )
        return await service.purge_expired_documents(limit=self.batch_limit)

    async def run_forever(self) -> None:
        """清理主循环：每 purge_interval_seconds（默认 1 小时）清一轮到期远端文档。

        单轮异常只记日志不退出；等待期间可被 stop 事件提前唤醒。
        """

        logger.info(
            "知识库软删除清理 worker 已启动 | interval=%ss, batch=%s",
            self.purge_interval_seconds,
            self.batch_limit,
        )
        try:
            while not self._stop.is_set():
                try:
                    await self.purge_once()
                except Exception as exc:
                    logger.warning("知识库清理单轮执行失败: %s", exc)
                try:
                    await asyncio.wait_for(
                        self._stop.wait(), timeout=self.purge_interval_seconds
                    )
                except TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("知识库软删除清理 worker 已停止")

    def start(self) -> None:
        """在当前事件循环中启动后台清理任务（幂等）。"""

        if self.is_running:
            return
        self._stop.clear()
        self._task = asyncio.create_task(
            self.run_forever(), name="knowledge-purge-worker"
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


__all__ = ["KnowledgeParsePollWorker", "KnowledgePurgeWorker"]
