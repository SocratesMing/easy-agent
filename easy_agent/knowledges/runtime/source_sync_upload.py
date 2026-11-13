"""研报同步的单条入库：日期目录、台账校验、文件定位与上传。"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool

from easy_agent.knowledges.core.models import DocumentMetadata
from easy_agent.knowledges.runtime.source_sync_support import (
    SourceRow,
    build_staged_name,
    date_folder_segments,
    describe_missing_source,
    resolve_batchid_file,
    sanitize_title,
)

logger = logging.getLogger(__name__)


class SourceSyncUploadMixin:
    """单条入库方法；由 SourceSyncEngine 组合，复用其仓储、服务与配置。"""

    async def ensure_date_folder(self, base_id: str, publish_date: str) -> str | None:
        """确保 ``YYYY年/MM月/DD日`` 三级目录存在，返回日目录 ID。

        ensure_folders 收到的是一整条路径（如 "2026年/10月/23日"），
        它会“缺哪层建哪层”。但它不返回最底层目录的 ID，所以这里创建完
        再逐层往下查一遍，拿到“日”目录的 ID——上传文档时要靠它指定放哪里。
        """
        year, month, day = date_folder_segments(publish_date)
        await self.service.ensure_folders(
            base_id=base_id,
            folder_id=None,  # None 表示从知识库根目录开始
            paths=["/".join([year, month, day])],  # 一次性传入完整路径
            principal=self.principal,
        )
        # ensure_folders 只保证目录存在，这里再定位叶子（日）目录 ID
        parent: str | None = None  # None 表示从这里开始是根层级
        for segment in (year, month, day):
            folder = await self._repo(
                self.repository.find_folder_by_name, base_id, segment, parent
            )
            if folder is None:
                # 理论上不会发生（刚 ensure 过），真发生说明目录被并发删了，直接报错
                raise RuntimeError(
                    f"日期目录创建后未找到: {base_id}/{year}/{month}/{day}"
                )
            parent = str(folder["id"])  # 下一层在这个目录里面找
        return parent  # 循环结束后，parent 就是“日”目录的 ID

    # ---------------- 单条入库 ----------------

    async def _upload_row(
        self,
        *,
        base_id: str,
        row: SourceRow,
        staged_file: Path | None = None,
        stage_error: str | None = None,
    ) -> dict[str, Any] | None:
        """处理一条源记录：校验 → 找文件 → 上传 → 写台账。

        参数：
            row: 已经解析好的 SourceRow（字段取值只在 run() 里做一次）。
            staged_file: 批量暂存阶段已经拷贝好的文件；为 None 时回退直接读源文件。
            stage_error: 暂存阶段失败原因；非空时直接记台账失败，不再上传。

        返回值：
        - 成功提交上传：返回“刷新后的台账行”（dict），调用方据此判断是否进入在途列表；
        - 跳过或失败：返回 None（失败原因已经写进台账的 last_error 字段）。
        """
        # ---- 第 1~6 步：校验主键、本地去重、台账行、待上传文件 ----
        resolved = await self._resolve_upload_source(
            base_id=base_id,
            row=row,
            staged_file=staged_file,
            stage_error=stage_error,
        )
        if resolved is None:
            return None
        record_id = row.record_id
        item_id, attempts, source_file, publish_date = resolved

        # ---- 第 7~8 步：日期目录、上传名、Content-Type、业务元数据 ----
        folder_id, upload_name, content_type, metadata = await self._build_upload_spec(
            base_id, row, source_file, publish_date
        )
        # ---- 第 9 步：台账先标记“上传中”，然后真正上传文件 ----
        await self._update_item(item_id, status="uploading")
        try:
            # with open(...) as f：文件用完会自动关闭（即使中途报错也会关）
            with source_file.open("rb") as content:
                accepted = await self.service.upload_document(
                    base_id=base_id,
                    folder_id=folder_id,
                    filename=upload_name,
                    content_type=content_type,
                    size_bytes=source_file.stat().st_size,  # 文件大小（字节）
                    content=content,  # 文件内容（二进制流）
                    principal=self.principal,
                    # request_id 会写进审计日志，方便按“源记录”追溯这次操作
                    request_id=f"source-sync:{record_id}",
                    metadata=metadata,
                )
        except Exception as exc:  # noqa: BLE001 — 单条失败不阻断整批
            # 上传异常不代表“什么都没发生”：可能本地记录/原文已经建好，
            # 只是调用远端失败。这里再查一次，把已有文档 ID 记到台账上，
            # 后续重试就能直接复用本地原文，避免重复建记录。
            document = await self._find_document(record_id, base_id)
            await self._update_item(
                item_id,
                document_id=str(document["id"]) if document else None,
                status="failed",
                attempts=attempts,
                # 截断到 1000 字符，防止异常信息过长把数据库字段撑爆
                last_error=str(exc)[:1000],
            )
            logger.error(
                "文档同步失败 | id=%s file=%s err=%s", record_id, source_file, exc
            )
            return None

        # ---- 第 10 步：上传成功，台账改为 parsing（等待知识库解析完成）----
        document = accepted.document
        await self._update_item(
            item_id,
            document_id=str(document.id),
            status="parsing",
            publish_date=publish_date,
            last_error=None,  # 清掉历史错误信息
        )
        logger.info(
            "文档同步已提交解析 | id=%s name=%s date=%s doc=%s",
            record_id,
            upload_name,
            publish_date,
            document.id,
        )
        # 重新读一次台账行再返回：期间状态可能已经被别处更新（比如解析秒完成），
        # 返回最新数据，调用方才能正确判断它是否还需要继续跟踪。
        return await self._repo(
            self.repository.get_sync_item, self.source, record_id
        )

    async def _resolve_upload_source(
        self,
        *,
        base_id: str,
        row: SourceRow,
        staged_file: Path | None,
        stage_error: str | None,
    ) -> tuple[str, int, Path, str] | None:
        """校验主键与本地去重、创建台账行，并定位待上传文件。

        返回 ``(台账行 ID, 本次尝试序号, 待上传文件, 发布日期)``；
        跳过或已记失败时返回 None（失败原因已经写进台账的 last_error）。
        """
        # ---- 第 1 步：校验主键 ----
        if not row.record_id:
            # 没有主键就无法去重（不知道是不是同一条），只能跳过
            logger.warning("文档同步跳过：源记录缺少 id | row=%s", row)
            return None
        record_id = row.record_id
        publish_date = row.publish_date
        batch_id = row.batch_id

        # ---- 第 2 步：本地是否已有这条源记录对应的文档？----
        # 兜底：同一源记录已存在本地文档时不再新建，避免触发唯一约束冲突
        # include_deleted=True：连已删除的也算上，避免“删了又建”的死循环
        existing = await self._find_document(record_id, base_id)
        if existing is not None:
            # 并发/重跑兜底：批量暂存时可能已经拷贝过，但这次不需要再上传，
            # 顺手删掉暂存副本，避免留下孤儿文件。
            if staged_file is not None:
                await self._cleanup_staged(staged_file)
            logger.info(
                "文档同步跳过：本地已有记录 | id=%s doc=%s status=%s",
                record_id,
                existing["id"],
                existing.get("status"),
            )
            return None

        # ---- 第 3 步：创建（或复用）台账行 ----
        # create_or_get_sync_item 是“有则取、无则建”：同一个来源 + 源记录 ID
        # 只会有一条台账，重复调用拿到的就是同一行，顺便带回之前失败过几次（attempts）。
        item = await self._repo(
            self.repository.create_or_get_sync_item,
            source=self.source,
            source_record_id=record_id,
            publish_date=publish_date,
        )
        item_id = str(item["id"])  # 台账行 ID，之后所有状态更新都靠它
        attempts = self._next_attempt(item)  # 本次是第几次尝试

        # ---- 第 4 步：发布日期必须能解析出 YYYY-MM-DD ----
        if not publish_date:
            await self._fail_item(
                item_id,
                record_id=record_id,
                reason="publishDate 无法解析",
                attempts=attempts,
            )
            return None
        # ---- 第 5 步：暂存失败直接记台账，不再扫描/上传 ----
        if stage_error:
            # suppress_log=True：批量场景下由 run() 统一汇总，不逐条打 warning
            await self._fail_item(
                item_id,
                record_id=record_id,
                reason=stage_error,
                attempts=attempts,
                suppress_log=True,
            )
            return None
        # ---- 第 6 步：定位本次要上传的文件 ----
        # 优先用批量暂存好的副本；未配置暂存目录时回退直接读 file_root。
        if staged_file is not None:
            source_file = staged_file
            if not source_file.is_file():
                await self._fail_item(
                    item_id,
                    record_id=record_id,
                    reason=f"暂存文件不存在: {source_file}",
                    attempts=attempts,
                )
                return None
        else:
            # 文件定位：只用文档 SQL 的 batchid 在 file_root 下匹配同名文件，
            # batchid 唯一，匹配不到即磁盘没有该文件，不做其它字段兜底。
            source_file = await run_in_threadpool(
                resolve_batchid_file, self.sync_config, batch_id
            )
            if source_file is None:
                # suppress_log=True：批量场景下由 run() 统一汇总，不逐条打 warning
                await self._fail_item(
                    item_id,
                    record_id=record_id,
                    reason=describe_missing_source(self.sync_config, batch_id),
                    attempts=attempts,
                    suppress_log=True,
                )
                return None
            if self._staging_dir is not None:
                # run() 会先批量暂存；走到这里说明调用链异常，回退直读但记一笔日志。
                logger.warning(
                    "暂存文件缺失，回退直接读取源文件 | id=%s file=%s",
                    record_id,
                    source_file,
                )
        return item_id, attempts, source_file, publish_date

    async def _build_upload_spec(
        self, base_id: str, row: SourceRow, source_file: Path, publish_date: str
    ) -> tuple[str | None, str, str, DocumentMetadata]:
        """确保日期目录存在，并组装上传文件名 / Content-Type / 业务元数据。"""
        record_id = row.record_id
        # ---- 第 7 步：确保日期目录存在，拿到“日”目录 ID ----
        folder_id = await self.ensure_date_folder(base_id, publish_date)
        # ---- 第 8 步：拼上传文件名、猜 Content-Type、组装业务元数据 ----
        # 复用暂存命名函数，保证上传名与暂存文件名完全一致（READY 后靠它反推路径）。
        upload_name = build_staged_name(row.title, record_id, source_file)
        content_type = (
            mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
        )
        metadata = DocumentMetadata(
            source=self.source,
            source_record_id=record_id,
            batch_id=row.batch_id,
            report_title=sanitize_title(row.title),
            report_tag=row.report_tag,
            report_second_source=row.second_source,
            publish_date=publish_date,
        )
        return folder_id, upload_name, content_type, metadata
