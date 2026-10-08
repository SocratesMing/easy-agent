"""外部目录树同步（ai_kb → 知识库板块）。

流程：将 SQL dump 文件（含 kb_catalog / kb_doc 表结构与数据）执行到外部
MySQL 库（默认 ai_kb，不存在时自动创建），读取 kb_catalog 目录树，再在
知识库板块幂等地创建知识库与文件夹，目录层级与表结构一致
（库 → 年 → 年月 → 年月日 → 机构 …）：

- level=1 根节点（parent_id<=0）→ 知识库（本地 knowledge_bases，走共享数据集）
- 更深层级 → knowledge_folders 文件夹（按 parent_id 链挂接，最大 max_level 层）

由 ``knowledge.catalog_sync.enabled`` 总开关控制；关闭时启动流程零副作用。
重复执行幂等：按名称查找已存在的知识库 / (父节点, 名称) 查找已存在的文件夹。
"""

from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from typing import Any

from starlette.concurrency import run_in_threadpool

try:
    import pymysql
except ImportError:  # pragma: no cover - 环境缺依赖时由运行期报错兜底
    pymysql = None  # type: ignore[assignment]

from ..db.database import Database
from .config import CatalogSyncConfig, KnowledgeConfig
from .models import KnowledgeBaseStatus
from .repository import KnowledgeRepository

logger = logging.getLogger(__name__)

_BASE_DESCRIPTION = "由外部目录同步（ai_kb 目录树）自动创建"

_DB_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

# 本地表列宽（knowledge_bases.name / knowledge_folders.name 均为 VARCHAR(127)），
# kb_catalog.name 为 VARCHAR(128)，超长时截断保证可写入
_MAX_LOCAL_NAME = 127


class CatalogSyncError(RuntimeError):
    """目录同步配置或执行错误。"""


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _fit_name(name: str) -> str:
    return name[:_MAX_LOCAL_NAME]


# ---------------------------------------------------------------------------
# SQL dump 解析与执行
# ---------------------------------------------------------------------------
def split_sql_statements(sql_text: str) -> list[str]:
    """按分号分割 SQL 文本为独立语句。

    跟踪单/双引号字符串（处理 ``\\`` 转义与 ``''`` 双写）、反引号标识符
    （处理 ```` `` ```` 双写）与 ``--`` / ``#`` / ``/* */`` 注释，仅在安全
    位置分割；空白语句被丢弃。
    """
    statements: list[str] = []
    buffer: list[str] = []
    text = sql_text
    length = len(text)
    index = 0
    while index < length:
        char = text[index]
        if char == "-" and text.startswith("--", index):
            end = text.find("\n", index)
            if end == -1:
                break
            index = end + 1
            continue
        if char == "#":
            end = text.find("\n", index)
            if end == -1:
                break
            index = end + 1
            continue
        if char == "/" and text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue
        if char in ("'", '"', "`"):
            quote = char
            buffer.append(char)
            index += 1
            while index < length:
                current = text[index]
                buffer.append(current)
                if current == "\\" and quote != "`" and index + 1 < length:
                    buffer.append(text[index + 1])
                    index += 2
                    continue
                if current == quote:
                    if index + 1 < length and text[index + 1] == quote:
                        buffer.append(text[index + 1])
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            continue
        if char == ";":
            statement = "".join(buffer).strip()
            if statement:
                statements.append(statement)
            buffer = []
            index += 1
            continue
        buffer.append(char)
        index += 1
    tail = "".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


def _resolve_mysql_params(sync_config: CatalogSyncConfig, db: Database) -> dict[str, Any]:
    """确定外部 MySQL 连接参数：优先 catalog_sync.mysql 覆盖，其次复用主库连接。"""
    override = sync_config.mysql
    if override.host.strip():
        if not override.user.strip():
            raise CatalogSyncError(
                "catalog_sync.mysql.host 已配置但 user 为空，必须提供完整连接信息"
            )
        return {
            "host": override.host.strip(),
            "port": int(override.port),
            "user": override.user.strip(),
            "password": override.password.get_secret_value(),
            "charset": override.charset,
        }
    main_config = getattr(db, "_mysql_config", None)
    if getattr(db, "db_type", None) == "mysql" and main_config:
        return {
            "host": main_config.get("host", "localhost"),
            "port": int(main_config.get("port", 3306)),
            "user": main_config.get("user", "root"),
            "password": main_config.get("password", ""),
            "charset": main_config.get("charset", "utf8mb4"),
        }
    raise CatalogSyncError(
        "主数据库为 SQLite 且未配置 catalog_sync.mysql 连接信息，无法执行 SQL dump"
    )


