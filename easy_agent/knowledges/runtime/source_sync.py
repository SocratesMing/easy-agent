"""文档 SQL → 知识库增量同步。

流程：
1. 从外部 MySQL（文档 SQL）按 ``publishDate`` 取最近窗口内的记录；
2. 按 ``batchid``（与磁盘文件名相同）在 ``file_root`` 下匹配文件，
   生成 ``report_title_id.扩展名`` 的上传名；
3. 按 ``publish_date`` 在知识库内 find-or-create 年/月/日 三级目录；
4. 调用 ``KnowledgeService.upload_document`` 入库，写入业务元数据；
5. 用 ``knowledge_source_sync_items`` 台账 + ``knowledge_documents`` 幂等键做差集，
   按滑动窗口（在途上限）分批上传，失败按次数重试，支持断点续跑。

关闭时（``knowledge.source_sync.enabled=false``）启动流程零副作用。
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import pymysql
    from pymysql.cursors import DictCursor
except ImportError:  # pragma: no cover - 依赖缺失时由运行期报错兜底
    pymysql = None  # type: ignore[assignment]
    DictCursor = None  # type: ignore[assignment]

from starlette.concurrency import run_in_threadpool

from easy_agent.knowledges.core.config import KnowledgeConfig, SourceSyncConfig
from easy_agent.knowledges.core.models import (
    DocumentMetadata,
    DocumentStatus,
    KnowledgeBaseStatus,
    KnowledgeBaseVisibility,
    KnowledgePrincipal,
)
from easy_agent.knowledges.core.original_store import LocalOriginalStore
from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.knowledges.service import KnowledgeService

logger = logging.getLogger(__name__)

_RUN_LOCK = asyncio.Lock()

# 台账中视为“已提交、需要回收状态”的状态：启动时恢复这些行，避免中断后丢失跟踪
_ACTIVE_ITEM_STATUSES = ("uploading", "parsing")
# 本地文档处于这些状态时无需再次上传
_READY_DOCUMENT_STATUSES = {
    DocumentStatus.READY.value,
    DocumentStatus.PENDING.value,
    DocumentStatus.PROCESSING.value,
    DocumentStatus.PAUSED.value,
}
_RETRYABLE_DOCUMENT_STATUSES = {
    DocumentStatus.FAILED.value,
    DocumentStatus.CANCELLED.value,
    DocumentStatus.UNKNOWN.value,
}

_ILLEGAL_FILENAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')
_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d")
_MAX_TITLE_CHARS = 100

# 同步来源标识：写入元数据 sql 的 source 字段，用于幂等与区分来源（固定值，不配置）
SOURCE_SYNC_SOURCE = "source_sync"

# 文档 SQL 表结构固定；表名可通过 source_sync.mysql.table 覆盖。
# 未配置表名时使用默认表并自动建表（首次运行零配置可跑）。
_DEFAULT_SOURCE_TABLE = "easy_agent_report"
_TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,64}$")
_SOURCE_COLUMNS = {
    "record_id": "id",
    "publish_date": "publishDate",
    "batch_id": "batchid",
    "report_title": "reportTile",
    "report_tag": "reportTag",
    "report_second_source": "reportSecondSource",
}
_SOURCE_SELECT = "id, publishDate, batchid, reportTile, reportTag, reportSecondSource"
# 按 batchid 匹配文件名时尝试的扩展名（表里 batchid 与磁盘文件名相同）
_FILE_EXTENSIONS = ("pdf", "doc", "docx", "txt", "md", "xls", "xlsx", "csv", "ppt", "pptx")

_CREATE_DEFAULT_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS `{_DEFAULT_SOURCE_TABLE}` (
    id BIGINT NOT NULL AUTO_INCREMENT,
    reportCode VARCHAR(255),
    batchid VARCHAR(255),
    attacUrl TEXT,
    reportAbstract MEDIUMTEXT,
    reportFirstSource VARCHAR(255),
    reportTile VARCHAR(255),
    reportTag VARCHAR(127),
    ViewPoints MEDIUMTEXT,
    reportSecondSource VARCHAR(127),
    reportContent MEDIUMTEXT,
    publishDate DATETIME,
    file_list_size INT,
    zip_flag TINYINT DEFAULT 0,
    create_time DATETIME,
    PRIMARY KEY (id),
    KEY idx_publish_date (publishDate)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
"""


