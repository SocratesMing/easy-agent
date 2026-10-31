"""后台 worker：解析状态轮询 + 软删除远端副本兜底清理。

上传与重试内联在 ``KnowledgeService.upload_document`` 中同步完成，只把文档
推进到本地 ``pending``；解析轮询 worker 负责观察上游 RUNNING，把
``processing``/``ready``/``failed`` 写回本地投影。清理 worker 兜底清理同步
删除失败或历史遗留的远端（Ragflow）文档副本，避免共享数据集膨胀。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from easy_agent.db import Database
from easy_agent.knowledges.core.config import KnowledgeConfig
from easy_agent.knowledges.ragflow import RagflowClient
from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.knowledges.core.original_store import LocalOriginalStore
from easy_agent.knowledges.service import KnowledgeService

logger = logging.getLogger(__name__)


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
        lock_provider: Callable[[], object] | None = None,
    ):
        """初始化解析轮询 worker；poll_interval_seconds 为轮询间隔（默认 5 秒）。"""

        self.config = config
        self.ragflow = ragflow
        self.original_store = original_store
        # 数据库在宿主 lifespan 中晚于本 worker 初始化，必须惰性获取
        self._db_provider = db_provider
        self.poll_interval_seconds = poll_interval_seconds
        # 多副本 leader 选举：同一轮只允许一个副本真正调用 Ragflow。
        # 未启用分布式锁时 LockHandle.acquired 恒 True（单实例行为不变）。
        if lock_provider is None:
            from easy_agent.persistence.distributed_lock import get_distributed_lock

            lock_provider = get_distributed_lock
        self._lock_provider = lock_provider
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def _refreshable_base_ids(self, db: Database) -> list[str]:
        """找出仍有可轮询解析文档（已有远端副本的 pending/processing）的知识库。"""

        return KnowledgeRepository(db).list_refreshable_base_ids()

    async def poll_once(self) -> bool:
        """单轮轮询；返回是否处理了任何知识库。数据库未就绪时静默跳过。"""

        db = self._db_provider()
        if db is None:
            return False

        lock = self._lock_provider()
        handle = lock.acquire(
            "knowledge:parse-poll", ttl_seconds=max(30.0, self.poll_interval_seconds * 3)
        )
        try:
            if not handle.acquired:
                # 另有副本正在轮询；本轮直接跳过，避免 N 副本重复调用 Ragflow。
                return False
            return await self._poll_locked(db)
        finally:
            handle.release()

    async def _poll_locked(self, db: Database) -> bool:
        try:
            base_ids = await asyncio.to_thread(self._refreshable_base_ids, db)
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
    """软删除远端副本兜底清理：周期删除同步删除失败/历史遗留的 Ragflow 文档。

    正常删除文档/知识库时已同步删除远端副本；本 worker 仅兜底重试失败项与
    历史遗留数据，本地软删除记录与原文保留。
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
        return await service.purge_deleted_documents(limit=self.batch_limit)

    async def run_forever(self) -> None:
        """清理主循环：每 purge_interval_seconds（默认 1 小时）清一轮待兜底远端文档。

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