def _connect_external_mysql(sync_config: CatalogSyncConfig, db: Database):
    """连接外部 MySQL 服务器并选中（必要时创建）目标库。"""
    if pymysql is None:
        raise CatalogSyncError("未安装 pymysql，无法连接外部 MySQL")
    database = sync_config.database.strip()
    if not _DB_NAME_RE.fullmatch(database):
        raise CatalogSyncError(f"catalog_sync.database 含非法字符: {database!r}")
    params = _resolve_mysql_params(sync_config, db)
    conn = pymysql.connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        charset=params["charset"],
        connect_timeout=10,
        cursorclass=pymysql.cursors.DictCursor,
    )
    try:
        with conn.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.select_db(database)
    except Exception:
        conn.close()
        raise
    return conn


def _execute_dump(conn, sql_path: Path) -> None:
    """将 dump 文件按语句逐条执行（含 DROP/CREATE/INSERT，直接覆盖同名表）。"""
    sql_text = sql_path.read_text(encoding="utf-8")
    statements = split_sql_statements(sql_text)
    executed = 0
    with conn.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)
            executed += 1
    conn.commit()
    logger.info(f"SQL dump 执行完成 | 文件: {sql_path} | 语句数: {executed}")


def _fetch_catalog_rows(conn) -> list[dict[str, Any]]:
    """读取 kb_catalog 全量目录行（status 过滤在同步引擎内进行）。"""
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT id, parent_id, name, full_path, level, library, sort, status "
            "FROM kb_catalog ORDER BY level, sort, id"
        )
        rows = list(cursor.fetchall())
    conn.commit()
    return [dict(row) for row in rows]


def _execute_dump_and_fetch(
    sync_config: CatalogSyncConfig, db: Database, sql_path: Path
) -> list[dict[str, Any]]:
    conn = _connect_external_mysql(sync_config, db)
    try:
        _execute_dump(conn, sql_path)
        return _fetch_catalog_rows(conn)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 同步引擎