def resolve_table_name(sync_config: SourceSyncConfig) -> tuple[str, bool]:
    """返回 (表名, 是否需要自动建表)；未配置时用默认表名并建表。"""
    configured = sync_config.mysql.table.strip()
    if not configured:
        return _DEFAULT_SOURCE_TABLE, True
    if not _TABLE_NAME_RE.fullmatch(configured):
        raise RuntimeError(
            f"source_sync.mysql.table 非法（仅允许字母/数字/下划线）: {configured!r}"
        )
    return configured, False


def build_source_query(table: str) -> str:
    """按固定列映射拼查询；table 已在 resolve_table_name 校验。"""
    return (
        f"SELECT {_SOURCE_SELECT} FROM `{table}` "
        "WHERE publishDate >= %(start)s AND publishDate < %(end)s"
    )


# ---------------------------------------------------------------------------
# 字段解析与命名
# ---------------------------------------------------------------------------
def _row_value(row: dict[str, Any], column: str, *aliases: str) -> Any:
    for key in (column, *aliases):
        if key and key in row:
            return row.get(key)
    return None


def parse_publish_date(value: Any) -> str | None:
    """把源表 publishDate 归一化为 YYYY-MM-DD；无法识别返回 None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if hasattr(value, "strftime") and not isinstance(value, str):
        return value.strftime("%Y-%m-%d")  # datetime.date
    text = str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # 兼容 "2026-10-21T10:00:00" / "2026-10-21 10:00:00.000"
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def sanitize_title(title: Any) -> str:
    """标题清洗：非法文件名字符替换为 _，压缩空白并截断到 100 字。"""
    text = str(title or "").strip()
    text = _ILLEGAL_FILENAME.sub("_", text)
    text = re.sub(r"\s+", " ", text).strip(" ._")
    return text[:_MAX_TITLE_CHARS]


def build_upload_name(title: Any, record_id: str, file_path: str) -> str:
    """上传名：``{report_title}_{id}{原扩展名}``；标题为空时 ``{id}{扩展名}``。

    文件名总字节数受知识库 ``max_filename_utf8_bytes``（默认 127）限制，
    中文标题按 UTF-8 字节二次裁剪，避免上传阶段被拒。
    """
    extension = Path(file_path).suffix
    cleaned = sanitize_title(title)
    suffix = f"_{record_id}{extension}"
    budget = 120 - len(suffix.encode("utf-8"))
    if budget <= 0:
        cleaned = ""
    else:
        encoded = cleaned.encode("utf-8")[:budget]
        cleaned = encoded.decode("utf-8", errors="ignore")
    stem = f"{cleaned}_{record_id}" if cleaned else str(record_id)
    return f"{stem}{extension}"


def resolve_batchid_file(
    sync_config: SourceSyncConfig, batch_id: Any
) -> Path | None:
    """按文档 SQL 的 ``batchid`` 匹配 ``file_root`` 下的磁盘文件。

    文件名包含 batchid 即可（忽略大小写），兼容 ``xxx_{batchid}.pdf``
    这类带前缀/后缀的命名；文件名完全等于 batchid 的优先命中。
    """
    raw = str(batch_id or "").strip()
    root = sync_config.file_root.strip()
    if not raw or not root:
        return None
    base = Path(root)
    if not base.is_absolute():
        raise RuntimeError("source_sync.file_root 必须是绝对路径")

    target = raw.casefold()
    contains_hit: Path | None = None
    for entry in base.iterdir():
        if not entry.is_file():
            continue
        name = entry.name.casefold()
        if name == target or Path(name).stem == target:
            return entry
        if contains_hit is None and target in name:
            contains_hit = entry
    return contains_hit


def date_folder_segments(publish_date: str) -> list[str]:
    """日期目录段：``2026-10-23`` → ``["2026年", "10月", "23日"]``。"""
    year, month, day = publish_date.split("-")
    return [f"{year}年", f"{int(month)}月", f"{int(day)}日"]


def _iter_windows(
    start: datetime, end: datetime, days: int
) -> Iterable[tuple[datetime, datetime]]:
    """按 days 切分 [start, end) 窗口，避免一次拉取过大数据量。"""
    cursor = start
    while cursor < end:
        boundary = min(cursor + timedelta(days=days), end)
        yield cursor, boundary
        cursor = boundary


# ---------------------------------------------------------------------------
# 外部 SQL
# ---------------------------------------------------------------------------
def _connect_source(sync_config: SourceSyncConfig) -> Any:
    if pymysql is None:
        raise RuntimeError("未安装 pymysql，无法连接研报 SQL")

    mysql = sync_config.mysql
    host = mysql.host.strip()
    database = mysql.database.strip()
    if not host or not database:
        raise RuntimeError("研报 SQL 配置不完整：mysql.host 与 mysql.database 均为必填")

    return pymysql.connect(
        host=host,
        port=mysql.port,
        user=mysql.user,
        password=mysql.password.get_secret_value(),
        database=database,
        charset=mysql.charset,
        cursorclass=DictCursor,
        connect_timeout=10,
        read_timeout=60,
    )


def fetch_source_rows(
    sync_config: SourceSyncConfig, start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """执行固定查询，返回窗口内的源记录（同步阻塞，调用方放线程池）。

    未配置表名时使用默认表并自动建表；配置表名时直接查询、不建表。
    连接/查询失败只记录「研报 SQL」错误日志并抛出，不做连接参数兜底。
    """
    table, create_if_missing = resolve_table_name(sync_config)
    mysql = sync_config.mysql
    try:
        connection = _connect_source(sync_config)
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "研报 SQL 连接失败 | host=%s:%s database=%s err=%s",
            mysql.host,
            mysql.port,
            mysql.database,
            exc,
        )
        raise
    logger.info(
        "研报 SQL 连接成功 | host=%s:%s database=%s table=%s",
        mysql.host,
        mysql.port,
        mysql.database,
        table,
    )
    try:
        with connection.cursor() as cursor:
            if create_if_missing:
                cursor.execute(_CREATE_DEFAULT_TABLE_SQL)
            cursor.execute(
                build_source_query(table),
                {
                    "start": start.strftime("%Y-%m-%d %H:%M:%S"),
                    "end": end.strftime("%Y-%m-%d %H:%M:%S"),
                },
            )
            rows = [dict(row) for row in cursor.fetchall()]
        logger.info(
            "研报 SQL 查询成功 | table=%s window=%s~%s rows=%s",
            table,
            start.strftime("%Y-%m-%d"),
            end.strftime("%Y-%m-%d"),
            len(rows),
        )
        return rows
    except Exception as exc:  # noqa: BLE001
        logger.error("研报 SQL 查询失败 | table=%s err=%s", table, exc)
        raise
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# 同步引擎
# ---------------------------------------------------------------------------
class SourceSyncEngine:
    """单次同步执行器：解析、差集、滑动窗口上传、状态回收。"""

    def __init__(
        self,
        *,
        config: KnowledgeConfig,
        sync_config: SourceSyncConfig,
        repository: KnowledgeRepository,
        service: KnowledgeService,
        principal: KnowledgePrincipal,
    ):
        self.config = config
        self.sync_config = sync_config
        self.repository = repository
        self.service = service
        self.principal = principal
        self.source = SOURCE_SYNC_SOURCE

    # ---------------- 知识库与目录 ----------------

    async def resolve_base_id(self) -> str:
        """确定入库目标库：``base_id`` 优先；为空时按 ``base_name`` 查找/新建。

        名字在 ``knowledge_bases`` 内不唯一（同名多库时按名称会取最早创建的
        那个），需要精确指定时填 ``base_id``；新建库固定为团队公共库。
        """
        sync_config = self.sync_config
        if sync_config.base_id.strip():
            base = await run_in_threadpool(
                self.repository.get_base, sync_config.base_id.strip()
            )
            if base is None or str(base.get("status")) in {"deleted", "deleting"}:
                raise RuntimeError(
                    f"source_sync.base_id 不存在或已删除: {sync_config.base_id}"
                )
            return str(base["id"])

        base = await run_in_threadpool(
            self.repository.find_base_by_name, sync_config.base_name
        )
        if base is not None:
            return str(base["id"])
        created = await run_in_threadpool(
            self.repository.create_base,
            name=sync_config.base_name,
            description="由文档 SQL 同步自动创建",
            # 研报接入固定入公共库（team），不提供个人库配置
            space_type=KnowledgeBaseVisibility.TEAM.value,
            owner_user_id=self.principal.user_id,
            department_id=self.principal.department_id,
            embedding_model=self.config.embedding.model,
        )
        await run_in_threadpool(
            self.repository.update_base,
            created["id"],
            status=KnowledgeBaseStatus.ACTIVE.value,
        )
        logger.info("文档同步已创建知识库 | id=%s name=%s", created["id"], created["name"])
        return str(created["id"])

    async def ensure_date_folder(self, base_id: str, publish_date: str) -> str | None:
        """确保 ``YYYY年/M月/D日`` 三级目录存在，返回日目录 ID。"""
        year, month, day = date_folder_segments(publish_date)
        await self.service.ensure_folders(
            base_id=base_id,
            folder_id=None,
            paths=["/".join([year, month, day])],
            principal=self.principal,
        )
        # ensure_folders 只保证目录存在，这里再定位叶子目录 ID
        parent: str | None = None
        for segment in (year, month, day):
            folder = await run_in_threadpool(
                self.repository.find_folder_by_name, base_id, segment, parent
            )
            if folder is None:
                raise RuntimeError(
                    f"日期目录创建后未找到: {base_id}/{year}/{month}/{day}"
                )
            parent = str(folder["id"])
        return parent

    # ---------------- 单条入库 ----------------

    async def _upload_row(
        self, *, base_id: str, row: dict[str, Any]
    ) -> dict[str, Any] | None:
        columns = _SOURCE_COLUMNS
        raw_id = _row_value(row, columns["record_id"])
        if raw_id is None or str(raw_id).strip() == "":
            logger.warning("文档同步跳过：源记录缺少 id | row=%s", row)
            return None
        record_id = str(raw_id).strip()
        publish_date = parse_publish_date(
            _row_value(row, columns["publish_date"])
        )
        batch_id = str(_row_value(row, columns["batch_id"]) or "").strip()
        title = _row_value(row, columns["report_title"], "reportTitle")
        report_tag = str(
            _row_value(row, columns["report_tag"]) or ""
        ).strip()
        second_source = str(
            _row_value(row, columns["report_second_source"]) or ""
        ).strip()

        # 兜底：同一源记录已存在本地文档时不再新建，避免触发唯一约束冲突
        existing = await run_in_threadpool(
            self.repository.find_document_by_source_record,
            self.source,
            record_id,
            base_id=base_id,
            include_deleted=True,
        )
        if existing is not None:
            logger.info(
                "文档同步跳过：本地已有记录 | id=%s doc=%s status=%s",
                record_id,
                existing["id"],
                existing.get("status"),
            )
            return None

        item = await run_in_threadpool(
            self.repository.create_or_get_sync_item,
            source=self.source,
            source_record_id=record_id,
            publish_date=publish_date,
        )
        item_id = str(item["id"])
        attempts = int(item.get("attempts") or 0)

        if not publish_date:
            await run_in_threadpool(
                self.repository.update_sync_item,
                item_id,
                status="failed",
                attempts=attempts + 1,
                last_error="publishDate 无法解析",
            )
            logger.warning("文档同步失败：publishDate 无法解析 | id=%s", record_id)
            return None
        # 文件定位：只用文档 SQL 的 batchid 在 file_root 下匹配同名文件，
        # batchid 唯一，匹配不到即磁盘没有该文件，不做其它字段兜底。
        source_file = resolve_batchid_file(self.sync_config, batch_id)
        if source_file is None:
            reason = (
                "未配置 source_sync.file_root，无法按 batchid 匹配磁盘文件"
                if not self.sync_config.file_root.strip()
                else f"batchid 未匹配到磁盘文件: {batch_id or '-'}"
            )
            await run_in_threadpool(
                self.repository.update_sync_item,
                item_id,
                status="failed",
                attempts=attempts + 1,
                last_error=reason,
            )
            logger.warning(
                "文档同步失败：%s | id=%s batchid=%s", reason, record_id, batch_id
            )
            return None

        folder_id = await self.ensure_date_folder(base_id, publish_date)
        upload_name = build_upload_name(title, record_id, str(source_file))
        content_type = (
            mimetypes.guess_type(upload_name)[0] or "application/octet-stream"
        )
        metadata = DocumentMetadata(
            source=self.source,
            source_record_id=record_id,
            batch_id=batch_id,
            report_title=sanitize_title(title),
            report_tag=report_tag,
            report_second_source=second_source,
            publish_date=publish_date,
        )
        await run_in_threadpool(
            self.repository.update_sync_item, item_id, status="uploading"
        )
        try:
            with source_file.open("rb") as content:
                accepted = await self.service.upload_document(
                    base_id=base_id,
                    folder_id=folder_id,
                    filename=upload_name,
                    content_type=content_type,
                    size_bytes=source_file.stat().st_size,
                    content=content,
                    principal=self.principal,
                    request_id=f"source-sync:{record_id}",
                    metadata=metadata,
                )
        except Exception as exc:  # noqa: BLE001 — 单条失败不阻断整批
            document = await run_in_threadpool(
                self.repository.find_document_by_source_record,
                self.source,
                record_id,
                base_id=base_id,
                include_deleted=True,
            )
            await run_in_threadpool(
                self.repository.update_sync_item,
                item_id,
                document_id=str(document["id"]) if document else None,
                status="failed",
                attempts=attempts + 1,
                last_error=str(exc)[:1000],
            )
            logger.error(
                "文档同步失败 | id=%s file=%s err=%s", record_id, source_file, exc
            )
            return None

        document = accepted.document
        await run_in_threadpool(
            self.repository.update_sync_item,
            item_id,
            document_id=str(document.id),
            status="parsing",
            publish_date=publish_date,
            last_error=None,
        )
        logger.info(
            "文档同步已提交解析 | id=%s name=%s date=%s doc=%s",
            record_id,
            upload_name,
            publish_date,
            document.id,
        )
        return await run_in_threadpool(
            self.repository.get_sync_item, self.source, record_id
        )

    # ---------------- 在途回收 ----------------

    async def refresh_inflight(
        self, items: list[dict[str, Any]], *, base_id: str | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, int]]:
        stats = {"ready": 0, "retried": 0, "failed": 0}
        remaining: list[dict[str, Any]] = []
        for item in items:
            item_id = str(item["id"])
            record_id = str(item["source_record_id"])
            document_id = str(item.get("document_id") or "")
            document = (
                await run_in_threadpool(self.repository.get_document, document_id)
                if document_id
                else await run_in_threadpool(
                    self.repository.find_document_by_source_record,
                    self.source,
                    record_id,
                    base_id=base_id,
                    include_deleted=True,
                )
            )
            if document is not None and base_id and str(document.get("base_id")) != base_id:
                # 台账指向旧库（已删除）的历史文档：按“未入库”处理，重新入当前库
                document = None
            if document is None:
                # 上传过程中进程中断且没有落库：重新排队
                await run_in_threadpool(
                    self.repository.update_sync_item, item_id, status="failed"
                )
                stats["failed"] += 1
                continue
            status = str(document.get("status") or "")
            if status == DocumentStatus.DELETED.value:
                # 文档在当前库内已被删除：尊重删除动作，不再重传
                await run_in_threadpool(
                    self.repository.update_sync_item,
                    item_id,
                    status="deleted",
                    last_error="文档已被删除，不再重传",
                )
                stats["failed"] += 1
                continue
            if status == DocumentStatus.READY.value:
                await run_in_threadpool(
                    self.repository.update_sync_item,
                    item_id,
                    document_id=str(document["id"]),
                    status="ready",
                )
                stats["ready"] += 1
                continue
            if (
                status == DocumentStatus.PENDING.value
                and not str(document.get("remote_document_id") or "")
            ):
                # 上次上传在“已建本地记录、未拿到远端 ID”之间中断：标记失败等待重试
                await run_in_threadpool(
                    self.repository.update_sync_item,
                    item_id,
                    status="failed",
                    attempts=int(item.get("attempts") or 0) + 1,
                    last_error="上次上传未完成",
                )
                stats["failed"] += 1
                continue
            if status in _RETRYABLE_DOCUMENT_STATUSES:
                attempts = int(item.get("attempts") or 0) + 1
                if attempts <= self.sync_config.max_attempts:
                    try:
                        await self.service.retry_document(
                            document_id=str(document["id"]),
                            principal=self.principal,
                            request_id=f"source-sync-retry:{record_id}:{attempts}",
                        )
                        await run_in_threadpool(
                            self.repository.update_sync_item,
                            item_id,
                            status="parsing",
                            attempts=attempts,
                            last_error=str(document.get("error_message") or "")[:1000],
                        )
                        item["attempts"] = attempts
                        item["status"] = "parsing"
                        remaining.append(item)
                        stats["retried"] += 1
                    except Exception as exc:  # noqa: BLE001
                        await run_in_threadpool(
                            self.repository.update_sync_item,
                            item_id,
                            status="failed",
                            attempts=attempts,
                            last_error=str(exc)[:1000],
                        )
                        stats["failed"] += 1
                else:
                    await run_in_threadpool(
                        self.repository.update_sync_item,
                        item_id,
                        status="failed",
                        attempts=attempts,
                        last_error=str(document.get("error_message") or "超过重试次数")[:1000],
                    )
                    stats["failed"] += 1
                continue
            remaining.append(item)
        return remaining, stats

    # ---------------- 主循环 ----------------

    async def run(
        self,
        *,
        start_date: str | None = None,
        limit: int | None = None,
        dry_run: bool = False,
    ) -> dict[str, int]:
        stats = {
            "scanned": 0,
            "existing": 0,
            "uploaded": 0,
            "failed": 0,
            "retried": 0,
            "ready": 0,
            "planned": 0,
            "truncated": 0,
        }
        base_id = await self.resolve_base_id() if not dry_run else ""
        now = datetime.now(UTC)
        counts = await run_in_threadpool(
            self.repository.count_sync_items, source=self.source
        )
        window_start = self._window_start(now, start_date, first_run=not any(counts.values()))
        windows = list(_iter_windows(window_start, now, self.sync_config.lookback_days))

        inflight: list[dict[str, Any]] = []
        if not dry_run:
            # 断点续跑：恢复上次中断时仍在 uploading/parsing 的台账行
            inflight = await run_in_threadpool(
                self.repository.list_sync_items,
                source=self.source,
                statuses=list(_ACTIVE_ITEM_STATUSES),
                limit=self.sync_config.max_inflight,
            )
            if inflight:
                inflight, resumed_stats = await self.refresh_inflight(
                    inflight, base_id=base_id
                )
                for key, value in resumed_stats.items():
                    stats[key] += value
                logger.info("研报 SQL 同步恢复在途文档 | count=%s", len(inflight))
        for window_start_at, window_end_at in windows:
            rows = await run_in_threadpool(
                fetch_source_rows, self.sync_config, window_start_at, window_end_at
            )
            logger.info(
                "研报 SQL 同步窗口 | start=%s end=%s rows=%s",
                window_start_at,
                window_end_at,
                len(rows),
            )
            for row in rows:
                if limit is not None and stats["scanned"] >= limit:
                    stats["truncated"] = 1
                    break
                stats["scanned"] += 1
                record_id = str(
                    _row_value(row, _SOURCE_COLUMNS["record_id"]) or ""
                ).strip()
                # 在途达上限时先回收，保证重试与新上传都不突破并发上限
                while len(inflight) >= self.sync_config.max_inflight:
                    inflight, batch_stats = await self.refresh_inflight(
                        inflight, base_id=base_id
                    )
                    for key, value in batch_stats.items():
                        stats[key] += value
                    if len(inflight) >= self.sync_config.max_inflight:
                        await asyncio.sleep(self.sync_config.poll_interval_seconds)
                document = (
                    await run_in_threadpool(
                        self.repository.find_document_by_source_record,
                        self.source,
                        record_id,
                        base_id=base_id,
                        include_deleted=True,
                    )
                    if record_id
                    else None
                )
                if document is not None:
                    status = str(document.get("status") or "")
                    if status == DocumentStatus.DELETED.value:
                        # 当前库内已被删除：尊重删除动作，不再重新上传
                        logger.info(
                            "文档同步跳过：文档已删除 | id=%s doc=%s",
                            record_id,
                            document["id"],
                        )
                        stats["existing"] += 1
                        continue
                    remote_id = str(document.get("remote_document_id") or "")
                    if status == DocumentStatus.PENDING.value and not remote_id:
                        # 上传中断：转失败后走重试（复用本地原文，避免重复建记录）
                        document = await run_in_threadpool(
                            self.repository.update_document,
                            str(document["id"]),
                            status=DocumentStatus.FAILED.value,
                            error_code="SYNC_UPLOAD_INTERRUPTED",
                            error_message="上次上传未完成，重新提交",
                        )
                        status = DocumentStatus.FAILED.value
                    elif status in _READY_DOCUMENT_STATUSES:
                        stats["existing"] += 1
                        continue
                    if status in _RETRYABLE_DOCUMENT_STATUSES:
                        publish_date = parse_publish_date(
                            _row_value(row, _SOURCE_COLUMNS["publish_date"])
                        )
                        retry_item = await run_in_threadpool(
                            self.repository.create_or_get_sync_item,
                            source=self.source,
                            source_record_id=record_id,
                            publish_date=publish_date,
                        )
                        retry_item_id = str(retry_item["id"])
                        attempts = int(retry_item.get("attempts") or 0) + 1
                        if attempts > self.sync_config.max_attempts:
                            await run_in_threadpool(
                                self.repository.update_sync_item,
                                retry_item_id,
                                status="failed",
                                attempts=attempts,
                                last_error="超过最大重试次数",
                            )
                            stats["failed"] += 1
                            continue
                        try:
                            await self.service.retry_document(
                                document_id=str(document["id"]),
                                principal=self.principal,
                                request_id=f"source-sync-retry:{record_id}:{attempts}",
                            )
                        except Exception as exc:  # noqa: BLE001
                            await run_in_threadpool(
                                self.repository.update_sync_item,
                                retry_item_id,
                                document_id=str(document["id"]),
                                status="failed",
                                attempts=attempts,
                                last_error=str(exc)[:1000],
                            )
                            stats["failed"] += 1
                            logger.error("研报文档重试失败 | id=%s err=%s", record_id, exc)
                            continue
                        await run_in_threadpool(
                            self.repository.update_sync_item,
                            retry_item_id,
                            document_id=str(document["id"]),
                            status="parsing",
                            attempts=attempts,
                        )
                        refreshed = await run_in_threadpool(
                            self.repository.get_sync_item, self.source, record_id
                        )
                        if refreshed is not None:
                            inflight.append(refreshed)
                        stats["retried"] += 1
                        continue
                if dry_run:
                    stats["planned"] += 1
                    logger.info(
                        "[dry-run] 待入库 | id=%s date=%s batchid=%s name=%s",
                        record_id,
                        parse_publish_date(
                            _row_value(row, _SOURCE_COLUMNS["publish_date"])
                        ),
                        _row_value(row, _SOURCE_COLUMNS["batch_id"]),
                        build_upload_name(
                            _row_value(row, _SOURCE_COLUMNS["report_title"]),
                            record_id,
                            str(_row_value(row, _SOURCE_COLUMNS["batch_id"]) or ""),
                        ),
                    )
                    continue
                item = await self._upload_row(base_id=base_id, row=row)
                if item is not None:
                    if str(item.get("status")) == "ready":
                        stats["ready"] += 1
                    else:
                        inflight.append(item)
                        stats["uploaded"] += 1
                else:
                    stats["failed"] += 1
            if limit is not None and stats["scanned"] >= limit:
                break

        while inflight and not dry_run:
            inflight, batch_stats = await self.refresh_inflight(
                inflight, base_id=base_id
            )
            for key, value in batch_stats.items():
                stats[key] += value
            if inflight:
                await asyncio.sleep(self.sync_config.poll_interval_seconds)

        logger.info(
            "文档同步完成 | " + " ".join(f"{key}={value}" for key, value in stats.items())
        )
        return stats

    def _window_start(
        self, now: datetime, start_date: str | None, *, first_run: bool
    ) -> datetime:
        configured = (start_date or self.sync_config.start_date or "").strip()
        if configured and first_run:
            # 首次回填：从配置的起始日期开始按窗口切分，避免一次拉全量
            return datetime.strptime(configured, "%Y-%m-%d").replace(tzinfo=UTC)
        return now - timedelta(days=self.sync_config.lookback_days)


# ---------------------------------------------------------------------------
# 生命周期入口
# ---------------------------------------------------------------------------
async def run_source_sync(
    app,
    *,
    start_date: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """执行一次文档同步（启动钩子 / CLI / 测试共用）。"""
    config: KnowledgeConfig | None = getattr(app.state, "knowledge_config", None)
    if config is None:
        raise RuntimeError("知识库配置未加载，无法执行文档同步")
    sync_config = config.source_sync
    if not sync_config.enabled:
        logger.info("研报 SQL 同步未启用（knowledge.source_sync.enabled=false），跳过")
        return {}
    if not config.enabled:
        raise RuntimeError("知识库模块未启用，无法执行文档同步")
    db = getattr(app.state, "db", None)
    ragflow = getattr(app.state, "ragflow_client", None)
    if db is None or ragflow is None:
        raise RuntimeError("数据库或 RAGFlow 客户端未就绪，无法执行文档同步")

    owner = await run_in_threadpool(db.get_user_by_username, sync_config.owner_username)
    if owner is None:
        raise RuntimeError(
            f"同步归属用户不存在: {sync_config.owner_username!r}"
            "（检查 source_sync.owner_username）"
        )
    principal = KnowledgePrincipal(
        user_id=str(owner.user_id),
        username=str(owner.username),
        department_id=(str(owner.department_id) or None) if owner.department_id else None,
    )
    original_store = getattr(app.state, "knowledge_original_store", None)
    repository = KnowledgeRepository(db)
    service = KnowledgeService(
        repository,
        ragflow,
        config,
        original_store=original_store or LocalOriginalStore(),
    )
    engine = SourceSyncEngine(
        config=config,
        sync_config=sync_config,
        repository=repository,
        service=service,
        principal=principal,
    )
    async with _RUN_LOCK:
        return await engine.run(start_date=start_date, limit=limit, dry_run=dry_run)


async def startup_source_sync(app) -> None:
    """启动钩子：开关开启时调度后台同步任务（失败不阻断启动）。"""
    config = getattr(app.state, "knowledge_config", None)
    sync_config = getattr(config, "source_sync", None) if config is not None else None
    if sync_config is None or not sync_config.enabled:
        return
    if not sync_config.mysql.host.strip() or not sync_config.mysql.database.strip():
        logger.error(
            "研报 SQL 配置不完整：mysql.host 与 mysql.database 均为必填，跳过同步"
        )
        return
    if not sync_config.file_root.strip():
        logger.error(
            "研报 SQL 配置不完整：source_sync.file_root 必填（按 batchid 匹配磁盘文件），跳过同步"
        )
        return
    task = asyncio.create_task(_sync_loop(app), name="source-doc-sync")
    app.state.source_sync_task = task
    logger.info("研报 SQL 同步任务已调度（后台执行）")


async def _sync_loop(app) -> None:
    try:
        while True:
            try:
                await run_source_sync(app)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.error("研报 SQL 同步失败: %s", exc)
            interval = int(getattr(app.state.knowledge_config.source_sync, "interval_minutes", 0))
            if interval <= 0:
                break
            await asyncio.sleep(interval * 60)
    except asyncio.CancelledError:
        raise


async def shutdown_source_sync(app) -> None:
    task = getattr(app.state, "source_sync_task", None)
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass


__all__ = [
    "SourceSyncEngine",
    "build_source_query",
    "build_upload_name",
    "fetch_source_rows",
    "parse_publish_date",
    "resolve_batchid_file",
    "resolve_table_name",
    "run_source_sync",
    "sanitize_title",
    "shutdown_source_sync",
    "startup_source_sync",
]
