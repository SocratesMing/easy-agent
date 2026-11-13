"""研报 SQL 同步执行器：差集判定、滑动窗口上传、在途回收与重试。"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool

from easy_agent.knowledges.core.config import KnowledgeConfig, SourceSyncConfig
from easy_agent.knowledges.core.models import (
    DocumentStatus,
    KnowledgeBaseStatus,
    KnowledgeBaseVisibility,
    KnowledgePrincipal,
)
from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.knowledges.runtime.source_sync_inflight import SourceSyncInflightMixin
from easy_agent.knowledges.runtime.source_sync_staging import SourceSyncStagingMixin
from easy_agent.knowledges.runtime.source_sync_upload import SourceSyncUploadMixin
from easy_agent.knowledges.runtime.source_sync_support import (
    INFLIGHT_POLL_INTERVAL_SECONDS,
    MAX_UPLOAD_ATTEMPTS,
    SOURCE_SYNC_SOURCE,
    SourceRow,
    _ACTIVE_ITEM_STATUSES,
    _READY_DOCUMENT_STATUSES,
    _RETRYABLE_DOCUMENT_STATUSES,
    _iter_windows,
    build_upload_name,
    cleanup_stale_staging_files,
    fetch_source_rows,
    parse_source_row,
    resolve_staging_dir,
)
from easy_agent.knowledges.service import KnowledgeService

logger = logging.getLogger(__name__)


class SourceSyncEngine(
    SourceSyncInflightMixin, SourceSyncStagingMixin, SourceSyncUploadMixin
):
    """单次同步执行器：解析、差集、滑动窗口上传、状态回收。

    【async 小课堂】def 前面加了 async 的函数叫“协程”：
    - 调用它不会立刻执行，只会返回一个“待执行的任务”；
    - 必须被 await（或交给事件循环）才会真正运行；
    - 执行到 await 时可以“让出” CPU 去干别的事，所以适合大量网络 IO。

    本类中的三种典型调用：
    - await self._repo(...)        → 数据库操作（丢到线程池，不卡事件循环）；
    - await self.service.xxx(...)  → 调用知识库服务（异步 HTTP 请求）；
    - await self._update_item(...) → 更新本地台账状态。
    """

    def __init__(
        self,
        *,
        config: KnowledgeConfig,
        sync_config: SourceSyncConfig,
        repository: KnowledgeRepository,
        service: KnowledgeService,
        principal: KnowledgePrincipal,
    ):
        self.config = config  # 知识库总配置（embedding 模型等）
        self.sync_config = sync_config  # 同步专属配置（MySQL、目录、并发上限等）
        self.repository = repository  # 数据库仓储：台账、文档、知识库、目录的增删改查
        self.service = service  # 知识库服务：真正执行上传/建目录/重试解析
        self.principal = principal  # 以哪个用户身份操作（权限校验、审计都会用到）
        self.source = SOURCE_SYNC_SOURCE  # 来源标识，固定为 "source_sync"
        # 暂存目录：None 表示未配置（直接读源文件）；run() 开始时会解析并创建
        self._staging_dir: Path | None = None

    async def _repo(self, method, *args, **kwargs):
        """同步仓储调用放入线程池执行，避免阻塞事件循环。

        self.repository 里的方法都是普通同步函数（内部走数据库驱动），
        直接调用会卡住 async 事件循环；run_in_threadpool 会把它们放到
        线程池的线程里执行，await 等结果，期间事件循环可以继续干别的。
        """

        return await run_in_threadpool(method, *args, **kwargs)

    async def _update_item(self, item_id: str, **fields: object) -> None:
        """更新同步台账行（统一走线程池）。

        item_id: 台账行的 ID
        **fields: 要更新的列，例如 status="failed"、attempts=2、last_error="..."
        """

        await self._repo(self.repository.update_sync_item, item_id, **fields)

    async def _find_document(
        self,
        record_id: str,
        base_id: str | None,
        *,
        include_deleted: bool = True,
    ) -> dict[str, Any] | None:
        """按“来源 + 源记录 ID”查本地文档（可限定知识库）。

        include_deleted 默认 True：同步判定要连软删除行一起看，
        避免“删了又建”的死循环。
        """
        return await self._repo(
            self.repository.find_document_by_source_record,
            self.source,
            record_id,
            base_id=base_id,
            include_deleted=include_deleted,
        )

    @staticmethod
    def _merge_stats(stats: dict[str, int], batch: dict[str, int]) -> None:
        """把一轮批次统计累加进本次同步的总统计。"""
        for key, value in batch.items():
            stats[key] += value

    @staticmethod
    def _next_attempt(item: dict[str, Any]) -> int:
        """本次尝试序号：台账里已记录的失败次数 + 1。"""
        return int(item.get("attempts") or 0) + 1

    async def _fail_item(
        self,
        item_id: str,
        *,
        record_id: str,
        reason: str,
        attempts: int,
        document_id: str | None = None,
        suppress_log: bool = False,
    ) -> None:
        """把台账置为 failed，统一记录原因/次数，避免各处重复拼字段。

        attempts 为本次尝试序号（``_next_attempt`` 的结果）。
        suppress_log=True 时不打单条 warning，由调用方在批量场景下统一汇总。
        """
        fields: dict[str, object] = {
            "status": "failed",
            "attempts": attempts,
            "last_error": str(reason)[:1000],
        }
        if document_id is not None:
            fields["document_id"] = document_id
        await self._update_item(item_id, **fields)
        if not suppress_log:
            logger.warning("文档同步失败 | id=%s reason=%s", record_id, reason)

    # ---------------- 知识库与目录 ----------------

    async def resolve_base_id(self) -> str:
        """确定入库目标库：``base_id`` 优先；为空时按 ``base_name`` 查找/新建。

        名字在 ``knowledge_bases`` 内不唯一（同名多库时按名称会取最早创建的
        那个），需要精确指定时填 ``base_id``；新建库固定为团队公共库。

        三种情况：
        1. 配置了 base_id → 必须存在且未删除，直接用它；
        2. 没配 base_id，但按 base_name 找到了同名库 → 用这个库；
        3. 都没找到 → 自动新建一个“团队公共库”，并把状态置为 ACTIVE。
        """
        sync_config = self.sync_config
        if sync_config.base_id.strip():  # 情况 1：明确指定了库 ID
            base = await self._repo(
                self.repository.get_base, sync_config.base_id.strip()
            )
            if base is None or str(base.get("status")) in {"deleted", "deleting"}:
                # 指定的库不存在、已删除或正在删除：直接报错，而不是悄悄换一个库
                raise RuntimeError(
                    f"source_sync.base_id 不存在或已删除: {sync_config.base_id}"
                )
            return str(base["id"])

        # 情况 2：按名称查找
        base = await self._repo(
            self.repository.find_base_by_name, sync_config.base_name
        )
        if base is not None:
            return str(base["id"])
        # 情况 3：没有就新建一个团队公共库
        # embedding_model 留空——真实模型由共享数据集统一决定；
        # create_base 不触发 RAGFlow 调用（纯本地建库）
        created = await self._repo(
            self.repository.create_base,
            name=sync_config.base_name,
            description="由文档 SQL 同步自动创建",
            # 研报接入固定入公共库（team），不提供个人库配置
            space_type=KnowledgeBaseVisibility.TEAM.value,
            owner_user_id=self.principal.user_id,
            department_id=self.principal.department_id,
            embedding_model="",
            # 新建库没有历史文档：后续上传即写 base_id 元数据，直接走 meta 检索
            meta_backfilled=1,
        )
        # 新建的库默认不是 ACTIVE，这里显式激活，后面才能往里传文档
        await self._repo(
            self.repository.update_base,
            created["id"],
            status=KnowledgeBaseStatus.ACTIVE.value,
        )
        logger.info("文档同步已创建知识库 | id=%s name=%s", created["id"], created["name"])
        return str(created["id"])

    # ---------------- 批量暂存 ----------------

    # ---------------- 主循环 ----------------

    async def _handle_existing_document(
        self,
        document: dict[str, Any],
        row: SourceRow,
        *,
        stats: dict[str, int],
        inflight: list[dict[str, Any]],
    ) -> bool:
        """处理“本地已有该源记录对应文档”的分支；返回 True 表示本行已处理完。

        返回 False 表示文档状态不在任何已知分支（既不需要跳过也不能重试），
        由调用方继续按新记录处理。
        """

        record_id = row.record_id
        status = str(document.get("status") or "")
        if status == DocumentStatus.DELETED.value:
            # 当前库内已被删除：尊重删除动作，不再重新上传
            logger.info(
                "文档同步跳过：文档已删除 | id=%s doc=%s",
                record_id,
                document["id"],
            )
            stats["existing"] += 1
            return True
        remote_id = str(document.get("remote_document_id") or "")
        if status == DocumentStatus.PENDING.value and not remote_id:
            # 上传中断（本地记录已建、远端还没拿到 ID）：
            # 先把状态改成 FAILED，让它进入“可重试”分支，
            # 重试会复用本地原文，避免重复建本地记录。
            document = await self._repo(
                self.repository.update_document,
                str(document["id"]),
                status=DocumentStatus.FAILED.value,
                error_code="SYNC_UPLOAD_INTERRUPTED",
                error_message="上次上传未完成，重新提交",
            )
            status = DocumentStatus.FAILED.value
        elif status in _READY_DOCUMENT_STATUSES:
            # 已完成/解析中/暂停：都不需要再上传，直接跳过
            stats["existing"] += 1
            return True
        if status in _RETRYABLE_DOCUMENT_STATUSES:
            await self._retry_existing_document(
                document, row, stats=stats, inflight=inflight
            )
            return True
        return False

    async def _retry_existing_document(
        self,
        document: dict[str, Any],
        row: SourceRow,
        *,
        stats: dict[str, int],
        inflight: list[dict[str, Any]],
    ) -> None:
        """对可重试状态的本地文档重新提交解析，并维护台账与在途列表。"""

        record_id = row.record_id
        # 走重试：先确保有台账行，再累计尝试次数
        retry_item = await self._repo(
            self.repository.create_or_get_sync_item,
            source=self.source,
            source_record_id=record_id,
            publish_date=row.publish_date,
        )
        retry_item_id = str(retry_item["id"])
        attempts = self._next_attempt(retry_item)
        if attempts > MAX_UPLOAD_ATTEMPTS:
            # 次数用完了：不再重试，记下失败原因
            await self._update_item(
                retry_item_id,
                status="failed",
                attempts=attempts,
                last_error="超过最大重试次数",
            )
            stats["failed"] += 1
            return
        try:
            await self.service.retry_document(
                document_id=str(document["id"]),
                principal=self.principal,
                request_id=f"source-sync-retry:{record_id}:{attempts}",
            )
        except Exception as exc:  # noqa: BLE001
            # 这次重试没成功：记录错误，下一轮再说
            await self._update_item(
                retry_item_id,
                document_id=str(document["id"]),
                status="failed",
                attempts=attempts,
                last_error=str(exc)[:1000],
            )
            stats["failed"] += 1
            logger.error("研报文档重试失败 | id=%s err=%s", record_id, exc)
            return
        # 重试成功：台账改为 parsing，并放进在途列表继续跟踪
        await self._update_item(
            retry_item_id,
            document_id=str(document["id"]),
            status="parsing",
            attempts=attempts,
        )
        refreshed = await self._repo(
            self.repository.get_sync_item, self.source, record_id
        )
        if refreshed is not None:
            inflight.append(refreshed)
        stats["retried"] += 1

    async def run(
        self,
        *,
        limit: int | None = None,
        dry_run: bool = False,
        wait_for_inflight: bool = True,
    ) -> dict[str, int]:
        """执行一次完整的同步，返回统计信息。

        参数：
            limit:      最多扫描多少条源记录，主要给测试/演练用（达到后提前结束）；
            dry_run:    演习模式：不建库、不上传，只打印“本来会入库”的清单；
            wait_for_inflight: 收尾时是否等在途文档解析完成。定时触发传 False，
                        只提交本批上传就返回；一次性/命令行场景保持 True。

        整体流程：
            1. 确定目标知识库（base_id）；
            2. 决定时间窗口：只回溯 lookback_days 天；
            3. 恢复上次中断时还在途的文档，并清理超期暂存残留；
            4. 按窗口拉取源记录：已入库→跳过，失败→重试；
            5. 新记录先批量拷贝到暂存目录，再从暂存文件上传；
            6. wait_for_inflight=True 时等在途文档全部解析完（READY 后删除暂存副本），
               再打印统计；定时触发传 False，剩余解析交给下一轮回收。
        """
        # stats 是本次同步的“统计计数器”，最终会打印到日志，也作为返回值给调用方：
        #   scanned  : 从文档 SQL 读到的记录条数
        #   existing : 本地已入库而跳过的条数
        #   uploaded : 本次新提交上传的条数
        #   failed   : 失败条数（含在途回收时判定失败的）
        #   retried  : 触发重试的条数
        #   ready    : 直接确认为已完成的条数
        #   planned  : dry-run 模式下“本来会入库”的条数
        #   truncated: 是否因为 limit 提前停止（1=是，0=否）
        #   copied   : 本批实际写入暂存目录的文件数（同名覆盖）
        #   copy_failed: 暂存拷贝失败条数
        #   missing_source: batchid 未匹配到磁盘文件的条数（批量汇总打一条日志）
        #   staged_deleted: READY/DELETED 后删除的暂存文件数
        #   staged_cleanup_failed: 暂存文件删除失败数
        #   stale_cleaned: 本轮开始时清理的超期暂存残留数
        stats = {
            "scanned": 0,
            "existing": 0,
            "uploaded": 0,
            "failed": 0,
            "retried": 0,
            "ready": 0,
            "planned": 0,
            "truncated": 0,
            "copied": 0,
            "copy_failed": 0,
            "missing_source": 0,
            "staged_deleted": 0,
            "staged_cleanup_failed": 0,
            "stale_cleaned": 0,
        }
        if not dry_run:
            # 暂存目录：存在直接用，不存在创建
            await self.prepare_staging()
        else:
            # 演习模式只校验路径，不创建目录、不拷贝、不删除
            self._staging_dir = resolve_staging_dir(self.sync_config)
        # dry-run 不需要（也不应该）建库，所以跳过解析目标库
        base_id = await self.resolve_base_id() if not dry_run else ""
        if not dry_run and self._staging_dir is not None:
            # 清理超期孤儿文件；失败/解析中文档的暂存副本保留
            keep_names = await self._staged_keep_names(base_id)
            stats["stale_cleaned"] = await run_in_threadpool(
                cleanup_stale_staging_files,
                self._staging_dir,
                self.sync_config.staging_retention_hours,
                keep_names=keep_names,
            )
        now = datetime.now(UTC)  # 本次同步的“截止时间”，统一用 UTC，避免时区问题
        window_start = now - timedelta(days=self.sync_config.lookback_days)
        # 把 [起始时间, 现在) 按 lookback_days 天切成若干段，逐段查询，
        # 避免一次性把几个月的数据全部拉进内存
        windows = list(_iter_windows(window_start, now, self.sync_config.lookback_days))

        inflight: list[dict[str, Any]] = []  # 在途台账行（上传/解析中，尚未结束）
        if not dry_run:
            # 断点续跑：恢复上次中断时仍在 uploading/parsing 的台账行
            inflight = await self._repo(
                self.repository.list_sync_items,
                source=self.source,
                statuses=list(_ACTIVE_ITEM_STATUSES),
                limit=self.sync_config.max_inflight,
            )
            if inflight:
                # 先检查它们对应的文档现在到底是什么状态，再决定继续等还是重试
                inflight, resumed_stats = await self.refresh_inflight(
                    inflight, base_id=base_id
                )
                self._merge_stats(stats, resumed_stats)
                logger.info("研报 SQL 同步恢复在途文档 | count=%s", len(inflight))
        for window_start_at, window_end_at in windows:
            # 查询数据库是阻塞操作，必须放线程池，否则会卡住事件循环
            rows = await run_in_threadpool(
                fetch_source_rows, self.sync_config, window_start_at, window_end_at
            )
            logger.info(
                "研报 SQL 同步窗口 | start=%s end=%s rows=%s",
                window_start_at,
                window_end_at,
                len(rows),
            )
            # 先扫描本窗口：已入库/可重试的记录当场处理，新记录收集起来
            pending_rows: list[SourceRow] = []
            for raw_row in rows:
                # limit 到达后提前结束（演练/测试用），并标记“被截断”
                if limit is not None and stats["scanned"] >= limit:
                    stats["truncated"] = 1
                    break
                stats["scanned"] += 1
                parsed_row = parse_source_row(raw_row)
                record_id = parsed_row.record_id
                # 在途文档达到上限时先回收，避免重试/上传突破并发上限
                inflight = await self._wait_for_inflight_slot(
                    inflight, base_id=base_id, stats=stats
                )
                # 先查本地是否已有这条源记录对应的文档（用于“跳过”或“重试”判断）
                document = (
                    await self._find_document(record_id, base_id) if record_id else None
                )
                if document is not None and await self._handle_existing_document(
                    document, parsed_row, stats=stats, inflight=inflight
                ):
                    continue
                if dry_run:
                    # 演习模式：只记录“本来会入库”，不做任何写操作
                    stats["planned"] += 1
                    logger.info(
                        "[dry-run] 待入库 | id=%s date=%s batchid=%s name=%s",
                        record_id,
                        parsed_row.publish_date,
                        parsed_row.batch_id,
                        build_upload_name(
                            parsed_row.title,
                            record_id,
                            parsed_row.batch_id,
                        ),
                    )
                    continue
                # 新记录先收集：等本窗口扫描完，统一批量拷贝到暂存目录
                pending_rows.append(parsed_row)

            # ---- 批量暂存：先把本窗口内要新入库的文件拷到暂存目录 ----
            staged_files: dict[str, Path] = {}
            stage_errors: dict[str, str] = {}
            if pending_rows and not dry_run:
                staged_files, stage_errors, stage_stats = await self.stage_rows(
                    pending_rows
                )
                self._merge_stats(stats, stage_stats)
                # 单独汇总打一条，missing_source 不再逐条打日志
                logger.info(
                    "研报 SQL 批量暂存完成 | start=%s end=%s rows=%s "
                    "copied=%s failed=%s missing_source=%s",
                    window_start_at,
                    window_end_at,
                    len(pending_rows),
                    stage_stats["copied"],
                    stage_stats["copy_failed"],
                    stage_stats["missing_source"],
                )
                if stage_stats["missing_source"]:
                    logger.info(
                        "本窗口 %d 条研报未在磁盘上找到对应源文件，已跳过",
                        stage_stats["missing_source"],
                    )

            # ---- 从暂存文件逐条上传（滑动窗口限流在这里生效）----
            if not dry_run:
                for parsed_row in pending_rows:
                    record_id = parsed_row.record_id
                    inflight = await self._wait_for_inflight_slot(
                        inflight, base_id=base_id, stats=stats
                    )
                    item = await self._upload_row(
                        base_id=base_id,
                        row=parsed_row,
                        staged_file=staged_files.get(record_id),
                        stage_error=stage_errors.get(record_id),
                    )
                    if item is None:
                        # 跳过或失败，原因已写进台账
                        stats["failed"] += 1
                        continue
                    if str(item.get("status")) != DocumentStatus.READY.value:
                        # 还在解析：放进在途列表，等待后续回收
                        inflight.append(item)
                        stats["uploaded"] += 1
                        continue
                    # 极快解析完成的（比如小文件）：直接记为 ready 并删暂存副本
                    stats["ready"] += 1
                    staged_file = staged_files.get(record_id)
                    if staged_file is not None:
                        await self._cleanup_staged(staged_file, stats=stats)
            # 外层循环也要检查 limit（一次窗口可能有多条记录）
            if limit is not None and stats["scanned"] >= limit:
                break

        # 【收尾】wait_for_inflight=True 时，等在途文档一个个结束（完成或失败）；
        # 定时触发传 False：只提交本批上传就返回，剩余解析交给下一轮开头回收。
        while wait_for_inflight and inflight and not dry_run:
            inflight, batch_stats = await self.refresh_inflight(
                inflight, base_id=base_id
            )
            self._merge_stats(stats, batch_stats)
            if inflight:
                # 还有没结束的：睡一会儿再查，避免疯狂空转打爆知识库接口
                await asyncio.sleep(INFLIGHT_POLL_INTERVAL_SECONDS)

        logger.info(
            "文档同步完成 | " + " ".join(f"{key}={value}" for key, value in stats.items())
        )
        return stats