# ---------------------------------------------------------------------------
class CatalogSyncer:
    """按 kb_catalog 目录树幂等创建知识库与文件夹。"""

    def __init__(
        self,
        *,
        db: Database,
        repository: KnowledgeRepository,
        config: KnowledgeConfig,
        sync_config: CatalogSyncConfig,
        owner_user_id: str,
    ) -> None:
        self.db = db
        self.repository = repository
        self.config = config
        self.sync_config = sync_config
        self.owner_user_id = owner_user_id
        self.max_level = int(sync_config.max_level)
        self.department_id = sync_config.department_id.strip() or None
        # base_id -> {(parent_id or '', name) -> folder_id}
        self._folder_cache: dict[str, dict[tuple[str, str], str]] = {}

    # ---------------- 知识库 ----------------

    def _find_base_by_name(self, name: str) -> dict[str, Any] | None:
        with self.db.get_connection() as conn:
            cursor = conn.cursor()
            self.db._execute(
                cursor,
                "SELECT * FROM knowledge_bases "
                "WHERE name=? AND status NOT IN ('deleted', 'deleting') "
                "ORDER BY created_at LIMIT 1",
                (name,),
            )
            row = cursor.fetchone()
            return dict(row) if row is not None else None

    async def _find_or_create_base(self, name: str) -> tuple[dict[str, Any], bool]:
        name = _fit_name(name)
        existing = await run_in_threadpool(self._find_base_by_name, name)
        if existing is not None:
            return existing, False
        base = await run_in_threadpool(
            self.repository.create_base,
            name=name,
            description=_BASE_DESCRIPTION,
            space_type=self.sync_config.space_type,
            owner_user_id=self.owner_user_id,
            department_id=self.department_id,
            embedding_model=self.config.embedding.model,
        )
        # 纯本地创建（同建文件夹）：不建 Ragflow 数据集，走共享数据集
        base = await run_in_threadpool(
            self.repository.update_base,
            base["id"],
            status=KnowledgeBaseStatus.ACTIVE.value,
        )
        return base, True

    # ---------------- 文件夹 ----------------

    async def _find_or_create_folder(
        self, base_id: str, name: str, parent_id: str | None, sort_order: int
    ) -> tuple[str, bool]:
        cache = self._folder_cache.get(base_id)
        if cache is None:
            folders = await run_in_threadpool(self.repository.list_folders, base_id)
            cache = {
                (str(folder.get("parent_id") or ""), str(folder["name"])): str(folder["id"])
                for folder in folders
            }
            self._folder_cache[base_id] = cache
        key = (parent_id or "", _fit_name(name))
        if key in cache:
            return cache[key], False
        try:
            row = await run_in_threadpool(
                self.repository.create_folder,
                base_id=base_id,
                name=key[1],
                parent_id=parent_id,
                sort_order=sort_order,
            )
        except Exception as exc:
            if "UNIQUE" in str(exc).upper() or "DUPLICATE" in str(exc).upper():
                existing = await run_in_threadpool(
                    self.repository.find_folder_by_name, base_id, key[1], parent_id
                )
                if existing is not None:
                    cache[key] = str(existing["id"])
                    return str(existing["id"]), False
            raise
        folder_id = str(row["id"])
        cache[key] = folder_id
        return folder_id, True

    # ---------------- 主流程 ----------------

    async def sync(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        stats = {
            "bases_created": 0,
            "bases_existing": 0,
            "folders_created": 0,
            "folders_existing": 0,
            "skipped": 0,
            "failed_bases": 0,
            "failed_folders": 0,
        }
        by_id: dict[int, dict[str, Any]] = {}
        active_rows = [
            row
            for row in rows
            if str(row.get("status") or "active").lower() == "active"
            and _as_int(row.get("id"), -1) >= 0
        ]
        for row in active_rows:
            by_id[_as_int(row.get("id"))] = row
        base_by_root_id: dict[int, dict[str, Any]] = {}
        folder_by_catalog_id: dict[int, str] = {}

        def _root_base(row: dict[str, Any]) -> dict[str, Any] | None:
            current = row
            seen: set[int] = set()
            while _as_int(current.get("parent_id"), 0) > 0:
                parent_id = _as_int(current.get("parent_id"))
                if parent_id in seen or parent_id not in by_id:
                    return None
                seen.add(parent_id)
                current = by_id[parent_id]
            return base_by_root_id.get(_as_int(current.get("id"), -1))

        ordered = sorted(
            active_rows,
            key=lambda row: (
                _as_int(row.get("level"), 1),
                _as_int(row.get("sort")),
                _as_int(row.get("id")),
            ),
        )
        for row in ordered:
            row_id = _as_int(row.get("id"), -1)
            parent_id = _as_int(row.get("parent_id"), 0)
            name = str(row.get("name") or "").strip()
            if row_id < 0 or not name:
                stats["skipped"] += 1
                continue
            if parent_id <= 0:
                # 根节点 → 知识库
                try:
                    base, created = await self._find_or_create_base(name)
                except Exception as exc:  # noqa: BLE001
                    logger.error(f"目录同步创建知识库失败（{name}）: {exc}")
                    stats["failed_bases"] += 1
                    continue
                base_by_root_id[row_id] = base
                stats["bases_created" if created else "bases_existing"] += 1
                continue
            if _as_int(row.get("level"), 1) > self.max_level:
                stats["skipped"] += 1
                continue
            parent_row = by_id.get(parent_id)
            if parent_row is None:
                stats["skipped"] += 1
                continue
            if _as_int(parent_row.get("parent_id"), 0) > 0:
                # 父节点是文件夹：必须已同步
                if parent_id not in folder_by_catalog_id:
                    stats["skipped"] += 1
                    continue
                parent_folder_id: str | None = folder_by_catalog_id[parent_id]
            else:
                parent_folder_id = None
            base = _root_base(row)
            if base is None:
                stats["skipped"] += 1
                continue
            try:
                folder_id, created = await self._find_or_create_folder(
                    str(base["id"]), name, parent_folder_id, _as_int(row.get("sort"))
                )
            except Exception as exc:  # noqa: BLE001
                logger.error(f"目录同步创建文件夹失败（{name}）: {exc}")
                stats["failed_folders"] += 1
                continue
            folder_by_catalog_id[row_id] = folder_id
            stats["folders_created" if created else "folders_existing"] += 1
        return stats


# ---------------------------------------------------------------------------
# 生命周期与入口
# ---------------------------------------------------------------------------
async def run_catalog_sync(app) -> dict[str, int]:
    """执行一次目录同步（供启动钩子与测试调用）。"""
    config: KnowledgeConfig | None = getattr(app.state, "knowledge_config", None)
    if config is None:
        raise CatalogSyncError("知识库配置未加载，无法执行目录同步")
    sync_config = config.catalog_sync
    db: Database | None = getattr(app.state, "db", None)
    if not config.enabled:
        raise CatalogSyncError("知识库模块未启用，无法执行目录同步")
    if db is None:
        raise CatalogSyncError("主数据库未初始化，无法执行目录同步")
    if not sync_config.sql_file.strip():
        raise CatalogSyncError("catalog_sync.enabled=true 但未配置 sql_file")
    sql_path = Path(sync_config.sql_file.strip())
    if not sql_path.is_absolute():
        sql_path = Path.cwd() / sql_path
    if not sql_path.is_file():
        raise CatalogSyncError(f"SQL 文件不存在: {sql_path}")
    owner = await run_in_threadpool(db.get_user_by_username, sync_config.owner_username)
    if owner is None:
        raise CatalogSyncError(
            f"同步归属用户不存在: {sync_config.owner_username!r}"
            "（检查 catalog_sync.owner_username）"
        )
    rows = await run_in_threadpool(_execute_dump_and_fetch, sync_config, db, sql_path)
    logger.info(f"kb_catalog 读取完成 | 行数: {len(rows)}")
    syncer = CatalogSyncer(
        db=db,
        repository=KnowledgeRepository(db),
        config=config,
        sync_config=sync_config,
        owner_user_id=str(owner.user_id),
    )
    stats = await syncer.sync(rows)
    logger.info("目录同步完成 | " + " ".join(f"{key}={value}" for key, value in stats.items()))
    return stats


async def startup_catalog_sync(app) -> None:
    """启动钩子：开关开启时以后台任务执行一次目录同步（失败不阻断启动）。"""
    config = getattr(app.state, "knowledge_config", None)
    sync_config = getattr(config, "catalog_sync", None) if config is not None else None
    if sync_config is None or not sync_config.enabled:
        return
    if not config.enabled:
        logger.warning("catalog_sync.enabled=true 但 knowledge.enabled=false，跳过外部目录同步")
        return
    task = asyncio.create_task(_run_startup_sync(app), name="catalog-sync")
    app.state.catalog_sync_task = task
    logger.info("外部目录同步任务已调度（后台执行）")


async def _run_startup_sync(app) -> None:
    try:
        await run_catalog_sync(app)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.error(f"外部目录同步失败: {exc}")


async def shutdown_catalog_sync(app) -> None:
    """关闭钩子：等待/放弃仍在执行的后台同步任务。"""
    task = getattr(app.state, "catalog_sync_task", None)
    app.state.catalog_sync_task = None
    if task is None or task.done():
        return
    task.cancel()
    _, pending = await asyncio.wait({task}, timeout=5)
    if pending:
        logger.warning("外部目录同步任务仍在执行，已放弃等待（下次启动幂等续跑）")


__all__ = [
    "CatalogSyncError",
    "CatalogSyncer",
    "run_catalog_sync",
    "shutdown_catalog_sync",
    "split_sql_statements",
    "startup_catalog_sync",
]
