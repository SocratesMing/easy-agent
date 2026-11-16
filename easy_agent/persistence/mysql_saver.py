"""自研 ``AsyncMySQLSaver``：LangGraph 官方 checkpoint 协议在 MySQL 8 上的实现。

为什么自己写而不是装社区包：官方只有 sqlite / postgres 两个实现，社区
``langgraph-checkpoint-mysql`` 是单人维护、不在官方集成清单里，且缺连接池 / 密码
URL 编码 / 断连重试 / 表名前缀四个生产必需能力（见
``docs/design/2026-11-11-self-hosted-mysql-saver-plan.md`` §3）。

结构照抄官方 postgres（**不是** sqlite）：三张业务表 + 一张元数据表。

- ``{p}checkpoints``：一行 = 某 thread 在某 ``checkpoint_ns`` 下的一个版本（快照头），
  只内联 JSON 安全的标量，大对象在 blobs；
- ``{p}checkpoint_blobs``：按 ``(thread, ns, channel, version)`` 去重，多个 checkpoint
  共享同一份 channel 值，避免「每步重存整个 messages 数组」的写放大；
- ``{p}checkpoint_writes``：任务级中间结果（``__interrupt__`` / 节点输出 / 错误），
  HITL 审批挂在这里等人点；
- ``{p}checkpoint_meta``：KV，存 DDL 版本号与 ``ns_hash`` 算法标识。

三张表放不进一张表（§4.6）：它们的写粒度是三种正交的 key —— checkpoint 版本号 /
channel 版本号 / 任务执行序号。合并只会把写放大、读放大、宽表三个问题请回来。

MySQL 特有的两处设计（都踩过）：

1. **``checkpoint_ns`` 不进主键**。它是子图嵌套路径，长度不可控，而 InnoDB 索引键上限
   3072 字节、超长是插入报错而不是截断。改用 ``sha256(ns)[:16]`` 存 ``BINARY(16)`` 参与
   主键，原值存 ``MEDIUMTEXT`` 并作为额外过滤条件 —— 真碰撞也不会读错。
2. **``thread_id`` 反解不出 ``session_id``**（两种形态 ``<uuid>`` 与 ``<uuid>-<message_id>``，
   UUID 自带 ``-``，没有可靠分隔符）。所以多一列 ``session_id`` 冗余列，写入时用
   ``session_id_of_thread()`` 算好，GC/TTL 用一条 JOIN 就能选出该删哪些 thread。

算法约束：``checkpoint_ns_hash`` 的计算方式**上线后不得修改**（改了历史 ns 全部对不上，
``aget_tuple`` 静默返回空历史且不报错）。``cp_checkpoint_meta.ns_hash_algo`` 是这条约束的
哨兵：``asetup`` 启动时比对，不一致直接启动失败。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    DeltaChannelHistory,
    PendingWrite,
    get_checkpoint_id,
    get_checkpoint_metadata,
)

try:  # langgraph-checkpoint ≥ 4.2 才导出；本项目 conda 环境仍是 4.1.x
    from langgraph.checkpoint.base import writes_sort_key
except ImportError:  # pragma: no cover - 老版本兜底，语义与新版逐字一致

    def writes_sort_key(
        task_path: str, task_id: str = "", idx: int = 0
    ) -> tuple[str, str, int]:
        """同一 super-step 内 writes 的排序键：实时执行就是按这个顺序 apply 的。"""
        return (task_path, task_id, idx)

from easy_agent.persistence.thread_ids import session_id_of_thread

logger = logging.getLogger(__name__)


# ── 常量 ────────────────────────────────────────────────────────────
#: ``checkpoint_ns`` 哈希算法标识。**改动它等于让历史 checkpoint 全部失联**，
#: 见模块 docstring 与 ``asetup`` 的比对逻辑。
NS_HASH_ALGO = "sha256-16"
#: checkpoint 表结构版本号（与业务 ``SCHEMA_VERSION`` 无关，各自演进）。
SCHEMA_VERSION = 1
#: 一次 ``adelete_thread`` 每批删多少行，避免删长会话时产生几百 MB 的长事务。
_DELETE_BATCH_WRITES = 2000
_DELETE_BATCH_BLOBS = 1000
_DELETE_BATCH_CHECKPOINTS = 200
#: delta 祖先回溯每批最多走多少层（超过就再来一批，不触 ``cte_max_recursion_depth``）。
_DELTA_WALK_BATCH = 64
#: 会话级 ``cte_max_recursion_depth``：递归只在本模块显式带 depth 上限时使用。
_CTE_MAX_DEPTH = 10000
#: mysqld 最低版本：``JSON`` 列与递归 CTE 都要求 8.0；8.0.19 是项目实际在跑的下限。
_MIN_SERVER_VERSION = (8, 0, 19)

_META_TABLE = """CREATE TABLE IF NOT EXISTS {p}checkpoint_meta (
  meta_key   VARCHAR(64)  NOT NULL COMMENT '元数据键：schema_version / ns_hash_algo / ddl_applied_at / last_gc_at',
  meta_value VARCHAR(255) NOT NULL COMMENT '元数据值；schema_version 为递增整数串，ns_hash_algo 形如 sha256-16',
  updated_at DATETIME(6)  NOT NULL COMMENT '最后更新时间（UTC，应用显式写入；不用 DEFAULT CURRENT_TIMESTAMP）',
  PRIMARY KEY (meta_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ROW_FORMAT=DYNAMIC
  COMMENT='checkpoint 存储自身的元数据（KV）：DDL 版本号、checkpoint_ns 哈希算法标识。业务代码不读它，仅供 asetup 与运维排查'"""

_CHECKPOINTS_TABLE = """CREATE TABLE IF NOT EXISTS {p}checkpoints (
  thread_id            VARCHAR(128) NOT NULL COMMENT '线程 id：<session_id>（会话级）或 <session_id>-<message_id>（单条消息级），定时任务以 sched- 开头',
  session_id           VARCHAR(64)  NOT NULL COMMENT '会话 id 冗余列，写入时由 session_id_of_thread() 解析；供 GC/TTL 直接 JOIN，避免全表拉回应用层解析',
  checkpoint_ns_hash   BINARY(16)   NOT NULL COMMENT 'checkpoint_ns 的 sha256 前 16 字节，仅用于索引定位（ns 由运行时决定、长度不可控，原值无法进主键）',
  checkpoint_ns        MEDIUMTEXT   NOT NULL COMMENT '子图命名空间原值，形如 tools:<uuid>|model:<uuid>|...；与 hash 同时作为查询过滤条件，保证 hash 碰撞时也不读错',
  checkpoint_id        VARCHAR(64)  NOT NULL COMMENT 'checkpoint 版本号，UUIDv6（36 字符），字典序≈写入时序，可直接倒序取最新',
  parent_checkpoint_id VARCHAR(64)  NULL     COMMENT '父 checkpoint id；首条为 NULL。delta 回溯与 alist 链式读取沿它上溯，不要用 checkpoint_id 排序代替',
  step                 INT          NULL     COMMENT '该 checkpoint 对应的图执行步号，用于排查；取自 metadata.step',
  channel_versions     JSON         NOT NULL COMMENT '各 channel 的版本号快照（内联自 checkpoint JSON，避免每次读都解析大 JSON）',
  type                 VARCHAR(64)  NULL     COMMENT 'checkpoint 序列化类型标识；本项目固定写 json',
  checkpoint           JSON         NOT NULL COMMENT 'checkpoint 主体（channel_values 仅保留 JSON 标量，大值在 checkpoint_blobs 表）',
  metadata             JSON         NOT NULL COMMENT '来源、步号、写入方等元数据（含 langgraph 的 source/steps 等字段）',
  created_at           DATETIME(6)  NOT NULL COMMENT '写入时间（UTC，应用显式写入）。不设 DEFAULT CURRENT_TIMESTAMP：该函数取服务器时区，跨时区 Pod 会混入两个时区的时间',
  PRIMARY KEY (thread_id, checkpoint_ns_hash, checkpoint_id),
  KEY idx_thread_ns_created (thread_id, checkpoint_ns_hash, created_at),
  KEY idx_thread_created (thread_id, created_at),
  KEY idx_session (session_id),
  KEY idx_parent (thread_id, checkpoint_ns_hash, parent_checkpoint_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ROW_FORMAT=DYNAMIC
  COMMENT='LangGraph checkpoint 主体：一行 = 某 thread 在某命名空间下的一个版本（即"快照头"）。大对象在 checkpoint_blobs，任务级中间结果在 checkpoint_writes'"""

_BLOBS_TABLE = """CREATE TABLE IF NOT EXISTS {p}checkpoint_blobs (
  thread_id          VARCHAR(128) NOT NULL COMMENT '线程 id，与 checkpoints.thread_id 同源',
  checkpoint_ns_hash BINARY(16)   NOT NULL COMMENT '命名空间哈希，与 checkpoints.checkpoint_ns_hash 一致',
  channel            VARCHAR(128) NOT NULL COMMENT 'channel 名，如 messages / context_compactions / 各节点输出',
  version            VARCHAR(64)  NOT NULL COMMENT '该 channel 的版本号，取自 checkpoints.channel_versions 对应值',
  type               VARCHAR(32)  NOT NULL COMMENT '值的序列化类型：msgpack（正常值）/ empty（该版本下无值）',
  payload            LONGBLOB     NULL     COMMENT '序列化后的 channel 值；type=empty 时为 NULL。列名不能叫 blob（MySQL 保留字）',
  PRIMARY KEY (thread_id, checkpoint_ns_hash, channel, version),
  KEY idx_thread_ns (thread_id, checkpoint_ns_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ROW_FORMAT=DYNAMIC
  COMMENT='channel 值（大对象）：按 (thread, ns, channel, version) 去重，多个 checkpoint 共享同一份，避免"每步重存整个 messages 数组"的写放大'"""

_WRITES_TABLE = """CREATE TABLE IF NOT EXISTS {p}checkpoint_writes (
  thread_id          VARCHAR(128) NOT NULL COMMENT '线程 id，与 checkpoints.thread_id 同源',
  checkpoint_ns_hash BINARY(16)   NOT NULL COMMENT '命名空间哈希，与 checkpoints.checkpoint_ns_hash 一致',
  checkpoint_id      VARCHAR(64)  NOT NULL COMMENT '产生该次写入的 checkpoint id；interrupt 时可能先于该 checkpoint 的 aput 落库',
  task_id            VARCHAR(64)  NOT NULL COMMENT '产生写入的任务（节点）id',
  idx                INT          NOT NULL COMMENT '同一 (checkpoint, task) 内多个 channel 写入的序号；特殊通道（__interrupt__ 等）用固定负值，见 WRITES_IDX_MAP',
  channel            VARCHAR(128) NOT NULL COMMENT 'channel 名；HITL 审批时值为 __interrupt__',
  task_path          TEXT         NOT NULL COMMENT '任务层级路径，与 checkpoint_ns 同源、长度不可控故用 TEXT；TEXT 无字面量默认值，INSERT 必须显式传（空串也要传）',
  type               VARCHAR(32)  NOT NULL COMMENT '值的序列化类型',
  payload            LONGBLOB     NOT NULL COMMENT '写入值。与 blobs 的区别：这是任务级中间结果，HITL 会长期挂在这里等人审批，不会被后续 aput 覆盖',
  PRIMARY KEY (thread_id, checkpoint_ns_hash, checkpoint_id, task_id, idx),
  KEY idx_thread_ns_cp (thread_id, checkpoint_ns_hash, checkpoint_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin ROW_FORMAT=DYNAMIC
  COMMENT='任务级中间写入：interrupt / error / 节点输出。HITL 审批挂起与恢复强依赖本表，禁止单独清理（整 thread 一起删）'"""

#: 迁移清单：只允许「加」——加表、加列、加索引。**禁止** ``DROP COLUMN`` / 缩小列宽。
#: ``auto_setup`` 会在每个 Pod 的每次启动上跑，任何非幂等语句都会放大成事故。
MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = (
    (1, (_META_TABLE, _CHECKPOINTS_TABLE, _BLOBS_TABLE, _WRITES_TABLE)),
)

#: ``scripts/db/mysql-checkpoint-ddl.sql`` 是同一份 DDL 的审计基线，内容必须与本文件
#: 逐字一致；单测 ``tests/persistence/test_mysql_saver_unit.py`` 做字符串比对防漂移。
DDL_STATEMENTS: tuple[str, ...] = tuple(
    stmt for _, statements in MIGRATIONS for stmt in statements
)


class MySQLCheckpointError(RuntimeError):
    """checkpoint 存储不可用（启动期 fail-fast，**不回退**进程内存储）。"""


def _patch_aiomysql_bytes_escaping(aiomysql) -> None:
    """修 aiomysql 0.3.2 与 PyMySQL ≥ 1.2.3 的字节转义不兼容（真库实测必现）。

    PyMySQL 1.2.3 把 ``converters.escape_bytes_prefixed`` 换成了一个字符串哨兵
    ``'DO NOT IMPORT THIS!!!'``，而 aiomysql 的 ``connection.escape()`` 仍然直接调用它，
    于是**只要参数里出现 bytes 就会 TypeError: 'str' object is not callable**。

    本模块传的参数里有两类 bytes，必然踩到：``BINARY(16)`` 的 ``ns_hash`` 与
    ``LONGBLOB`` 的 ``payload``。PyMySQL 1.2.3 提供了等价的 ``escape_bytes``
    （``_binary X'hex'``），补上即可，不必把业务依赖 pymysql 钉死在 1.2.2 以下。

    做成"检查后再补"，所以 pymysql 侧以后若再改名字，这里只会失效一次、日志里能立刻看到，
    而不是在每个请求上随机报错。
    """
    try:
        import pymysql.converters as _conv
    except ImportError:  # pragma: no cover - aiomysql 必然依赖 pymysql
        return
    connection = getattr(aiomysql, "connection", None)
    current = getattr(connection, "escape_bytes_prefixed", None)
    if callable(current):
        return
    replacement = getattr(_conv, "escape_bytes", None)
    if not callable(replacement):
        logger.error(
            "pymysql %s 的 escape_bytes_prefixed 不可调用且没有 escape_bytes 兜底："
            "写入 bytes 参数（ns_hash / payload）会失败，请降级 pymysql<1.2.3",
            getattr(__import__("pymysql"), "__version__", "?"),
        )
        return
    connection.escape_bytes_prefixed = replacement
    logger.warning(
        "已为 aiomysql 补上 escape_bytes_prefixed（PyMySQL ≥1.2.3 移除了该函数，"
        "改用 converters.escape_bytes）"
    )


@dataclass(frozen=True)
class MySQLCheckpointConfig:
    """``config.yaml`` 的 ``checkpoint.mysql`` 段（连接参数由调用方补齐后传入）。"""

    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "easy_agent"
    charset: str = "utf8mb4"
    table_prefix: str = "cp_"
    pool_min_size: int = 2
    pool_max_size: int = 10
    connect_timeout: int = 5
    read_timeout: int = 10
    auto_setup: bool = True
    auto_setup_lock_wait: int = 60
    gc_interval_seconds: int = 600
    record_ttl_days: int = 0
    ttl_basis: str = "session_updated_at"
    ttl_only_archived: bool = True
    ttl_batch_size: int = 200
    ttl_protect_active_stream: bool = True
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False)


def ns_hash(checkpoint_ns: str) -> bytes:
    """``checkpoint_ns`` → 16 字节哈希（主键用）。**算法变更需要数据迁移**。"""
    return hashlib.sha256((checkpoint_ns or "").encode("utf-8")).digest()[:16]


def _utcnow() -> datetime:
    """naive UTC：连接已 ``SET time_zone='+00:00'``，列是 DATETIME(6) 不带时区。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _is_inline_scalar(value: Any) -> bool:
    """能否安全内联进 checkpoint JSON。``bool`` 是 ``int`` 子类，一起判断即可。"""
    return value is None or isinstance(value, (bool, int, float, str))


class AsyncMySQLSaver(BaseCheckpointSaver[str]):
    """共享 MySQL checkpointer：所有 Pod 读写同一组表，任意 Pod 都能恢复同一 thread。

    生命周期由 ``start_checkpoint_backend()`` 管理：``asetup()`` 建池 + 建表 + 自检，
    ``aclose()`` 关池。**不要**每个会话 new 一个 —— 它是进程级共享对象，
    与官方 ``AsyncSqliteSaver`` 的用法一致（这也正是 ``shared_checkpointer_active()``
    要表达的意思）。
    """

    def __init__(self, config: MySQLCheckpointConfig):
        super().__init__()
        if not str(config.table_prefix or "").strip():
            # 前缀为空会把 cp_ 表写进业务表名空间（R24），宁可不启动
            raise MySQLCheckpointError("checkpoint.mysql.table_prefix 不能为空")
        self.config = config
        self._pool: Any = None
        self._closed = False
        self._setup_done = False

    # ── 表名 ────────────────────────────────────────────────────────
    @property
    def _p(self) -> str:
        return self.config.table_prefix

    @property
    def table_checkpoints(self) -> str:
        return f"{self._p}checkpoints"

    @property
    def table_blobs(self) -> str:
        return f"{self._p}checkpoint_blobs"

    @property
    def table_writes(self) -> str:
        return f"{self._p}checkpoint_writes"

    @property
    def table_meta(self) -> str:
        return f"{self._p}checkpoint_meta"

    # ── 连接池 ──────────────────────────────────────────────────────
    async def _create_pool(self):
        try:
            import aiomysql  # 延迟导入：只用 sqlite 后端的部署不必装它
        except ImportError as exc:  # pragma: no cover - 安装缺失时的明确报错
            raise MySQLCheckpointError(
                "缺少 aiomysql 依赖（checkpoint.mysql 需要）：pip install 'aiomysql>=0.3.2'"
            ) from exc
        _patch_aiomysql_bytes_escaping(aiomysql)
        cfg = self.config
        pool = await aiomysql.create_pool(
            host=cfg.host,
            port=int(cfg.port),
            user=cfg.user,
            password=cfg.password,
            db=cfg.database,
            charset=cfg.charset or "utf8mb4",
            cursorclass=aiomysql.DictCursor,
            autocommit=True,
            minsize=max(1, int(cfg.pool_min_size)),
            maxsize=max(1, int(cfg.pool_max_size)),
            connect_timeout=int(cfg.connect_timeout),
            # 会话时区定为 UTC：created_at 由应用写 naive UTC，读侧比较才不会错位。
            # charset=utf8mb4 同时决定建表时中文注释能否正确入库（§4.3.2 约束 1）。
            # aiomysql 不支持 read_timeout 参数，改用服务端 max_execution_time（只作用于
            # SELECT）实现"单次查询超时"，避免慢查询长期占着连接。
            init_command=(
                "SET time_zone='+00:00', "
                f"cte_max_recursion_depth={_CTE_MAX_DEPTH}, "
                f"max_execution_time={max(1000, int(cfg.read_timeout) * 1000)}"
            ),
        )
        logger.info(
            "HITL checkpointer 连接池已建立 | %s:%s/%s | prefix=%s | pool=%s~%s",
            cfg.host,
            cfg.port,
            cfg.database,
            cfg.table_prefix,
            cfg.pool_min_size,
            cfg.pool_max_size,
        )
        return pool

    async def _ensure_pool(self):
        if self._closed:
            raise MySQLCheckpointError("checkpoint 连接池已关闭")
        if self._pool is None:
            self._pool = await self._create_pool()
        return self._pool

    async def aclose(self) -> None:
        pool, self._pool = self._pool, None
        self._closed = True
        self._setup_done = False
        if pool is not None:
            pool.close()
            try:
                await pool.wait_closed()
            except Exception as exc:  # noqa: BLE001 - 关闭期异常不影响进程退出
                logger.warning("关闭 checkpoint 连接池异常（忽略）: %s", exc)

    # ── 建表 / 自检 ─────────────────────────────────────────────────
    async def asetup(self) -> None:
        """建池 → 建表/迁移 → 自检（表在不在 / collation / ns 算法）。

        任何一步失败都抛 :class:`MySQLCheckpointError`，由调用方 fail-fast ——
        **绝不静默退回进程内存储**（那会让一半 Pod 写库、一半写内存）。
        """
        if self._setup_done:
            return
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await self._check_server_version(conn)
            if self.config.auto_setup:
                await self._run_migrations(conn)
            await self._verify(conn)
            await self._warn_packet_size(conn)
        self._setup_done = True
        logger.info(
            "HITL checkpointer: AsyncMySQLSaver | %s:%s/%s | 表=%s, %s, %s, %s | auto_setup=%s",
            self.config.host,
            self.config.port,
            self.config.database,
            self.table_checkpoints,
            self.table_blobs,
            self.table_writes,
            self.table_meta,
            self.config.auto_setup,
        )

    async def _check_server_version(self, conn) -> None:
        async with conn.cursor() as cur:
            await cur.execute("SELECT VERSION()")
            row = await cur.fetchone()
        raw = str((row or {}).get("VERSION()") or "") if isinstance(row, dict) else str(
            (row or [""])[0] or ""
        )
        numbers: list[int] = []
        for part in raw.split("-")[0].split("."):
            digits = "".join(ch for ch in part if ch.isdigit())
            numbers.append(int(digits or 0))
        version = tuple(numbers[:3]) or (0, 0, 0)
        if version < _MIN_SERVER_VERSION:
            raise MySQLCheckpointError(
                f"checkpoint 需要 MySQL ≥ {'.'.join(map(str, _MIN_SERVER_VERSION))}，当前 {raw}"
            )

    async def _run_migrations(self, conn) -> None:
        """``GET_LOCK`` 串行化 + 按版本号补迁移。抢不到锁即启动失败（不静默跳过建表）。"""
        lock_name = f"{self.config.database}.{self._p}ddl"
        wait = max(1, int(self.config.auto_setup_lock_wait))
        async with conn.cursor() as cur:
            await cur.execute("SELECT GET_LOCK(%s, %s) AS locked", (lock_name, wait))
            row = await cur.fetchone()
        locked = int((row or {}).get("locked") or 0) if isinstance(row, dict) else 0
        if locked != 1:
            raise MySQLCheckpointError(
                f"checkpoint 建表锁 {lock_name!r} 在 {wait}s 内没抢到："
                "另一个 Pod 正在建表，或存在未释放的命名锁；请稍后重启本 Pod"
            )
        try:
            current = await self._read_schema_version(conn)
            applied = current
            for version, statements in MIGRATIONS:
                if version <= current:
                    continue
                for statement in statements:
                    async with conn.cursor() as cur:
                        await cur.execute(statement.format(p=self._p))
                applied = version
                logger.info(
                    "checkpoint 迁移已应用 | version=%s | 表前缀=%s", version, self._p
                )
            await self._write_meta(conn, "schema_version", str(applied))
            await self._write_meta(conn, "ns_hash_algo", NS_HASH_ALGO)
            if applied != current:
                await self._write_meta(
                    conn, "ddl_applied_at", _utcnow().isoformat(timespec="seconds")
                )
        finally:
            async with conn.cursor() as cur:
                await cur.execute("SELECT RELEASE_LOCK(%s) AS released", (lock_name,))

    async def _read_schema_version(self, conn) -> int:
        try:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT meta_value FROM {self.table_meta} "
                    "WHERE meta_key='schema_version'"
                )
                row = await cur.fetchone()
        except Exception as exc:  # noqa: BLE001 - 表还不存在（首次启动）
            if _errno_of(exc) == 1146:
                return 0
            raise
        return int(str((row or {}).get("meta_value") or 0) or 0)

    async def _write_meta(self, conn, key: str, value: str) -> None:
        async with conn.cursor() as cur:
            await cur.execute(
                f"INSERT INTO {self.table_meta} (meta_key, meta_value, updated_at) "
                "VALUES (%s, %s, %s) AS new ON DUPLICATE KEY UPDATE "
                "meta_value=new.meta_value, updated_at=new.updated_at",
                (key, value, _utcnow()),
            )

    async def _verify(self, conn) -> None:
        """启动自检：4 张表都在、collation 是 ``utf8mb4_bin``、ns 算法与库里一致。"""
        expected = (
            self.table_meta,
            self.table_checkpoints,
            self.table_blobs,
            self.table_writes,
        )
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT TABLE_NAME AS name, TABLE_COLLATION AS collation "
                "FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "AND TABLE_NAME IN (%s, %s, %s, %s)",
                expected,
            )
            rows = await cur.fetchall() or []
        found = {str(r["name"]): str(r.get("collation") or "") for r in rows}
        missing = [name for name in expected if name not in found]
        if missing:
            raise MySQLCheckpointError(
                f"checkpoint 表缺失：{missing}；auto_setup={self.config.auto_setup} 时请确认"
                "账号有 DDL 权限，或先手工执行 scripts/db/mysql-checkpoint-ddl.sql"
            )
        wrong = [
            f"{name}={found[name]}" for name in expected if found[name] != "utf8mb4_bin"
        ]
        if wrong:
            raise MySQLCheckpointError(
                "checkpoint 表 collation 必须是 utf8mb4_bin"
                "（thread_id 大小写不同不能算同一行），当前 "
                + "，".join(wrong)
                + "；修复："
                + "；".join(
                    f"ALTER TABLE {name} CONVERT TO CHARACTER SET utf8mb4 "
                    "COLLATE utf8mb4_bin"
                    for name in expected
                )
            )
        if self.config.auto_setup:
            stored_algo = await self._read_meta(conn, "ns_hash_algo")
            if stored_algo and stored_algo != NS_HASH_ALGO:
                raise MySQLCheckpointError(
                    f"检测到 checkpoint_ns 哈希算法变更（库内={stored_algo}，"
                    f"代码={NS_HASH_ALGO}）：历史 checkpoint 将全部无法命中，"
                    "必须先做数据迁移；不要强行启动"
                )

    async def _read_meta(self, conn, key: str) -> str:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT meta_value FROM {self.table_meta} WHERE meta_key=%s", (key,)
            )
            row = await cur.fetchone()
        return str((row or {}).get("meta_value") or "")

    async def read_meta(self, key: str) -> str:
        """读一条元数据（``schema_version`` / ``ns_hash_algo`` / ``last_gc_at``）。

        运维排查用：确认这个库到底建到哪一版、GC 上次跑是什么时候。
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            return await self._read_meta(conn, key)

    async def _warn_packet_size(self, conn) -> None:
        try:
            async with conn.cursor() as cur:
                await cur.execute("SHOW VARIABLES LIKE 'max_allowed_packet'")
                row = await cur.fetchone()
            size = int((row or {}).get("Value") or 0) if isinstance(row, dict) else 0
        except Exception:  # noqa: BLE001 - 权限不足时不影响启动
            return
        if 0 < size < 16 * 1024 * 1024:
            logger.warning(
                "max_allowed_packet=%s 小于 16MB：工具返回大结果时单条 checkpoint "
                "写入可能被拒（Packet too large）",
                size,
            )

    async def set_last_gc_at(self, value: str) -> None:
        """GC 跑完写一次时间戳：否则运维无法判断「到底有没有在扫」。"""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await self._write_meta(conn, "last_gc_at", value)

    # ── GC：清理「会话已删除」与「过期的会话」 ──────────────────────
    async def list_orphan_threads(self, *, limit: int = 500) -> list[str]:
        """``sessions`` 表里已经不存在的会话，它的 thread 就是孤儿。

        一条库内 JOIN 走 ``idx_session``；**不要**先 ``SELECT DISTINCT thread_id``
        全表拉到应用层再逐个解析会话 id（几十万 thread 时那是全表扫描 + N+1）。
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT DISTINCT c.thread_id AS thread_id "
                    f"FROM {self.table_checkpoints} c "
                    "LEFT JOIN sessions s ON s.session_id = c.session_id "
                    "WHERE s.session_id IS NULL AND c.session_id <> '' "
                    "LIMIT %s",
                    (int(limit),),
                )
                rows = await cur.fetchall() or []
        return [str(r["thread_id"]) for r in rows]

    async def list_expired_threads(
        self,
        *,
        cutoff: str,
        basis: str = "session_updated_at",
        only_archived: bool = True,
        protect_active_stream: bool = True,
        limit: int = 200,
    ) -> list[str]:
        """按时间淘汰：返回「整条 thread 一起删」的 thread_id 列表。

        **删除粒度必须是整 thread**：按 ``created_at < cutoff`` 删部分 checkpoint 会产生
        父 id 悬空的碎片链，``messages`` 的 ``_DeltaSnapshot`` 被删后恢复出来是空历史，
        而且不报错。

        ``cutoff`` 是 ISO 字符串；``sessions.updated_at`` 也是 ISO 字符串，字典序=时间序
        只在格式完全一致时成立（见风险 R22），因此本函数只接受同格式的 cutoff。
        """
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                if basis == "checkpoint_created_at":
                    await cur.execute(
                        f"SELECT thread_id AS thread_id FROM {self.table_checkpoints} "
                        "GROUP BY thread_id HAVING MAX(created_at) < %s LIMIT %s",
                        (cutoff, int(limit)),
                    )
                    rows = await cur.fetchall() or []
                    return [str(r["thread_id"]) for r in rows]

                where = ["s.updated_at < %s"]
                params: list[Any] = [cutoff]
                if only_archived:
                    where.append("s.archived = 1")
                if protect_active_stream:
                    where.append(
                        "(r.stream_until IS NULL OR r.stream_until = '' "
                        "OR r.stream_until < %s)"
                    )
                    params.append(cutoff)
                params.append(int(limit))
                await cur.execute(
                    f"SELECT DISTINCT c.thread_id AS thread_id "
                    f"FROM {self.table_checkpoints} c "
                    "JOIN sessions s ON s.session_id = c.session_id "
                    "LEFT JOIN session_runtime r ON r.session_id = s.session_id "
                    "WHERE " + " AND ".join(where) + " LIMIT %s",
                    tuple(params),
                )
                rows = await cur.fetchall() or []
        return [str(r["thread_id"]) for r in rows]

    async def stats(self) -> dict[str, Any]:
        """运维看容量用：表数据量（字节）与行数，不扫全表。"""
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT TABLE_NAME AS name, "
                    "DATA_LENGTH + INDEX_LENGTH AS bytes, TABLE_ROWS AS rows_est "
                    "FROM information_schema.TABLES "
                    "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s, %s)",
                    (self.table_checkpoints, self.table_blobs, self.table_writes),
                )
                rows = await cur.fetchall() or []
        return {
            str(r["name"]): {
                "bytes": int(r.get("bytes") or 0),
                "rows_est": int(r.get("rows_est") or 0),
            }
            for r in rows
        }

    # ── 写：aput ────────────────────────────────────────────────────
    async def aput(
        self,
        config,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ):
        """写一个 checkpoint：blobs + 主体在**同一事务**里提交。

        内联策略（对齐官方 postgres）：``None/bool/int/float/str`` 额外内联进主体的 JSON，
        其余（含 ``_DeltaSnapshot``）**只**写 blobs。

        注意 ``new_versions`` 里的每个 channel 都**必须**有一行 blob（拿不到值就写
        ``type='empty'``）：主体 JSON 的 ``channel_values`` 是"本步变更"，不含未变的
        channel；读的时候要靠 ``channel_versions`` 去 blobs 里按版本号回填，缺行就等于
        该 channel 这一步不存在——DeltaChannel 的语义正是如此，所以空值必须显式写 empty，
        不能省略。内联的那份是给人看的（``JSON_EXTRACT`` 排查、少量标量免一次 blob 往返），
        不是回填依据。
        """
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "") or ""
        parent_id = config["configurable"].get("checkpoint_id")
        checkpoint_id = str(checkpoint["id"])
        ns_hash_value = ns_hash(checkpoint_ns)

        channel_values = dict(checkpoint.get("channel_values") or {})
        inline = {k: v for k, v in channel_values.items() if _is_inline_scalar(v)}
        stored = dict(checkpoint)
        stored["channel_values"] = inline

        blob_rows: list[tuple[Any, ...]] = []
        for channel, version in new_versions.items():
            if channel in channel_values:
                type_, payload = self.serde.dumps_typed(channel_values[channel])
            else:
                # 该版本下该 channel 没有值：写 empty 占位，读的时候跳过
                type_, payload = "empty", None
            blob_rows.append(
                (thread_id, ns_hash_value, channel, str(version), type_, payload)
            )

        raw_versions = checkpoint.get("channel_versions") or {}
        versions_json = {
            str(k): v for k, v in raw_versions.items() if _is_inline_scalar(v)
        }
        step = metadata.get("step") if isinstance(metadata, Mapping) else None

        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.begin()
            try:
                if blob_rows:
                    async with conn.cursor() as cur:
                        await cur.executemany(
                            f"INSERT INTO {self.table_blobs} "
                            "(thread_id, checkpoint_ns_hash, channel, version, "
                            "type, payload) "
                            "VALUES (%s, %s, %s, %s, %s, %s) "
                            "ON DUPLICATE KEY UPDATE version=version",
                            blob_rows,
                        )
                async with conn.cursor() as cur:
                    await cur.execute(
                        f"INSERT INTO {self.table_checkpoints} "
                        "(thread_id, session_id, checkpoint_ns_hash, checkpoint_ns, "
                        "checkpoint_id, parent_checkpoint_id, step, channel_versions, "
                        "type, checkpoint, metadata, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "AS new ON DUPLICATE KEY UPDATE checkpoint=new.checkpoint, "
                        "metadata=new.metadata, "
                        "channel_versions=new.channel_versions, "
                        "parent_checkpoint_id=new.parent_checkpoint_id",
                        (
                            thread_id,
                            session_id_of_thread(thread_id) or "",
                            ns_hash_value,
                            checkpoint_ns,
                            checkpoint_id,
                            parent_id,
                            step if isinstance(step, int) else None,
                            _json_dumps(versions_json),
                            "json",
                            _json_dumps(stored),
                            _json_dumps(get_checkpoint_metadata(config, metadata)),
                            _utcnow(),
                        ),
                    )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id,
            }
        }

    async def aput_writes(
        self,
        config,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        """写中间写入（HITL 的关键路径）。

        冲突策略取决于**这次 writes 是否全是特殊通道**（``__error__`` /
        ``__scheduled__`` / ``__interrupt__`` / ``__resume__``）：

        - 全是 → 覆盖。同一个 interrupt 重复写必须覆盖，否则审批界面会一直显示
          第一次的内容（用户点了"继续"看到的还是旧审批）；
        - 否则 → 冲突即忽略。普通 writes 重复投递应当丢弃（幂等），覆盖会让消息重复。
        """
        if not writes:
            return
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "") or ""
        checkpoint_id = str(config["configurable"]["checkpoint_id"])
        ns_hash_value = ns_hash(checkpoint_ns)
        overwrite = all(w[0] in WRITES_IDX_MAP for w in writes)
        suffix = (
            "AS new ON DUPLICATE KEY UPDATE channel=new.channel, type=new.type, "
            "payload=new.payload, task_path=new.task_path"
            if overwrite
            else "ON DUPLICATE KEY UPDATE idx=idx"
        )
        rows = [
            (
                thread_id,
                ns_hash_value,
                checkpoint_id,
                str(task_id),
                WRITES_IDX_MAP.get(channel, idx),
                str(channel),
                str(task_path or ""),
                *self.serde.dumps_typed(value),
            )
            for idx, (channel, value) in enumerate(writes)
        ]
        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    f"INSERT INTO {self.table_writes} "
                    "(thread_id, checkpoint_ns_hash, checkpoint_id, task_id, idx, "
                    "channel, task_path, type, payload) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) " + suffix,
                    rows,
                )

    # ── 读：aget_tuple ──────────────────────────────────────────────
    async def aget_tuple(self, config) -> CheckpointTuple | None:
        """读一个 checkpoint：checkpoints → blobs → writes，**同一只读事务**内完成。

        连接池是 ``autocommit=True``，每条 SELECT 自成一个事务；三次查询若不显式包进
        一个事务，会读到「checkpoint 行已提交、blobs 还没提交」的中间态。
        """
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "") or ""
        checkpoint_id = get_checkpoint_id(config)
        ns_hash_value = ns_hash(checkpoint_ns)

        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            await conn.begin()
            try:
                row = await self._select_checkpoint_row(
                    conn, thread_id, ns_hash_value, checkpoint_ns, checkpoint_id
                )
                if row is None:
                    await conn.rollback()
                    return None
                checkpoint = await self._load_channel_values(
                    conn, thread_id, ns_hash_value, row
                )
                pending = await self._load_writes(
                    conn, thread_id, ns_hash_value, str(row["checkpoint_id"])
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

        resolved_id = str(row["checkpoint_id"])
        parent_id = row.get("parent_checkpoint_id")
        return CheckpointTuple(
            {
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": resolved_id,
                }
            },
            checkpoint,
            _json_loads(row.get("metadata")) or {},
            (
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_id,
                    }
                }
                if parent_id
                else None
            ),
            pending,
        )

    async def _select_checkpoint_row(
        self, conn, thread_id, ns_hash_value, checkpoint_ns, checkpoint_id
    ):
        base = (
            f"SELECT checkpoint_id, parent_checkpoint_id, channel_versions, "
            f"checkpoint, metadata FROM {self.table_checkpoints} "
            "WHERE thread_id=%s AND checkpoint_ns_hash=%s AND checkpoint_ns=%s"
        )
        async with conn.cursor() as cur:
            if checkpoint_id:
                await cur.execute(
                    base + " AND checkpoint_id=%s",
                    (thread_id, ns_hash_value, checkpoint_ns, checkpoint_id),
                )
            else:
                # 取最新按 checkpoint_id 倒序：UUIDv6 高位是时间戳，正好命中主键倒序
                # 扫描。不按 created_at —— 那是应用写入时刻，多 Pod 并发时可能反向。
                await cur.execute(
                    base + " ORDER BY checkpoint_id DESC LIMIT 1",
                    (thread_id, ns_hash_value, checkpoint_ns),
                )
            return await cur.fetchone()

    async def _load_channel_values(
        self, conn, thread_id, ns_hash_value, row
    ) -> Checkpoint:
        checkpoint = _json_loads(row.get("checkpoint")) or {}
        checkpoint["channel_values"] = dict(checkpoint.get("channel_values") or {})
        versions = (
            _json_loads(row.get("channel_versions"))
            or checkpoint.get("channel_versions")
            or {}
        )
        if versions:
            wanted = [(str(k), str(v)) for k, v in versions.items()]
            placeholders = ", ".join(["%s"] * len(wanted))
            sql = (
                f"SELECT channel, version, type, payload FROM {self.table_blobs} "
                "WHERE thread_id=%s AND checkpoint_ns_hash=%s "
                f"AND version IN ({placeholders})"
            )
            async with conn.cursor() as cur:
                await cur.execute(
                    sql, (thread_id, ns_hash_value, *[v for _, v in wanted])
                )
                blob_rows = await cur.fetchall() or []
            by_key = {
                (str(r["channel"]), str(r["version"])): r for r in blob_rows
            }
            for channel, version in wanted:
                blob = by_key.get((channel, version))
                if (
                    blob is None
                    or blob.get("type") == "empty"
                    or blob.get("payload") is None
                ):
                    continue
                checkpoint["channel_values"][channel] = self.serde.loads_typed(
                    (blob["type"], bytes(blob["payload"]))
                )
        return checkpoint

    async def _load_writes(
        self, conn, thread_id, ns_hash_value, checkpoint_id
    ) -> list[PendingWrite]:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT task_id, channel, type, payload, task_path, idx "
                f"FROM {self.table_writes} "
                "WHERE thread_id=%s AND checkpoint_ns_hash=%s AND checkpoint_id=%s",
                (thread_id, ns_hash_value, checkpoint_id),
            )
            rows = await cur.fetchall() or []
        rows.sort(
            key=lambda r: writes_sort_key(
                str(r.get("task_path") or ""), str(r["task_id"]), int(r["idx"])
            )
        )
        return [
            (
                str(r["task_id"]),
                str(r["channel"]),
                self.serde.loads_typed((r["type"], bytes(r["payload"]))),
            )
            for r in rows
        ]

    # ── 列历史：alist ───────────────────────────────────────────────
    async def alist(
        self,
        config,
        *,
        filter: dict[str, Any] | None = None,
        before=None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        """按 ``checkpoint_id`` 倒序分页；``before`` 是上一页最后一条的 config。

        排序**只用** ``checkpoint_id``（UUIDv6 = 时间序，主键倒序扫描，无 filesort）；
        再叠加 ``created_at`` 只会让索引失效。
        """
        clauses: list[str] = []
        params: list[Any] = []
        if config is not None:
            thread_id = config["configurable"].get("thread_id")
            checkpoint_ns = config["configurable"].get("checkpoint_ns", "") or ""
            if thread_id:
                clauses.append("thread_id=%s")
                params.append(str(thread_id))
            if "checkpoint_ns" in config["configurable"]:
                clauses.append("checkpoint_ns_hash=%s AND checkpoint_ns=%s")
                params.extend([ns_hash(checkpoint_ns), checkpoint_ns])
        if before is not None:
            before_id = get_checkpoint_id(before)
            if before_id:
                clauses.append("checkpoint_id < %s")
                params.append(before_id)
        for key, value in (filter or {}).items():
            clauses.append("JSON_UNQUOTE(JSON_EXTRACT(metadata, %s)) = %s")
            params.extend([_json_path(key), str(value)])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
            f"channel_versions, checkpoint, metadata FROM {self.table_checkpoints} "
            f"{where} ORDER BY checkpoint_id DESC"
        )
        if limit is not None:
            sql += " LIMIT %s"
            params.append(int(limit))

        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall() or []
            for row in rows:
                ns_value = str(row.get("checkpoint_ns") or "")
                ns_value_hash = ns_hash(ns_value)
                row_thread = str(row["thread_id"])
                checkpoint = await self._load_channel_values(
                    conn, row_thread, ns_value_hash, row
                )
                pending = await self._load_writes(
                    conn, row_thread, ns_value_hash, str(row["checkpoint_id"])
                )
                parent_id = row.get("parent_checkpoint_id")
                yield CheckpointTuple(
                    {
                        "configurable": {
                            "thread_id": row_thread,
                            "checkpoint_ns": ns_value,
                            "checkpoint_id": str(row["checkpoint_id"]),
                        }
                    },
                    checkpoint,
                    _json_loads(row.get("metadata")) or {},
                    (
                        {
                            "configurable": {
                                "thread_id": row_thread,
                                "checkpoint_ns": ns_value,
                                "checkpoint_id": parent_id,
                            }
                        }
                        if parent_id
                        else None
                    ),
                    pending,
                )

    # ── 删除 ────────────────────────────────────────────────────────
    async def adelete_thread(self, thread_id: str) -> int:
        """删掉整条 thread：writes → blobs → checkpoints，各表分批删。

        顺序固定：先子后主。万一中途崩溃，thread 仍留在 checkpoints 里，下一轮 GC 还会
        选中它；反过来先删 checkpoints 会留下永远查不到的孤儿 blob。
        """
        thread_id = str(thread_id)
        pool = await self._ensure_pool()
        deleted = 0
        async with pool.acquire() as conn:
            deleted += await self._delete_batched(
                conn,
                self.table_writes,
                "thread_id=%s",
                thread_id,
                _DELETE_BATCH_WRITES,
            )
            deleted += await self._delete_batched(
                conn,
                self.table_blobs,
                "thread_id=%s",
                thread_id,
                _DELETE_BATCH_BLOBS,
            )
            deleted += await self._delete_batched(
                conn,
                self.table_checkpoints,
                "thread_id=%s ORDER BY checkpoint_id",
                thread_id,
                _DELETE_BATCH_CHECKPOINTS,
            )
        if deleted:
            logger.info(
                "checkpoint thread 已删除 | thread=%s | session=%s | rows=%s",
                thread_id,
                session_id_of_thread(thread_id) or "-",
                deleted,
            )
        return deleted

    async def _delete_batched(self, conn, table, where, param, batch: int) -> int:
        total = 0
        while True:
            async with conn.cursor() as cur:
                await cur.execute(
                    f"DELETE FROM {table} WHERE {where} LIMIT {int(batch)}", (param,)
                )
                affected = int(cur.rowcount or 0)
            total += affected
            if affected < batch:
                return total
            # 每批让出事件循环：删长会话时不能把整个事件循环占死
            await asyncio.sleep(0)

    async def aprine(
        self, thread_ids: Sequence[str], *, strategy: str = "delete"
    ) -> None:
        """缩容：只支持 ``delete``。

        ``keep_latest`` 会删掉快照链中间的 checkpoint，``_DeltaSnapshot`` 一旦被删，
        delta 通道会**静默重建为空数组**（长会话突然丢历史，不报错）。基类 docstring
        给了三条安全路径，我们选最保守的一条：直接拒绝。
        """
        if strategy != "delete":
            raise NotImplementedError(
                "AsyncMySQLSaver 只支持 aprine(strategy='delete')：keep_latest 会切断 "
                "DeltaChannel 的快照链（messages 会静默变成空历史）"
            )
        for thread_id in dict.fromkeys(t for t in thread_ids if t):
            await self.adelete_thread(thread_id)

    # ── delta 历史（P2 快路径） ──────────────────────────────────────
    async def aget_delta_channel_history(
        self, *, config, channels: Sequence[str]
    ) -> Mapping[str, DeltaChannelHistory]:
        """沿 ``parent_checkpoint_id`` 一次性把祖先链取出来，而不是逐层 ``aget_tuple``。

        基类默认实现每层祖先要 3 次 SQL（checkpoints + blobs + writes）；长会话最坏回溯
        50 层 = 180 次往返，跨网络 300~500ms，表现为「点开长会话先白屏半秒」。
        这里固定 4 次：链（1~n 批）+ 存在性（1）+ 种子值（≤1）+ 全部 writes（1）。
        """
        if not channels:
            return {}
        channels = list(channels)
        thread_id = str(config["configurable"]["thread_id"])
        checkpoint_ns = config["configurable"].get("checkpoint_ns", "") or ""
        ns_hash_value = ns_hash(checkpoint_ns)
        checkpoint_id = get_checkpoint_id(config)
        if not checkpoint_id:
            target = await self.aget_tuple(config)
            if target is None:
                return {ch: {"writes": []} for ch in channels}
            checkpoint_id = str(target.config["configurable"]["checkpoint_id"])

        pool = await self._ensure_pool()
        async with pool.acquire() as conn:
            chain = await self._walk_chain(
                conn, thread_id, ns_hash_value, checkpoint_ns, checkpoint_id
            )
            if not chain:
                return {ch: {"writes": []} for ch in channels}
            # 第一行是 target 自己：它的 writes/seed 不属于契约
            ancestors = chain[1:]
            seeds, stop_ids = await self._resolve_seeds(
                conn, thread_id, ns_hash_value, channels, ancestors
            )
            chain_by_channel: dict[str, list[str]] = {}
            for channel in channels:
                stop = stop_ids.get(channel)
                ids: list[str] = []
                for row in ancestors:
                    cid = str(row["checkpoint_id"])
                    ids.append(cid)
                    if stop == cid:
                        break
                chain_by_channel[channel] = ids
            all_ids = list(dict.fromkeys(i for ids in chain_by_channel.values() for i in ids))
            writes_by_checkpoint = await self._load_chain_writes(
                conn, thread_id, ns_hash_value, channels, all_ids
            )

        result: dict[str, DeltaChannelHistory] = {}
        for channel in channels:
            collected: list[PendingWrite] = []
            # 链是「近→远」，输出要求「旧→新」
            for cid in reversed(chain_by_channel[channel]):
                collected.extend(writes_by_checkpoint.get((cid, channel), []))
            entry: DeltaChannelHistory = {"writes": collected}
            if channel in seeds:
                entry["seed"] = seeds[channel]
            result[channel] = entry
        return result

    async def _walk_chain(
        self, conn, thread_id, ns_hash_value, checkpoint_ns, checkpoint_id
    ) -> list[dict]:
        """递归 CTE 沿 ``parent_checkpoint_id`` 上溯；每批最多 ``_DELTA_WALK_BATCH`` 层。

        用批而不是一次走到根：MySQL 会把整条链物化，长会话（几千个 checkpoint）会白读
        几千行。分批后常见情况（50 层内命中快照）只需 1 批。

        **只沿 parent 走，不能按 checkpoint_id 范围扫**：id 只在单进程内单调，跨 Pod
        并发时范围扫会漏掉 id 排在子节点之后的父节点。遇到重复 id 立即停（防人工修数据
        造成环，否则会打满 ``cte_max_recursion_depth``）。
        """
        rows: list[dict] = []
        seen: set[str] = set()
        cursor_id: str | None = checkpoint_id
        while cursor_id and cursor_id not in seen:
            sql = (
                "WITH RECURSIVE ancestors (checkpoint_id, parent_checkpoint_id, "
                "channel_versions, inline_keys, depth) AS ("
                "SELECT checkpoint_id, parent_checkpoint_id, channel_versions, "
                "JSON_KEYS(checkpoint, '$.channel_values'), 0 "
                f"FROM {self.table_checkpoints} "
                "WHERE thread_id=%s AND checkpoint_ns_hash=%s AND checkpoint_id=%s "
                "UNION ALL "
                "SELECT c.checkpoint_id, c.parent_checkpoint_id, c.channel_versions, "
                "JSON_KEYS(c.checkpoint, '$.channel_values'), a.depth + 1 "
                f"FROM ancestors a JOIN {self.table_checkpoints} c "
                "ON c.thread_id=%s AND c.checkpoint_ns_hash=%s "
                "AND c.checkpoint_id = a.parent_checkpoint_id "
                f"WHERE a.depth < {_DELTA_WALK_BATCH}"
                ") SELECT checkpoint_id, parent_checkpoint_id, channel_versions, "
                "inline_keys, depth FROM ancestors ORDER BY depth"
            )
            async with conn.cursor() as cur:
                await cur.execute(
                    sql,
                    (thread_id, ns_hash_value, cursor_id, thread_id, ns_hash_value),
                )
                batch = list(await cur.fetchall() or [])
            if not batch:
                break
            fresh = [row for row in batch if str(row["checkpoint_id"]) not in seen]
            for row in fresh:
                seen.add(str(row["checkpoint_id"]))
                rows.append(row)
            if not fresh:
                break
            cursor_id = rows[-1].get("parent_checkpoint_id")
        return rows

    async def _resolve_seeds(
        self, conn, thread_id, ns_hash_value, channels, ancestors
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """给每个 channel 找最近的祖先种子值，以及它的 checkpoint id（链要在此截断）。"""
        if not ancestors:
            return {}, {}
        wanted: dict[str, list[tuple[str, str | None]]] = {ch: [] for ch in channels}
        for row in ancestors:
            versions = _json_loads(row.get("channel_versions")) or {}
            inline_keys = _json_keys(row.get("inline_keys"))
            cid = str(row["checkpoint_id"])
            for channel in channels:
                if channel in inline_keys:
                    wanted[channel].append((cid, None))
                elif channel in versions:
                    wanted[channel].append((cid, str(versions[channel])))

        all_versions = list(
            {version for pairs in wanted.values() for _, version in pairs if version}
        )
        presence: set[tuple[str, str]] = set()
        payloads: dict[tuple[str, str], tuple[str, bytes]] = {}
        if all_versions:
            placeholders = ", ".join(["%s"] * len(all_versions))
            async with conn.cursor() as cur:
                await cur.execute(
                    f"SELECT channel, version, type, payload FROM {self.table_blobs} "
                    "WHERE thread_id=%s AND checkpoint_ns_hash=%s "
                    f"AND version IN ({placeholders})",
                    (thread_id, ns_hash_value, *all_versions),
                )
                for row in await cur.fetchall() or []:
                    if row.get("type") == "empty" or row.get("payload") is None:
                        continue
                    key = (str(row["channel"]), str(row["version"]))
                    presence.add(key)
                    payloads[key] = (str(row["type"]), bytes(row["payload"]))

        seeds: dict[str, Any] = {}
        stop_ids: dict[str, str] = {}
        inline_seeds: dict[str, str] = {}
        for channel in channels:
            for cid, version in wanted[channel]:
                if version is None:
                    inline_seeds[channel] = cid
                    stop_ids[channel] = cid
                    break
                if (channel, version) in presence:
                    type_tag, blob = payloads[(channel, version)]
                    seeds[channel] = self.serde.loads_typed((type_tag, blob))
                    stop_ids[channel] = cid
                    break
        if inline_seeds:
            inline_values = await self._load_inline_seeds(
                conn, thread_id, ns_hash_value, inline_seeds
            )
            seeds.update(inline_values)
            for channel in list(stop_ids):
                if channel in inline_seeds and channel not in inline_values:
                    # 内联 key 在、但值取不到（脏数据）：别把它当种子停在这里
                    stop_ids.pop(channel, None)
        return seeds, stop_ids

    async def _load_inline_seeds(
        self, conn, thread_id, ns_hash_value, inline_seeds: dict[str, str]
    ) -> dict[str, Any]:
        """内联标量种子的读取：只为选中的那几个 checkpoint 取一次 JSON。"""
        result: dict[str, Any] = {}
        by_checkpoint: dict[str, list[str]] = {}
        for channel, cid in inline_seeds.items():
            by_checkpoint.setdefault(cid, []).append(channel)
        async with conn.cursor() as cur:
            for cid, channel_list in by_checkpoint.items():
                await cur.execute(
                    f"SELECT checkpoint FROM {self.table_checkpoints} "
                    "WHERE thread_id=%s AND checkpoint_ns_hash=%s AND checkpoint_id=%s",
                    (thread_id, ns_hash_value, cid),
                )
                row = await cur.fetchone()
                if not row:
                    continue
                values = (_json_loads(row.get("checkpoint")) or {}).get(
                    "channel_values"
                ) or {}
                for channel in channel_list:
                    if channel in values:
                        result[channel] = values[channel]
        return result

    async def _load_chain_writes(
        self, conn, thread_id, ns_hash_value, channels, checkpoint_ids
    ) -> dict[tuple[str, str], list[PendingWrite]]:
        """一条 SQL 取整条链上这些 channel 的全部 writes，按 task_path/task_id/idx 排序。"""
        if not checkpoint_ids:
            return {}
        id_placeholders = ", ".join(["%s"] * len(checkpoint_ids))
        ch_placeholders = ", ".join(["%s"] * len(channels))
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT checkpoint_id, task_id, channel, type, payload, "
                f"task_path, idx FROM {self.table_writes} "
                "WHERE thread_id=%s AND checkpoint_ns_hash=%s "
                f"AND checkpoint_id IN ({id_placeholders}) "
                f"AND channel IN ({ch_placeholders})",
                (thread_id, ns_hash_value, *checkpoint_ids, *channels),
            )
            rows = list(await cur.fetchall() or [])
        grouped: dict[tuple[str, str], list[dict]] = {}
        for row in rows:
            grouped.setdefault(
                (str(row["checkpoint_id"]), str(row["channel"])), []
            ).append(row)
        result: dict[tuple[str, str], list[PendingWrite]] = {}
        for key, items in grouped.items():
            items.sort(
                key=lambda r: writes_sort_key(
                    str(r.get("task_path") or ""),
                    str(r["task_id"]),
                    int(r["idx"]),
                )
            )
            result[key] = [
                (
                    str(r["task_id"]),
                    key[1],
                    self.serde.loads_typed((r["type"], bytes(r["payload"]))),
                )
                for r in items
            ]
        return result


# ── 小工具 ──────────────────────────────────────────────────────────
def _json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _json_keys(value: Any) -> set[str]:
    parsed = _json_loads(value)
    if isinstance(parsed, list):
        return {str(item) for item in parsed}
    return set()


def _json_path(key: str) -> str:
    """构造 ``$.key``；带特殊字符时用双引号包裹并转义。"""
    if key and key.replace("_", "").isalnum():
        return f"$.{key}"
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'$."{escaped}"'


def _errno_of(exc: BaseException) -> int | None:
    args = getattr(exc, "args", ())
    if args and isinstance(args[0], int):
        return args[0]
    return None


__all__ = [
    "AsyncMySQLSaver",
    "DDL_STATEMENTS",
    "MIGRATIONS",
    "MySQLCheckpointConfig",
    "MySQLCheckpointError",
    "NS_HASH_ALGO",
    "SCHEMA_VERSION",
    "ns_hash",
]
