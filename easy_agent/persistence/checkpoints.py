"""Durable HITL checkpointer：共享 MySQL（自研）或官方 SQLite。

``EASY_CHECKPOINT_BACKEND`` 选的是**用哪一种 checkpointer 实现**，不是"连哪个数据库"
（数据库连接在 ``database.mysql`` / ``checkpoint.mysql``）：

============================  =======================================================  ==================
值                            实现                                                     状态存在哪
============================  =======================================================  ==================
``mysql_saver``（默认）        ``AsyncMySQLSaver``：协议级 4 张表，多 Pod 共享              业务库 ``cp_*`` 表
``mysql``                     **废弃别名**，等价 ``mysql_saver``（老 ConfigMap 里可能写着）  同上
``sqlite``                    官方 ``AsyncSqliteSaver``                                本 Pod 的 ``{pod}.db``
``legacy``                    进程内 ``MemorySaver``                                    内存，重启即丢
``auto``                      业务库是 MySQL → ``mysql_saver``；否则 → ``sqlite``        跟随后两者
============================  =======================================================  ==================

为什么默认是 MySQL：SQLite 文件不能跨副本共享（WAL 不能跨进程可靠共享），多副本下 HITL
审批只能在记录它的那个 Pod 上恢复，靠 Ingress 粘性会话兜着。粘性一失效（清 cookie、换设备、
Pod 重建、HPA 扩缩容）审批就丢。共享 saver 把状态放到库里，任意 Pod 都能接手，粘性不再必需。

**sqlite 分支一行未改**，它是回滚目标（发布后出问题把 ``EASY_CHECKPOINT_BACKEND`` 改回
``sqlite`` 即可，历史会话由 ``plan_turn`` 从 ``session_messages`` 水合，一条不少）。
回滚的前提是保留 Ingress 的粘性 annotation 与路径里的 ``{pod}``（see
``validate_checkpoint_deployment``）。

命名上的两件事容易搞混：

- ``sqlite_checkpointer_active()`` 语义是"当前有没有一个**进程级共享**的官方 saver"，
  所以它现在同时覆盖 sqlite 与 mysql_saver；新增的 ``shared_checkpointer_active()`` 是
  推荐写法，老名字保留只为兼容。
- ``per_pod_checkpoints_active()`` 仍然**只**表示 sqlite 文件按 Pod 隔离 —— 审批归属校验
  依赖它，不能跟着改。

见 ``docs/design/2026-11-11-self-hosted-mysql-saver-plan.md``。
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from easy_agent.bootstrap.env import env_bool, env_int, env_str
from easy_agent.persistence.mysql_saver import (
    AsyncMySQLSaver,
    MySQLCheckpointConfig,
    MySQLCheckpointError,
)
from easy_agent.persistence.thread_ids import session_id_of_thread
from easy_agent.utils.paths import PROJECT_ROOT
from easy_agent.utils.blocking import run_blocking
from easy_agent.utils.deployment import (
    detect_replica_count,
    detect_workers_per_pod,
    instance_name,
    is_valid_instance_name,
)
from easy_agent.db.repositories import sessions as sessions_repo

logger = logging.getLogger(__name__)

# legacy 后端：每会话一个进程内 saver，只为调试保留
_CHECKPOINTERS: dict[str, MemorySaver] = {}
_CHECKPOINTER_MAX = 200
_CHECKPOINTER_LOCK = threading.Lock()
_sqlite_cm = None
_sqlite_saver = None
_mysql_saver: AsyncMySQLSaver | None = None
#: 进程级共享 saver（sqlite 或 mysql_saver），语义中性，判据统一用它
_shared_saver: Any = None
# 打开 saver 时用的解析结果：按 pod 隔离（路径含 {pod}）时记下实例名
_active_selection: "CheckpointSelection | None" = None
_gc_task: asyncio.Task | None = None


# ── 后端选择 ────────────────────────────────────────────────────────
BACKEND_AUTO = "auto"
BACKEND_SQLITE = "sqlite"
BACKEND_LEGACY = "legacy"
BACKEND_MYSQL_SAVER = "mysql_saver"
#: 废弃别名：历史上 ``mysql`` 指"业务库全量 pickle 快照"，那套实现已删除；
#: 现在它映射到 ``mysql_saver``，只为不让"ConfigMap 里还写着 mysql"的部署启动失败。
BACKEND_MYSQL_ALIAS = "mysql"
BACKEND_CHOICES = (
    BACKEND_AUTO,
    BACKEND_SQLITE,
    BACKEND_MYSQL_SAVER,
    BACKEND_MYSQL_ALIAS,
    BACKEND_LEGACY,
)
_DEPRECATED_BACKENDS = {BACKEND_MYSQL_ALIAS: BACKEND_MYSQL_SAVER}

_BACKEND_ENV = "EASY_CHECKPOINT_BACKEND"
_SQLITE_PATH_ENV = "EASY_CHECKPOINT_SQLITE_PATH"
_ALLOW_LEGACY_ENV = "EASY_ALLOW_LEGACY_CHECKPOINTER"
_STICKY_ENV = "EASY_STICKY_SESSIONS"
_GC_INTERVAL_ENV = "EASY_CHECKPOINT_GC_INTERVAL"
_AGENT_ENV = "AGENT_ENV"
#: SQLite 路径里的实例名占位符：多副本时每个 pod 一个文件
POD_PLACEHOLDER = "{pod}"
DEFAULT_GC_INTERVAL = 600

#: config.yaml 和环境变量都没配时的路径：每个 pod 一个文件，放在部署目录 /qts 下。
#: 配置里的相对路径按项目根解析，避免依赖进程 cwd（web_runner 会 chdir，测试注入绝对路径）
DEFAULT_SQLITE_PATH = f"/qts/checkpoint/{POD_PLACEHOLDER}.db"
_UNSET: Any = object()


class CheckpointDeploymentError(RuntimeError):
    """checkpointer 的部署配置不成立（启动期 fail-fast）。"""


@dataclass(frozen=True)
class CheckpointSelection:
    """checkpointer 后端解析结果（纯数据，便于单测注入，不打开任何连接）。

    ``per_pod`` 表示 SQLite 路径模板里有 ``{pod}``：``sqlite_path`` 是替换成 ``instance``
    之后的实际路径，``sqlite_template`` 是原始模板。``mysql`` 只在 ``mysql_saver`` 时非空。
    """

    kind: str
    reason: str
    sqlite_path: str = ""
    warning: str = ""
    per_pod: bool = False
    instance: str = ""
    sqlite_template: str = ""
    mysql: MySQLCheckpointConfig | None = None

    @property
    def is_official(self) -> bool:
        """是否"进程级共享、可跨会话复用"的实现（sqlite / mysql_saver 都是）。"""
        return self.kind in (BACKEND_SQLITE, BACKEND_MYSQL_SAVER)


def resolve_sqlite_path(path: str) -> Path:
    """相对路径按项目根解析；``:memory:`` 保持原样交给 sqlite。"""
    if path == ":memory:":
        return Path(path)
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return PROJECT_ROOT / candidate


def _pod_instance_name() -> str:
    """拼进 SQLite 路径的实例名；不能安全地放进路径时启动失败。"""
    name = instance_name()
    if not is_valid_instance_name(name):
        raise CheckpointDeploymentError(
            f"实例名 {name!r} 不能替换 checkpoint 路径里的 {POD_PLACEHOLDER}："
            "只允许字母、数字、. _ -，不能以 . 开头，最长 253 个字符；请设置 EASY_POD_NAME"
        )
    return name


def detect_business_db_type() -> str | None:
    """返回已加载业务库的类型（sqlite/mysql）；不触发建库，避免启动副作用。"""
    try:
        from easy_agent.db import database as _database_module
    except Exception:  # noqa: BLE001 - 数据库模块不可用时按未知处理
        return None
    db = getattr(_database_module, "_db_instance", None)
    db_type = getattr(db, "db_type", None)
    return str(db_type).strip().lower() if db_type else None


def _pick(source: Any, name: str, default: Any = "") -> Any:
    """从 pydantic 模型或 dict 里取一个配置值（两种都支持，单测好写）。"""
    if source is None:
        return default
    if isinstance(source, Mapping):
        value = source.get(name, default)
    else:
        value = getattr(source, name, default)
    return default if value is None else value


def build_mysql_checkpoint_config(
    checkpoint_mysql: Any = None,
    business_mysql: Any = None,
) -> MySQLCheckpointConfig:
    """把 ``checkpoint.mysql`` 与业务库 ``database.mysql`` 合成 saver 的配置。

    连接参数**留空即复用业务库**（同实例同库，用 ``cp_`` 前缀隔离）；显式配了就以它为准，
    这样也可以把 checkpoint 单独放另一个库。
    """
    defaults = MySQLCheckpointConfig()
    host = str(_pick(checkpoint_mysql, "host", "") or "").strip()
    port = int(_pick(checkpoint_mysql, "port", 0) or 0)
    user = str(_pick(checkpoint_mysql, "user", "") or "").strip()
    password = str(_pick(checkpoint_mysql, "password", "") or "")
    database = str(_pick(checkpoint_mysql, "database", "") or "").strip()

    fallback_host = str(_pick(business_mysql, "host", defaults.host) or defaults.host)
    fallback_port = int(_pick(business_mysql, "port", defaults.port) or defaults.port)
    fallback_user = str(_pick(business_mysql, "user", defaults.user) or defaults.user)
    fallback_password = str(_pick(business_mysql, "password", defaults.password) or "")
    fallback_database = str(
        _pick(business_mysql, "database", defaults.database) or defaults.database
    )

    return MySQLCheckpointConfig(
        host=host or fallback_host,
        port=port or fallback_port,
        user=user or fallback_user,
        password=password or fallback_password,
        database=database or fallback_database,
        charset=str(_pick(checkpoint_mysql, "charset", defaults.charset) or "utf8mb4"),
        table_prefix=str(
            _pick(checkpoint_mysql, "table_prefix", defaults.table_prefix)
            or defaults.table_prefix
        ),
        pool_min_size=int(
            _pick(checkpoint_mysql, "pool_min_size", defaults.pool_min_size)
            or defaults.pool_min_size
        ),
        pool_max_size=int(
            _pick(checkpoint_mysql, "pool_max_size", defaults.pool_max_size)
            or defaults.pool_max_size
        ),
        connect_timeout=int(
            _pick(checkpoint_mysql, "connect_timeout", defaults.connect_timeout)
            or defaults.connect_timeout
        ),
        read_timeout=int(
            _pick(checkpoint_mysql, "read_timeout", defaults.read_timeout)
            or defaults.read_timeout
        ),
        auto_setup=bool(_pick(checkpoint_mysql, "auto_setup", defaults.auto_setup)),
        auto_setup_lock_wait=int(
            _pick(
                checkpoint_mysql,
                "auto_setup_lock_wait",
                defaults.auto_setup_lock_wait,
            )
            or defaults.auto_setup_lock_wait
        ),
        gc_interval_seconds=int(
            _pick(checkpoint_mysql, "gc_interval_seconds", defaults.gc_interval_seconds)
            or defaults.gc_interval_seconds
        ),
        record_ttl_days=int(
            _pick(checkpoint_mysql, "record_ttl_days", defaults.record_ttl_days) or 0
        ),
        ttl_basis=str(
            _pick(checkpoint_mysql, "ttl_basis", defaults.ttl_basis)
            or defaults.ttl_basis
        ),
        ttl_only_archived=bool(
            _pick(checkpoint_mysql, "ttl_only_archived", defaults.ttl_only_archived)
        ),
        ttl_batch_size=int(
            _pick(checkpoint_mysql, "ttl_batch_size", defaults.ttl_batch_size)
            or defaults.ttl_batch_size
        ),
        ttl_protect_active_stream=bool(
            _pick(
                checkpoint_mysql,
                "ttl_protect_active_stream",
                defaults.ttl_protect_active_stream,
            )
        ),
    )


def _sqlite_path_template(
    configured: str | None, warnings: list[str]
) -> tuple[str, str]:
    """SQLite 路径模板和它的来源：config.yaml > 旧环境变量 > 默认路径。"""
    configured = (configured or "").strip()
    legacy = env_str(_SQLITE_PATH_ENV).strip()
    if configured:
        if legacy and legacy != configured:
            warnings.append(
                f"环境变量 {_SQLITE_PATH_ENV}={legacy!r} 不再生效："
                "config.yaml 的 checkpoint.sqlite_path 优先，可以删掉这个变量"
            )
        return configured, "config.yaml 的 checkpoint.sqlite_path"
    if legacy:
        return legacy, f"环境变量 {_SQLITE_PATH_ENV}"
    return DEFAULT_SQLITE_PATH, "默认路径"


def resolve_checkpoint_backend(
    *,
    backend: str | None = None,
    sqlite_path: str | None = None,
    db_type: Any = _UNSET,
    mysql_config: Any = None,
    business_mysql: Any = None,
) -> CheckpointSelection:
    """解析 checkpointer 后端（不打开连接）。

    ``sqlite_path`` 是 config.yaml 的 ``checkpoint.sqlite_path``；``mysql_config`` /
    ``business_mysql`` 分别是 ``checkpoint.mysql`` 与 ``database.mysql`` 两段配置
    （pydantic 模型或 dict 都行），留空时连接参数取默认值。

    ``auto`` 的语义（容易写错，这里定死）：**只看配置，不看连通性** —— 业务库是 MySQL 且
    ``checkpoint.mysql.enabled`` 为真就选 ``mysql_saver``，否则 ``sqlite``。不做"连不上就
    退回 sqlite"这种事：那会导致 Pod1 写库、Pod2 写本地文件，比启动失败严重得多。
    """
    raw_backend = (
        (env_str(_BACKEND_ENV) if backend is None else str(backend)).strip().lower()
    )
    warnings: list[str] = []
    if not raw_backend:
        raw_backend = BACKEND_AUTO
    elif raw_backend not in BACKEND_CHOICES:
        warnings.append(
            f"未知的 {_BACKEND_ENV}={raw_backend!r}"
            "（可选 auto/sqlite/mysql_saver/mysql/legacy），按 auto 处理"
        )
        raw_backend = BACKEND_AUTO
    if raw_backend in _DEPRECATED_BACKENDS:
        mapped = _DEPRECATED_BACKENDS[raw_backend]
        warnings.append(
            f"{_BACKEND_ENV}={raw_backend} 已废弃（旧的业务库全量快照实现已删除），"
            f"自动按 {mapped} 处理；请把部署配置改成 {mapped}"
        )
        raw_backend = mapped
    if raw_backend == BACKEND_LEGACY:
        # 用不到 SQLite 文件：不解析路径，也就不校验 {pod} 的实例名
        return CheckpointSelection(
            BACKEND_LEGACY, "EASY_CHECKPOINT_BACKEND=legacy：显式使用进程内 MemorySaver"
        )

    business_db = (
        detect_business_db_type()
        if db_type is _UNSET
        else (str(db_type).strip().lower() if db_type else None)
    )
    mysql_enabled = bool(_pick(mysql_config, "enabled", True))
    mysql_settings = build_mysql_checkpoint_config(mysql_config, business_mysql)

    if raw_backend == BACKEND_MYSQL_SAVER:
        configured_target = bool(
            str(_pick(mysql_config, "host", "") or "").strip()
            or str(_pick(mysql_config, "database", "") or "").strip()
        )
        if business_db != "mysql" and not configured_target:
            raise CheckpointDeploymentError(
                f"EASY_CHECKPOINT_BACKEND={BACKEND_MYSQL_SAVER} 要求业务数据库为 MySQL，"
                f"当前为 {business_db!r}；或显式配置 checkpoint.mysql.host/database"
            )
        return CheckpointSelection(
            BACKEND_MYSQL_SAVER,
            f"EASY_CHECKPOINT_BACKEND={BACKEND_MYSQL_SAVER}：共享 MySQL checkpoint（协议级 4 张表）",
            warning="；".join(warnings),
            mysql=mysql_settings,
        )

    if raw_backend == BACKEND_AUTO and business_db == "mysql" and mysql_enabled:
        return CheckpointSelection(
            BACKEND_MYSQL_SAVER,
            "auto：业务库是 MySQL，使用共享 MySQL checkpoint（协议级 4 张表）",
            warning="；".join(warnings),
            mysql=mysql_settings,
        )

    sqlite, source = _sqlite_path_template(sqlite_path, warnings)
    # 路径模板里的 {pod} 替换成本实例名：多副本时每个 pod 一个文件
    pod_fields: dict[str, Any] = {}
    if POD_PLACEHOLDER in sqlite:
        instance = _pod_instance_name()
        pod_fields = {"per_pod": True, "instance": instance, "sqlite_template": sqlite}
        sqlite = sqlite.replace(POD_PLACEHOLDER, instance)
    # 按 pod 隔离时多副本由启动校验把关，这条单机提示就不必了
    if business_db == "mysql" and not pod_fields and not mysql_enabled:
        warnings.append(
            "业务库为 MySQL 但 checkpoint.mysql.enabled=false，checkpoint 将落在本地 "
            "SQLite 文件，多副本会各写各的（仅适合单机）"
        )

    mode = (
        "EASY_CHECKPOINT_BACKEND=sqlite"
        if raw_backend == BACKEND_SQLITE
        else "auto"
    )
    return CheckpointSelection(
        kind=BACKEND_SQLITE,
        reason=f"{mode}：使用官方 AsyncSqliteSaver，路径来自{source}",
        sqlite_path=sqlite,
        warning="；".join(warnings),
        **pod_fields,
    )


def active_checkpoint_backend() -> str:
    """当前进程实际生效的 checkpointer 后端。"""
    if _mysql_saver is not None:
        return BACKEND_MYSQL_SAVER
    if _sqlite_saver is not None:
        return BACKEND_SQLITE
    return BACKEND_LEGACY


def shared_checkpointer_active() -> bool:
    """进程级共享 saver 是否已打开（sqlite 或 mysql_saver）。

    **这是唯一应该用的判据**。以前各处写 ``sqlite_checkpointer_active()``，语义上是
    "有没有一个进程级共享、可跨会话复用的 saver"，只是当时只有 sqlite 一种实现。多一个
    后端就必须改成这个，否则会话线程、图缓存、手动压缩会**静默退化**。
    """
    return _shared_saver is not None


def sqlite_checkpointer_active() -> bool:
    """兼容旧名字（语义已改为"共享 saver 是否就绪"）。新代码用
    :func:`shared_checkpointer_active`。"""
    return _shared_saver is not None


def mysql_checkpointer_active() -> bool:
    """当前是否用的是新的共享 MySQL saver。"""
    return _mysql_saver is not None


def mysql_checkpoint_config() -> MySQLCheckpointConfig | None:
    """当前生效的 MySQL checkpoint 配置（GC/TTL/运维查询用）；未启用时 None。"""
    return _mysql_saver.config if _mysql_saver is not None else None


def _resolve_agent_env(agent_env: str | None) -> str:
    if agent_env is None:
        return env_str(_AGENT_ENV, "").strip().lower()
    return str(agent_env).strip().lower()


def _resolve_replicas(replicas: int | None) -> int:
    if replicas is not None:
        return int(replicas)
    return detect_replica_count()


def _resolve_workers_per_pod(workers_per_pod: int | None) -> int:
    if workers_per_pod is not None:
        return int(workers_per_pod)
    return detect_workers_per_pod()


def validate_checkpoint_deployment(
    selection: CheckpointSelection,
    *,
    agent_env: str | None = None,
    replicas: int | None = None,
    workers_per_pod: int | None = None,
    sticky: bool | None = None,
    allow_legacy: bool | None = None,
) -> bool:
    """生产多副本的部署校验，不成立时抛 ``CheckpointDeploymentError``。

    返回 True 表示调用方要按 pod 隔离 SQLite，且打开失败时不能回退。

    - **mysql_saver**：状态在库里，任意 Pod / 任意 worker 都能接手，直接放行；
    - **sqlite**：要求路径含 ``{pod}`` + Ingress 粘性会话（回滚目标，规则原样保留）。

    dev/test、单 pod 单 worker 不受影响；``EASY_ALLOW_LEGACY_CHECKPOINTER=1`` 是逃生门。
    """
    if _resolve_agent_env(agent_env) != "prod":
        return False
    replicas = _resolve_replicas(replicas)
    workers_per_pod = _resolve_workers_per_pod(workers_per_pod)
    if replicas <= 1 and workers_per_pod <= 1:
        return False
    if selection.kind == BACKEND_MYSQL_SAVER:
        logger.info(
            "生产环境 %s 个副本、每 pod %s 个 worker：HITL checkpoint 在共享 MySQL 上，"
            "不依赖粘性会话",
            replicas,
            workers_per_pod,
        )
        return False
    allowed = (
        env_bool(_ALLOW_LEGACY_ENV, False)
        if allow_legacy is None
        else bool(allow_legacy)
    )
    if allowed:
        logger.warning(
            "生产环境 %s 个副本、每个 pod %s 个 worker，checkpointer=%s：已通过 %s 显式豁免，"
            "HITL 跨副本恢复不保证一致",
            replicas,
            workers_per_pod,
            selection.kind,
            _ALLOW_LEGACY_ENV,
        )
        return False
    if workers_per_pod > 1:
        raise CheckpointDeploymentError(
            f"生产环境每个 pod 跑了 {workers_per_pod} 个 worker：同一个 pod 的 worker 共用 "
            "checkpoint 文件，审批还可能落到另一个 worker 上；请把 WEB_CONCURRENCY / "
            f"UVICORN_WORKERS 设为 1，或改用 {_BACKEND_ENV}={BACKEND_MYSQL_SAVER}。"
            f"若接受审批恢复不一致，可显式设置 {_ALLOW_LEGACY_ENV}=1（默认关闭）。"
        )
    if selection.kind != BACKEND_SQLITE:
        raise CheckpointDeploymentError(
            f"生产环境检测到 {replicas} 个副本，但 HITL checkpointer 是 "
            f"{selection.kind!r}（进程内，不能跨副本共享）。请设置 {_BACKEND_ENV}="
            f"{BACKEND_MYSQL_SAVER} 使用共享 MySQL，或使用每个 pod 独立的 SQLite 文件"
            f"（路径带 {POD_PLACEHOLDER}）并配置粘性会话。"
            f"若接受审批跨副本恢复不一致，可显式设置 {_ALLOW_LEGACY_ENV}=1（默认关闭）。"
        )
    if not selection.per_pod:
        raise CheckpointDeploymentError(
            f"生产环境检测到 {replicas} 个副本，但 checkpoint 路径 "
            f"{selection.sqlite_path} 里没有 {POD_PLACEHOLDER}："
            "多个 pod 不能共用一个 SQLite 文件（WAL 不能跨主机，网络盘上的文件锁也不可靠）。"
            f"请把 config.yaml 的 checkpoint.sqlite_path 改成 {DEFAULT_SQLITE_PATH} "
            "这类每个 pod 一个文件的路径。"
        )
    if not (env_bool(_STICKY_ENV, False) if sticky is None else bool(sticky)):
        raise CheckpointDeploymentError(
            f"生产环境检测到 {replicas} 个副本，checkpoint 按 pod 隔离，但没有确认粘性会话："
            "审批只能在记录它的 pod 上恢复。请先在 Ingress 配好基于 cookie 的会话保持，"
            f"再设置 {_STICKY_ENV}=1；或改用 {_BACKEND_ENV}={BACKEND_MYSQL_SAVER}。"
        )
    logger.info(
        "生产环境 %s 个副本：HITL checkpoint 按 pod 隔离（%s → %s），依赖 Ingress 粘性会话",
        replicas,
        selection.sqlite_template,
        selection.sqlite_path,
    )
    return True


# ── 打开 / 关闭 ─────────────────────────────────────────────────────
def _clear_legacy_checkpointers() -> None:
    with _CHECKPOINTER_LOCK:
        _CHECKPOINTERS.clear()


async def open_sqlite_checkpointer(path: str):
    """打开官方 AsyncSqliteSaver（dev/test、单机与生产回滚都用它）。"""
    global _sqlite_cm, _sqlite_saver, _shared_saver
    if _sqlite_saver is not None:
        return _sqlite_saver
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    db_path = resolve_sqlite_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _sqlite_cm = AsyncSqliteSaver.from_conn_string(str(db_path))
    _sqlite_saver = await _sqlite_cm.__aenter__()
    await _sqlite_saver.setup()
    # setup() 已开 WAL。WAL 下 NORMAL 不再每次提交都等磁盘落盘（只在 WAL 检查点同步），
    # 单文件压测吞吐约 +70%。进程崩溃不丢数据；断电或系统崩溃时最后一小段提交可能回滚，
    # 库不会损坏，最多是那一刻正在跑的几轮要重试。该设置只对当前连接生效。
    await _sqlite_saver.conn.execute("PRAGMA synchronous=NORMAL")
    _shared_saver = _sqlite_saver
    logger.info("HITL checkpointer: AsyncSqliteSaver | path=%s", db_path)
    return _sqlite_saver


async def close_sqlite_checkpointer() -> None:
    global _sqlite_cm, _sqlite_saver, _shared_saver, _active_selection
    cm = _sqlite_cm
    _sqlite_cm = None
    _sqlite_saver = None
    if _shared_saver is not None and _mysql_saver is None:
        _shared_saver = None
    _active_selection = None
    if cm is None:
        return
    await cm.__aexit__(None, None, None)


async def open_mysql_checkpointer(
    config: MySQLCheckpointConfig,
) -> AsyncMySQLSaver:
    """打开共享 MySQL saver（建池 + 建表/迁移 + 自检）。

    失败时抛 :class:`CheckpointDeploymentError`，**不回退**进程内存储：一半 Pod 写库、
    一半写内存，比启动失败难查一百倍。
    """
    global _mysql_saver, _shared_saver
    if _mysql_saver is not None:
        return _mysql_saver
    saver = AsyncMySQLSaver(config)
    try:
        await saver.asetup()
    except MySQLCheckpointError as exc:
        await saver.aclose()
        raise CheckpointDeploymentError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - 连接失败、权限不足等统一转部署错误
        await saver.aclose()
        raise CheckpointDeploymentError(
            f"共享 MySQL checkpoint 打不开（{config.host}:{config.port}/{config.database}）："
            f"{type(exc).__name__}: {exc}"
        ) from exc
    _mysql_saver = saver
    _shared_saver = saver
    return saver


async def close_mysql_checkpointer() -> None:
    global _mysql_saver, _shared_saver
    saver, _mysql_saver = _mysql_saver, None
    if _shared_saver is saver:
        _shared_saver = None
    if saver is not None:
        await saver.aclose()


async def shutdown_checkpointers() -> None:
    """停掉对账任务、关闭 saver 并清空 legacy 缓存（应用 shutdown 与测试隔离都依赖它）。"""
    await stop_checkpoint_gc()
    await close_mysql_checkpointer()
    await close_sqlite_checkpointer()
    _clear_legacy_checkpointers()


def per_pod_checkpoints_active() -> bool:
    """当前进程的官方 SQLite checkpoint 是否按 pod 隔离（路径里有 ``{pod}``）。

    共享 MySQL 下恒为 False —— 这正是我们要的：审批归属校验
    （``ensure_approval_on_this_instance``）会自动失效。
    """
    return (
        _sqlite_saver is not None
        and _active_selection is not None
        and _active_selection.per_pod
    )


def checkpoint_instance() -> str:
    """checkpoint 所在的实例名：按 pod 隔离时是替换进路径的名字，否则是本实例名。"""
    if _active_selection is not None and _active_selection.instance:
        return _active_selection.instance
    return instance_name()


def _describe_target(selection: CheckpointSelection) -> str:
    if selection.kind == BACKEND_SQLITE:
        if selection.per_pod:
            return (
                f"{selection.sqlite_path}（模板 {selection.sqlite_template}，"
                f"实例 {selection.instance}）"
            )
        return selection.sqlite_path
    if selection.kind == BACKEND_MYSQL_SAVER:
        cfg = selection.mysql
        if cfg is None:
            return "共享 MySQL checkpoint"
        return (
            f"{cfg.host}:{cfg.port}/{cfg.database} 的 {cfg.table_prefix}* 四张表"
            f"（auto_setup={cfg.auto_setup}）"
        )
    return "进程内 MemorySaver（不写业务库）"


async def _open_with_fallback(selection: CheckpointSelection) -> str:
    """单机场景打开官方 SQLite saver；打不开时降级为进程内 saver。

    注意：**只有 sqlite 允许降级**。mysql_saver 一律 fail-fast（见
    :func:`open_mysql_checkpointer`），否则会出现"一半 Pod 写库、一半写内存"。
    """
    if selection.kind == BACKEND_SQLITE:
        try:
            await open_sqlite_checkpointer(selection.sqlite_path)
            return BACKEND_SQLITE
        except Exception as exc:  # noqa: BLE001 - 单机降级不能阻断启动
            logger.error(
                "官方 sqlite checkpointer 启用失败，降级为进程内 MemorySaver | %s: %s",
                type(exc).__name__,
                exc,
            )
    logger.warning(
        "HITL checkpointer: MemorySaver（进程内，重启即丢；HITL 审批无法跨重启恢复）"
    )
    return BACKEND_LEGACY


async def start_checkpoint_backend(
    selection: CheckpointSelection | None = None,
    *,
    sqlite_path: str | None = None,
    mysql_config: Any = None,
    business_mysql: Any = None,
    agent_env: str | None = None,
    replicas: int | None = None,
    workers_per_pod: int | None = None,
    sticky: bool | None = None,
    allow_legacy: bool | None = None,
) -> str:
    """按解析结果打开 checkpointer，返回实际生效的后端名。

    部署形态在打开之前校验（见 :func:`validate_checkpoint_deployment`）。
    ``mysql_saver`` 打开失败直接抛 ``CheckpointDeploymentError``；单 pod 的 ``sqlite``
    打不开会降级为进程内 saver。
    """
    global _active_selection
    if selection is None:
        selection = resolve_checkpoint_backend(
            sqlite_path=sqlite_path,
            mysql_config=mysql_config,
            business_mysql=business_mysql,
        )
    _clear_legacy_checkpointers()
    if selection.warning:
        logger.warning("HITL checkpointer 配置 | %s", selection.warning)
    logger.info(
        "HITL checkpointer 选择 | backend=%s | 目标: %s | 原因: %s",
        selection.kind,
        _describe_target(selection),
        selection.reason,
    )
    per_pod_cluster = validate_checkpoint_deployment(
        selection,
        agent_env=agent_env,
        replicas=replicas,
        workers_per_pod=workers_per_pod,
        sticky=sticky,
        allow_legacy=allow_legacy,
    )
    if selection.kind == BACKEND_MYSQL_SAVER:
        if selection.mysql is None:
            raise CheckpointDeploymentError("mysql_saver 后端缺少连接配置")
        try:
            await open_mysql_checkpointer(selection.mysql)
        except CheckpointDeploymentError:
            raise
        except Exception as exc:  # noqa: BLE001 - 任何异常都不能降级成进程内 saver
            raise CheckpointDeploymentError(
                f"共享 MySQL checkpoint 打开失败，不回退本地 saver："
                f"{type(exc).__name__}: {exc}"
            ) from exc
        _active_selection = None
        opened = BACKEND_MYSQL_SAVER
    elif per_pod_cluster:
        # 多副本只靠每个 pod 自己的 SQLite 文件：打不开直接失败，不回退
        try:
            await open_sqlite_checkpointer(selection.sqlite_path)
        except Exception as exc:  # noqa: BLE001 - 统一转成部署错误，app 启动会因此失败
            raise CheckpointDeploymentError(
                f"多副本模式下官方 SQLite checkpointer 打不开"
                f"（{selection.sqlite_path}），不回退本地 saver：{type(exc).__name__}: {exc}"
            ) from exc
        opened = BACKEND_SQLITE
    else:
        opened = await _open_with_fallback(selection)
    _active_selection = selection if opened == BACKEND_SQLITE else None
    return opened


# ── 删除会话后的对账：补删"会话已不存在"的 thread ─────────────────────
async def _list_thread_ids(saver) -> list[str]:
    """SQLite 文件里的全部 thread_id（直接查官方 saver 的表，不遍历 checkpoint）。"""
    async with saver.lock, saver.conn.execute(
        "SELECT DISTINCT thread_id FROM checkpoints"
    ) as cursor:
        rows = await cursor.fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


def _resolve_db(db=None):
    """取业务库 facade：调用方没传时回落到单例。

    GC 的两个阶段（对账、TTL）都要查会话表，但 ``get_database()`` 在 ``easy_agent/db/``
    之外是「只减不增」的架构计数项（DW212），所以整个模块只保留这一个出口。
    """
    if db is not None:
        return db
    from easy_agent.db import get_database

    return get_database()


async def prune_deleted_session_threads(db=None) -> int:
    """删掉「会话已不存在」的 thread，返回删除个数。

    删除会话时只有处理请求的进程能当场删（:func:`delete_session_threads`）；多副本下其余
    残留由这里补删。

    - sqlite：读本 Pod 文件里的 thread 列表，回查 ``sessions``；
    - mysql_saver：**一条库内 JOIN**（``session_id`` 冗余列 + ``idx_session``），
      不再把几十万 thread 拉到应用层解析。
    """
    if _mysql_saver is not None:
        return await _prune_orphans_mysql()
    saver = _sqlite_saver
    if saver is None:
        return 0

    by_session: dict[str, list[str]] = {}
    for thread_id in await _list_thread_ids(saver):
        session_id = session_id_of_thread(thread_id)
        if session_id:
            by_session.setdefault(session_id, []).append(thread_id)
    if not by_session:
        return 0

    if db is None:
        db = _resolve_db()
    try:
        existing = await run_blocking(
            sessions_repo.existing_session_ids, db, list(by_session)
        )
    except Exception as exc:  # noqa: BLE001 - 查不到就不删，下一轮再来
        logger.warning("checkpoint 对账：查询会话失败，本轮跳过 | %s", exc)
        return 0

    deleted = 0
    for session_id, thread_ids in by_session.items():
        if session_id in existing:
            continue
        for thread_id in thread_ids:
            if _sqlite_saver is not saver:  # 中途关闭了 saver（应用 shutdown）
                return deleted
            try:
                await saver.adelete_thread(thread_id)
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "checkpoint 对账：删除 thread 失败 | thread=%s | %s", thread_id, exc
                )
            # 每删一个让出一次事件循环：saver 的锁也是进行中的对话写 checkpoint 要用的
            await asyncio.sleep(0)
    if deleted:
        logger.info("checkpoint 对账：删除 %s 个已删除会话的 thread", deleted)
    return deleted


async def _prune_orphans_mysql() -> int:
    saver = _mysql_saver
    if saver is None:
        return 0
    try:
        thread_ids = await saver.list_orphan_threads(limit=500)
    except Exception as exc:  # noqa: BLE001 - 查不到就不删，下一轮再来
        logger.warning("checkpoint 对账：查询孤儿 thread 失败，本轮跳过 | %s", exc)
        return 0
    deleted = 0
    for thread_id in thread_ids:
        if _mysql_saver is not saver:
            break
        try:
            await saver.adelete_thread(thread_id)
            deleted += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "checkpoint 对账：删除 thread 失败 | thread=%s | session=%s | %s",
                thread_id,
                session_id_of_thread(thread_id) or "-",
                exc,
            )
        await asyncio.sleep(0)
    if deleted:
        logger.info("checkpoint 对账：删除 %s 个已删除会话的 thread", deleted)
    return deleted


def _ttl_cutoff(days: int) -> str:
    """TTL 的截止时间串。

    ``sessions.updated_at`` 是 ``datetime.now().isoformat()`` 写进去的**本地时间的 ISO
    串**（``VARCHAR(50)``），所以 cutoff 必须用同样的格式生成，否则字符串比较会错。这是
    已知风险 R22：一旦写入格式变成带 ``Z`` 或时区偏移，比较会失效 → 启动期探针（见
    ``_probe_sessions_updated_at``）会先验一遍。
    """
    return (datetime.now() - timedelta(days=int(days))).isoformat()


async def _probe_sessions_updated_at(db) -> bool:
    """TTL 前的探针：确认 ``sessions.updated_at`` 仍是可比较的 ISO 串。

    取一条真实行按固定格式解析；解析失败说明格式变了，此时 TTL 必须降级为
    ``checkpoint_created_at``，否则会误删或永不删（R22）。
    """
    try:
        def _read_one():
            with db.get_connection() as conn:
                cursor = conn.cursor()
                db._execute(cursor, "SELECT updated_at FROM sessions LIMIT 1", ())
                return cursor.fetchone()

        row = await run_blocking(_read_one)
    except Exception as exc:  # noqa: BLE001 - 探针失败就当不可用，宁可不删
        logger.warning("checkpoint TTL：updated_at 探针失败，本轮跳过 | %s", exc)
        return False
    if not row:
        return True
    value = (
        row.get("updated_at") if isinstance(row, dict) else row[0]
    ) or ""
    try:
        datetime.fromisoformat(str(value))
    except ValueError:
        logger.error(
            "checkpoint TTL：sessions.updated_at 不再是可解析的 ISO 串（示例 %r），"
            "本轮降级为 checkpoint_created_at 判定",
            value,
        )
        return False
    return True


async def prune_expired_session_threads(db=None) -> int:
    """TTL：把「很久没人用」的会话对应 checkpoint **整条 thread** 删掉。

    默认关闭（``record_ttl_days=0``）。开启后三重保护缺一不可：
    ① 有活跃流锁的会话跳过；② 优先只清理 ``archived=1``；③ 按整 thread 删，绝不删中间行。
    只让一个 Pod 干活（``DistributedLock``）；抢不到锁就本轮跳过。
    """
    saver = _mysql_saver
    cfg = saver.config if saver is not None else None
    if saver is None or cfg is None or int(cfg.record_ttl_days) <= 0:
        return 0

    if db is None:
        db = _resolve_db()

    handle = None
    try:
        from easy_agent.persistence.distributed_lock import get_distributed_lock

        handle = await run_blocking(
            get_distributed_lock().acquire, "checkpoint-ttl", 300.0, 0.0
        )
        if handle is not None and not handle.acquired:
            return 0
    except Exception as exc:  # noqa: BLE001 - 锁不可用时退化为每个 Pod 都跑（DELETE 幂等）
        logger.warning("checkpoint TTL：抢锁失败，本轮按单实例处理 | %s", exc)

    try:
        basis = cfg.ttl_basis
        if basis == "session_updated_at" and not await _probe_sessions_updated_at(db):
            basis = "checkpoint_created_at"
        cutoff = _ttl_cutoff(cfg.record_ttl_days)
        try:
            thread_ids = await saver.list_expired_threads(
                cutoff=cutoff,
                basis=basis,
                only_archived=bool(cfg.ttl_only_archived),
                protect_active_stream=bool(cfg.ttl_protect_active_stream),
                limit=int(cfg.ttl_batch_size),
            )
        except Exception as exc:  # noqa: BLE001 - 查询失败本轮跳过
            logger.warning("checkpoint TTL：查询过期会话失败，本轮跳过 | %s", exc)
            return 0
        deleted = 0
        for thread_id in thread_ids:
            try:
                await saver.adelete_thread(thread_id)
                deleted += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "checkpoint TTL：删除 thread 失败 | thread=%s | %s", thread_id, exc
                )
            await asyncio.sleep(0)
        if deleted:
            logger.info(
                "checkpoint TTL：删除 %s 个过期会话的 thread | basis=%s | cutoff=%s",
                deleted,
                basis,
                cutoff,
            )
        return deleted
    finally:
        if handle is not None:
            try:
                await run_blocking(handle.release)
            except Exception as exc:  # noqa: BLE001 - 释放失败由 TTL 到期兜底
                logger.debug("checkpoint TTL：锁释放失败，等租约到期 | %s", exc)


async def _checkpoint_gc_loop(interval: int, db) -> None:
    while True:
        try:
            await prune_deleted_session_threads(db)
        except Exception as exc:  # noqa: BLE001 - 对账失败不能拖垮应用，下一轮重试
            logger.warning("checkpoint 对账失败 | %s", exc)
        try:
            await prune_expired_session_threads(db)
        except Exception as exc:  # noqa: BLE001 - TTL 失败同样只记日志
            logger.warning("checkpoint TTL 失败 | %s", exc)
        if _mysql_saver is not None:
            try:
                await _mysql_saver.set_last_gc_at(
                    datetime.now(UTC).isoformat(timespec="seconds")
                )
            except Exception as exc:  # noqa: BLE001 - 只是运维字段
                logger.debug("checkpoint GC：写 last_gc_at 失败 | %s", exc)
        await asyncio.sleep(interval)


def start_checkpoint_gc(*, interval: int | None = None, db=None) -> asyncio.Task | None:
    """启动后台对账任务（共享 saver 激活时才启动），启动时先跑一轮。

    间隔优先取 ``EASY_CHECKPOINT_GC_INTERVAL``，没配时用 ``checkpoint.mysql.
    gc_interval_seconds``；不大于 0 时关闭（对账与 TTL 一起关）。
    """
    global _gc_task
    if _shared_saver is None:
        return None
    if _gc_task is not None and not _gc_task.done():
        return _gc_task
    if interval is None:
        env_value = env_int(_GC_INTERVAL_ENV, -1)
        if env_value >= 0:
            seconds = env_value
        elif _mysql_saver is not None:
            seconds = int(_mysql_saver.config.gc_interval_seconds)
        else:
            seconds = DEFAULT_GC_INTERVAL
    else:
        seconds = int(interval)
    if seconds <= 0:
        logger.info("checkpoint 对账已关闭（间隔=%s 秒）", seconds)
        return None
    _gc_task = asyncio.create_task(
        _checkpoint_gc_loop(seconds, db), name="checkpoint-gc"
    )
    return _gc_task


async def stop_checkpoint_gc() -> None:
    global _gc_task
    task, _gc_task = _gc_task, None
    if task is None:
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def get_session_checkpointer(session_id: str):
    """共享 saver 激活时返回它；legacy 后端按 session 给一个进程内 MemorySaver。"""
    if _shared_saver is not None:
        return _shared_saver
    with _CHECKPOINTER_LOCK:
        checkpointer = _CHECKPOINTERS.get(session_id)
        if checkpointer is None:
            if len(_CHECKPOINTERS) >= _CHECKPOINTER_MAX:
                _CHECKPOINTERS.pop(next(iter(_CHECKPOINTERS)), None)
            checkpointer = MemorySaver()
            _CHECKPOINTERS[session_id] = checkpointer
        return checkpointer


def release_session_checkpointer(session_id: str) -> None:
    with _CHECKPOINTER_LOCK:
        _CHECKPOINTERS.pop(session_id, None)


async def adelete_threads(thread_ids) -> int:
    """从共享 saver 删除这些 thread，返回成功删除的个数。

    saver 全局共享，删除会话不会顺带删掉它的 thread，只能按 thread_id 显式删除。legacy
    按会话存放，由 :func:`release_session_checkpointer` 整体清理，这里不处理。
    """
    saver = _shared_saver
    if saver is None:
        return 0
    deleted = 0
    for thread_id in dict.fromkeys(t for t in thread_ids if t):
        try:
            await saver.adelete_thread(thread_id)
            deleted += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "删除 checkpoint thread 失败 | thread=%s | session=%s | %s",
                thread_id,
                session_id_of_thread(thread_id) or "-",
                exc,
            )
    return deleted


async def adelete_agent_thread(agent, thread_id: str) -> bool:
    """用 agent 编译图自己的 checkpointer 删除一个 thread（共享 saver 与 legacy 都适用）。"""
    checkpointer = getattr(getattr(agent, "agent", None), "checkpointer", None)
    if checkpointer is None or not thread_id:
        return False
    try:
        await checkpointer.adelete_thread(thread_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "删除 checkpoint thread 失败 | thread=%s | session=%s | %s",
            thread_id,
            session_id_of_thread(thread_id) or "-",
            exc,
        )
        return False


async def delete_session_threads(session_id: str, message_ids=()) -> int:
    """删除会话的全部 thread：会话 thread 与每条消息一个的旧 thread（DA-03）。

    ``message_ids`` 来自该会话的 ``agent_runs``；旧 thread 名为 ``{session_id}-{message_id}``。
    """
    deleted = await adelete_threads(
        [session_id, *(f"{session_id}-{mid}" for mid in message_ids if mid)]
    )
    release_session_checkpointer(session_id)
    return deleted
