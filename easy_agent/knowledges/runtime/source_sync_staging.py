"""研报同步的批量暂存：暂存目录准备、文件拷贝与暂存副本清理。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool

from easy_agent.knowledges.runtime.source_sync_support import (
    SourceRow,
    _STAGED_KEEP_STATUSES,
    build_staged_name,
    copy_to_staging,
    describe_missing_source,
    ensure_staging_dir,
    remove_file_if_exists,
    resolve_batchid_file,
    resolve_staging_dir,
)

logger = logging.getLogger(__name__)


class SourceSyncStagingMixin:
    """批量暂存方法；由 SourceSyncEngine 组合，复用其仓储与配置。"""

    async def prepare_staging(self) -> None:
        """解析暂存配置：目录存在就直接用，不存在就创建。"""
        staging_dir = resolve_staging_dir(self.sync_config)
        if staging_dir is None:
            self._staging_dir = None
            return
        try:
            await run_in_threadpool(ensure_staging_dir, staging_dir)
        except OSError as exc:
            raise RuntimeError(
                f"暂存目录不可用: source_sync.staging_dir={staging_dir}: {exc}"
            ) from exc
        self._staging_dir = staging_dir
        logger.info("文档同步暂存目录就绪 | dir=%s", staging_dir)

    async def _staged_keep_names(self, base_id: str) -> set[str]:
        """收集失败/解析中文档的暂存文件名，TTL 清理时跳过它们。

        用户确认“上传/解析失败保留暂存文件”，所以按文档名做白名单；
        READY/DELETED 的文件不保留，删除失败时由下一轮清理补删。
        """
        page_size = max(1, int(self.config.limits.max_documents_per_query))
        offset = 0
        names: set[str] = set()
        while True:
            documents = await self._repo(
                self.repository.list_documents,
                base_id,
                limit=page_size,
                offset=offset,
            )
            if not documents:
                break
            for document in documents:
                name = str(document.get("name") or "")
                if (
                    name
                    and str(document.get("status") or "") in _STAGED_KEEP_STATUSES
                ):
                    names.add(Path(name).name)
            if len(documents) < page_size:
                break
            offset += page_size
        return names

    async def stage_rows(
        self, rows: list[SourceRow]
    ) -> tuple[dict[str, Path], dict[str, str], dict[str, int]]:
        """把一个窗口内待入库的文件批量拷贝到暂存目录。

        返回值：
        - staged：record_id → 暂存文件路径；
        - errors：record_id → 暂存失败原因（上传前就会写进台账）；
        - stats：{"copied": n, "copy_failed": n}。
        同名暂存文件会被最新源文件直接覆盖。

        只有去重通过的新记录才会走到这里，不会重复拷贝已入库文件。
        """
        stage_stats = {"copied": 0, "copy_failed": 0, "missing_source": 0}
        staged: dict[str, Path] = {}
        errors: dict[str, str] = {}
        for row in rows:
            if not row.record_id:
                continue
            # 文件定位与上传阶段保持一致：只用 batchid 在 file_root 下匹配。
            source_file = await run_in_threadpool(
                resolve_batchid_file, self.sync_config, row.batch_id
            )
            if source_file is None:
                # 没找到源文件时不拷贝，但仍把原因带回去，让台账记失败。
                # 计数加一，run() 里统一汇总打印，避免每条都打一条 warning。
                stage_stats["missing_source"] += 1
                errors[row.record_id] = describe_missing_source(
                    self.sync_config, row.batch_id
                )
                continue
            # 未配置暂存目录时跳过拷贝，直接返回空 staged 供直读路径使用。
            if self._staging_dir is None:
                continue
            staged_file = self._staging_dir / build_staged_name(
                row.title, row.record_id, source_file
            )
            try:
                await run_in_threadpool(copy_to_staging, source_file, staged_file)
            except Exception as exc:  # noqa: BLE001 — 单条失败不阻断整批
                reason = f"暂存拷贝失败: {exc}"
                errors[row.record_id] = reason[:1000]
                stage_stats["copy_failed"] += 1
                logger.error(
                    "文档暂存拷贝失败 | id=%s src=%s dst=%s err=%s",
                    row.record_id,
                    source_file,
                    staged_file,
                    exc,
                )
                continue
            staged[row.record_id] = staged_file
            stage_stats["copied"] += 1
            logger.info(
                "文档已暂存（同名覆盖） | id=%s src=%s dst=%s",
                row.record_id,
                source_file,
                staged_file,
            )
        return staged, errors, stage_stats

    async def _cleanup_staged(
        self,
        path: Path | None = None,
        *,
        document: dict[str, Any] | None = None,
        stats: dict[str, int] | None = None,
    ) -> None:
        """删除暂存副本（按路径或文档上传名定位），删除失败不阻断同步。

        READY / DELETED 时调用方手里只有 ``knowledge_documents.name``
        （上传名），暂存文件与它同名，因此传 ``document`` 即可反推路径，
        不需要给台账加 ``staged_path`` 字段。``stats`` 非空时按删除结果
        累加 staged_deleted / staged_cleanup_failed。
        """
        if document is not None:
            if self._staging_dir is None:
                return
            name = str(document.get("name") or "")
            if not name:
                return
            path = self._staging_dir / Path(name).name
        if path is None:
            return
        try:
            deleted = await run_in_threadpool(remove_file_if_exists, path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("暂存文件删除失败 | path=%s err=%s", path, exc)
            result = "failed"
        else:
            if deleted:
                logger.info("暂存文件已删除 | path=%s", path)
                result = "deleted"
            else:
                result = "missing"
        if stats is None:
            return
        if result == "deleted":
            stats["staged_deleted"] += 1
        elif result == "failed":
            stats["staged_cleanup_failed"] += 1
