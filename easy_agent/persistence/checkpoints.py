"""Durable HITL checkpointer：官方 AsyncSqliteSaver 或自研业务库快照。

FW-09：由 ``EASY_CHECKPOINT_BACKEND=auto|sqlite|legacy`` 控制，选择规则集中在
``resolve_checkpoint_backend()``：配置了 ``EASY_CHECKPOINT_SQLITE_PATH`` 或业务库为
SQLite 时用官方 SQLite saver（默认 ``data/checkpoints.db``）。业务库不再保存
checkpoint。显式 ``legacy`` 或 MySQL 未配 SQLite 路径时，只用进程内
``PersistentMemorySaver``，重启后审批状态不在。

生产只有 MySQL，Postgres checkpointer 已移除。两种后端都不能在副本之间共享，多副本时每个 pod
用自己的 SQLite 文件：路径里写 ``{pod}``（启动时替换成实例名），由 Ingress 粘性会话把同一用户的
请求固定到同一个 pod（``EASY_STICKY_SESSIONS=1`` 确认已配好），删除会话时别的 pod 里的残留由后台
对账补删。其余多副本组合在启动期 fail-fast；显式设置 ``EASY_ALLOW_LEGACY_CHECKPOINTER=1``（默认
关闭）才放行，此时 HITL 跨副本恢复不保证一致。见 ``docs/design/2026-10-21-per-pod-sqlite-checkpoints.md``。
"""

from __future__ import annotations

import asyncio
import logging
import pickle
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from easy_agent.bootstrap.env import env_bool, env_int, env_str
from easy_agent.utils.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

_CHECKPOINTERS: dict[str, "PersistentMemorySaver"] = {}
_CHECKPOINTER_MAX = 200
# 持久化裁剪：MemorySaver 会按 thread 保留全部 checkpoint。thread_id 形如
# "{session_id}-{message_id}"，每轮新增一个 thread 且永不裁剪 → 每轮全量 pickle
# 写库、blob 大小 O(轮数²)。这里只保留最近若干 thread：HITL 只会恢复最新未完成
# thread，历史 thread 不再需要。
#
# thread 内部的 checkpoint 一个都不能删：deepagents 0.7 的 messages 是 DeltaChannel，
# checkpoint 里只存增量，读状态时要沿父链回溯到最近的快照再重放各祖先的写入。
# 早先按 thread 只留最近 2 个 checkpoint，运行刚结束 messages 就重建成空列表，
# HITL 恢复时模型看不到对话（DA-03 P1 发现）。
_PERSIST_MAX_THREADS = 3
_CHECKPOINTER_LOCK = threading.Lock()
_sqlite_cm = None
_sqlite_saver = None
# 打开官方 SQLite saver 时用的解析结果：按 pod 隔离（路径含 {pod}）时记下实例名
_active_selection: "CheckpointSelection | None" = None
_gc_task: asyncio.Task | None = None


# ── FW-09：checkpointer 后端选择（auto/sqlite/legacy） ──────────────────────
# auto（默认）：配了 SQLite 路径或业务库为 SQLite → 官方 AsyncSqliteSaver；
# 业务库为 MySQL（官方无 saver）→ 回退自研 PersistentMemorySaver。
# EASY_CHECKPOINT_BACKEND 可强制指定后端。
BACKEND_AUTO = "auto"
BACKEND_SQLITE = "sqlite"
BACKEND_LEGACY = "legacy"
BACKEND_CHOICES = (BACKEND_AUTO, BACKEND_SQLITE, BACKEND_LEGACY)

_BACKEND_ENV = "EASY_CHECKPOINT_BACKEND"
_SQLITE_PATH_ENV = "EASY_CHECKPOINT_SQLITE_PATH"
_ALLOW_LEGACY_ENV = "EASY_ALLOW_LEGACY_CHECKPOINTER"
_STICKY_ENV = "EASY_STICKY_SESSIONS"
_GC_INTERVAL_ENV = "EASY_CHECKPOINT_GC_INTERVAL"
_AGENT_ENV = "AGENT_ENV"
#: SQLite 路径里的实例名占位符：多副本时每个 pod 一个文件
POD_PLACEHOLDER = "{pod}"
DEFAULT_GC_INTERVAL = 600

# 相对路径按项目根解析，避免依赖进程 cwd（web_runner 会 chdir，测试注入绝对路径）
DEFAULT_SQLITE_PATH = "data/checkpoints.db"
_UNSET: Any = object()


class CheckpointDeploymentError(RuntimeError):
    """checkpointer 的部署配置不成立（启动期 fail-fast）。"""


@dataclass(frozen=True)
class CheckpointSelection:
    """checkpointer 后端解析结果（纯数据，便于单测注入，不打开任何连接）。

    ``per_pod`` 表示 SQLite 路径模板里有 ``{pod}``：``sqlite_path`` 是替换成 ``instance``
    之后的实际路径，``sqlite_template`` 是原始模板。
    """

    kind: str
    reason: str
    sqlite_path: str = ""
    warning: str = ""
    per_pod: bool = False
    instance: str = ""
    sqlite_template: str = ""

    @property
    def is_official(self) -> bool:
        return self.kind == BACKEND_SQLITE


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
    # 延迟导入：utils 包会拉起较重依赖，checkpoints 可能先于 app 初始化被导入。
    from easy_agent.utils.deployment import instance_name, is_valid_instance_name

    name = instance_name()
    if not is_valid_instance_name(name):
        raise CheckpointDeploymentError(
            f"实例名 {name!r} 不能用于 {_SQLITE_PATH_ENV} 里的 {POD_PLACEHOLDER}："
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


def resolve_checkpoint_backend(
    *,
    backend: str | None = None,
    sqlite_path: str | None = None,
    db_type: Any = _UNSET,
) -> CheckpointSelection:
    """解析 checkpointer 后端（不打开连接）。

    所有入参都可显式注入：``None`` 表示读环境变量；``db_type`` 保持 ``_UNSET``
    时读取已加载的业务库实例。单测依赖这一点，无需真实 MySQL。
    """
    raw_backend = (env_str(_BACKEND_ENV) if backend is None else str(backend)).strip().lower()
    warnings: list[str] = []
    if not raw_backend:
        raw_backend = BACKEND_AUTO
    elif raw_backend not in BACKEND_CHOICES:
        warnings.append(
            f"未知的 {_BACKEND_ENV}={raw_backend!r}"
            "（可选 auto/sqlite/legacy），按 auto 处理"
        )
        raw_backend = BACKEND_AUTO

    sqlite = (
        env_str(_SQLITE_PATH_ENV) if sqlite_path is None else str(sqlite_path)
    ).strip()
    # 路径模板里的 {pod} 替换成本实例名：多副本时每个 pod 一个文件
    pod_fields: dict[str, Any] = {}
    if POD_PLACEHOLDER in sqlite:
        instance = _pod_instance_name()
        pod_fields = {"per_pod": True, "instance": instance, "sqlite_template": sqlite}
        sqlite = sqlite.replace(POD_PLACEHOLDER, instance)
    if db_type is _UNSET:
        business_db = detect_business_db_type()
    else:
        business_db = str(db_type).strip().lower() if db_type else None

    def result(
        kind: str,
        reason: str,
        *,
        sqlite_value: str = "",
        extra: str = "",
    ) -> CheckpointSelection:
        messages = [*warnings]
        if extra:
            messages.append(extra)
        return CheckpointSelection(
            kind=kind,
            reason=reason,
            sqlite_path=sqlite_value,
            warning="；".join(messages),
            **(pod_fields if sqlite_value and sqlite_value == sqlite else {}),
        )

    if raw_backend == BACKEND_LEGACY:
        return result(BACKEND_LEGACY, "EASY_CHECKPOINT_BACKEND=legacy：显式使用自研业务库快照")

    # 按 pod 隔离时多副本由启动校验把关，这条单机提示就不必了
    local_only_hint = "" if pod_fields else (
        "业务库为 MySQL，checkpoint 将落在本地 SQLite 文件，"
        "多副本会各写各的（仅适合单机）"
    )

    if raw_backend == BACKEND_SQLITE:
        return result(
            BACKEND_SQLITE,
            "EASY_CHECKPOINT_BACKEND=sqlite：使用官方 AsyncSqliteSaver",
            sqlite_value=sqlite or DEFAULT_SQLITE_PATH,
            extra=local_only_hint if business_db == "mysql" else "",
        )

    # auto
    if sqlite:
        return result(
            BACKEND_SQLITE,
            "已配置 EASY_CHECKPOINT_SQLITE_PATH：使用官方 AsyncSqliteSaver",
            sqlite_value=sqlite,
            extra=local_only_hint if business_db == "mysql" else "",
        )
    if business_db == "sqlite":
        return result(
            BACKEND_SQLITE,
            "业务库为 SQLite：默认使用官方 AsyncSqliteSaver",
            sqlite_value=DEFAULT_SQLITE_PATH,
        )
    if business_db == "mysql":
        return result(
            BACKEND_LEGACY,
            "业务库为 MySQL 且未配置 SQLite 路径：回退自研业务库快照",
            extra=(
                "官方 LangGraph 没有 MySQL checkpointer，HITL 回退到自研 "
                "PersistentMemorySaver 快照：功能可用，但每轮全量 pickle 写业务库、"
                "非官方实现，高并发/长会话性能较差；建议配置 EASY_CHECKPOINT_SQLITE_PATH"
            ),
        )
    return result(
        BACKEND_LEGACY,
        "未识别业务库类型且未配置 checkpoint 路径：回退自研业务库快照",
        extra=(
            "未能确定业务库类型（数据库可能尚未初始化），且未配置 "
            "EASY_CHECKPOINT_SQLITE_PATH，HITL 使用自研 MemorySaver 业务库快照"
        ),
    )


def active_checkpoint_backend() -> str:
    """当前进程实际生效的 checkpointer 后端。"""
    if _sqlite_saver is not None:
        return BACKEND_SQLITE
    return BACKEND_LEGACY


def _resolve_agent_env(agent_env: str | None) -> str:
    if agent_env is None:
        return env_str(_AGENT_ENV, "").strip().lower()
    return str(agent_env).strip().lower()


def _resolve_replicas(replicas: int | None) -> int:
    if replicas is not None:
        return int(replicas)
    # 延迟导入：utils 包会拉起较重依赖，checkpoints 可能先于 app 初始化被导入。
    from easy_agent.utils.deployment import detect_replica_count

    return detect_replica_count()


def _resolve_workers_per_pod(workers_per_pod: int | None) -> int:
    if workers_per_pod is not None:
        return int(workers_per_pod)
    from easy_agent.utils.deployment import detect_workers_per_pod

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

    只放行一种多副本组合：官方 SQLite saver、路径含 ``{pod}``（每个 pod 一个文件）、
    ``EASY_STICKY_SESSIONS=1``（Ingress 已配粘性会话）、每个 pod 一个 worker。返回 True 表示
    按这种组合放行：调用方打开 SQLite 失败时不能回退业务库快照。

    dev/test、单 pod 单 worker 不受影响；``EASY_ALLOW_LEGACY_CHECKPOINTER=1`` 是逃生门（放行并
    告警）。各入参 ``None`` 时读环境变量，显式传值用于单测。
    """
    if _resolve_agent_env(agent_env) != "prod":
        return False
    replicas = _resolve_replicas(replicas)
    workers_per_pod = _resolve_workers_per_pod(workers_per_pod)
    if replicas <= 1 and workers_per_pod <= 1:
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
            f"生产环境每个 pod 跑了 {workers_per_pod} 个 worker：同一个 pod 的 worker 共用 checkpoint 文件，"
            "审批还可能落到另一个 worker 上；请把 WEB_CONCURRENCY / UVICORN_WORKERS 设为 1，用多个 pod 扩容。"
            f"若接受审批恢复不一致，可显式设置 {_ALLOW_LEGACY_ENV}=1（默认关闭）。"
        )
    if selection.kind != BACKEND_SQLITE:
        raise CheckpointDeploymentError(
            f"生产环境检测到 {replicas} 个副本，但 HITL checkpointer 是 {selection.kind!r}（业务库快照），"
            "不能跨副本共享。多副本请用每个 pod 独立的官方 SQLite 文件：设置 "
            f"{_SQLITE_PATH_ENV}=data/checkpoints/{POD_PLACEHOLDER}.db，在 Ingress 配好粘性会话后再设置 "
            f"{_STICKY_ENV}=1。若接受审批跨副本恢复不一致，可显式设置 {_ALLOW_LEGACY_ENV}=1（默认关闭）。"
        )
    if not selection.per_pod:
        raise CheckpointDeploymentError(
            f"生产环境检测到 {replicas} 个副本，但 {_SQLITE_PATH_ENV} 里没有 {POD_PLACEHOLDER}："
            "多个 pod 不能共用一个 SQLite 文件（WAL 不能跨主机，网络盘上的文件锁也不可靠）。"
            f"请改成 data/checkpoints/{POD_PLACEHOLDER}.db 这类每个 pod 一个文件的路径。"
        )
    if not (env_bool(_STICKY_ENV, False) if sticky is None else bool(sticky)):
        raise CheckpointDeploymentError(
            f"生产环境检测到 {replicas} 个副本，checkpoint 按 pod 隔离，但没有确认粘性会话："
            "审批只能在记录它的 pod 上恢复。请先在 Ingress 配好基于 cookie 的会话保持，"
            f"再设置 {_STICKY_ENV}=1。"
        )
    logger.info(
        "生产环境 %s 个副本：HITL checkpoint 按 pod 隔离（%s → %s），依赖 Ingress 粘性会话",
        replicas,
        selection.sqlite_template,
        selection.sqlite_path,
    )
    return True


def _clear_legacy_checkpointers() -> None:
    with _CHECKPOINTER_LOCK:
        _CHECKPOINTERS.clear()


class PersistentMemorySaver(MemorySaver):
    """MemorySaver：传入带 ``load/save_agent_checkpoint`` 的存储对象时，每次写入后把快照存进去。

    业务库已不再保存 checkpoint，:func:`get_session_checkpointer` 不传存储，legacy 回退只用进程内
    状态（重启后审批状态不在）。写入后仍按 ``_PERSIST_MAX_THREADS`` 裁剪。
    """

    def __init__(self, session_id: str, db=None):
        super().__init__()
        self._session_id = session_id
        self._db = db
        self._hydrate()

    def _hydrate(self) -> None:
        if self._db is None:
            return
        blob = self._db.load_agent_checkpoint(self._session_id)
        if not blob:
            return
        try:
            payload = pickle.loads(blob)
        except Exception as exc:  # noqa: BLE001
            logger.warning("回放 checkpointer 失败 | session=%s | %s", self._session_id, exc)
            return
        for attr in ("storage", "writes", "blobs"):
            value = payload.get(attr)
            if value is not None and hasattr(self, attr):
                current = getattr(self, attr)
                if hasattr(current, "clear") and hasattr(current, "update"):
                    current.clear()
                    current.update(value)
                else:
                    setattr(self, attr, value)

    @staticmethod
    def _plain_mapping(value: Any) -> Any:
        """Drop defaultdict factories so MemorySaver state can be pickled."""
        if isinstance(value, Mapping):
            return {k: PersistentMemorySaver._plain_mapping(v) for k, v in value.items()}
        return value

    def _prune_for_persistence(self) -> None:
        """裁剪内存态 checkpoint，避免全量 pickle 无限增长：只保留最近 M 个 thread。

        按 thread 的最新 checkpoint id 排序（LangGraph 使用时间有序的 UUID6），同步清理
        writes/blobs 中属于被删 thread 的条目。thread 内的 checkpoint 全部保留（见模块顶部
        ``_PERSIST_MAX_THREADS`` 的说明）。
        """
        try:
            if len(self.storage) <= _PERSIST_MAX_THREADS:
                return

            def _latest(thread_id: str) -> str:
                ids = [
                    cid
                    for ns_map in self.storage.get(thread_id, {}).values()
                    if isinstance(ns_map, dict)
                    for cid in ns_map
                    if isinstance(cid, str)
                ]
                return max(ids) if ids else ""

            keep = set(sorted(self.storage, key=_latest)[-_PERSIST_MAX_THREADS:])
            for thread_id in list(self.storage):
                if thread_id not in keep:
                    self.storage.pop(thread_id, None)
            for key in list(self.writes):
                if key[0] not in keep:
                    self.writes.pop(key, None)
            for key in list(self.blobs):
                if key[0] not in keep:
                    self.blobs.pop(key, None)
        except Exception as exc:  # noqa: BLE001 - 裁剪失败不能阻断持久化
            logger.warning("checkpoint 裁剪失败（忽略，继续全量持久化）: %s", exc)

    def _flush(self) -> None:
        if self._db is None:
            return
        self._prune_for_persistence()
        payload: dict[str, Any] = {}
        for attr in ("storage", "writes", "blobs"):
            if hasattr(self, attr):
                payload[attr] = self._plain_mapping(getattr(self, attr))
        try:
            self._db.save_agent_checkpoint(self._session_id, pickle.dumps(payload, protocol=5))
        except Exception as exc:  # noqa: BLE001
            logger.warning("持久化 checkpointer 失败 | session=%s | %s", self._session_id, exc)

    def put(self, *args, **kwargs):
        result = super().put(*args, **kwargs)
        self._flush()
        return result

    def put_writes(self, *args, **kwargs):
        result = super().put_writes(*args, **kwargs)
        self._flush()
        return result

    async def aput(self, *args, **kwargs):
        result = await super().aput(*args, **kwargs)
        self._flush()
        return result

    async def aput_writes(self, *args, **kwargs):
        result = await super().aput_writes(*args, **kwargs)
        self._flush()
        return result

    def delete_thread(self, thread_id: str) -> None:
        super().delete_thread(thread_id)
        self._flush()

    async def adelete_thread(self, thread_id: str) -> None:
        await super().adelete_thread(thread_id)
        self._flush()


def sqlite_checkpointer_active() -> bool:
    return _sqlite_saver is not None


async def open_sqlite_checkpointer(path: str):
    """打开官方 AsyncSqliteSaver（FW-09 单机默认路径）。

    业务库为 SQLite 时 ``auto`` 默认走这里（路径 ``data/checkpoints.db``，可用
    ``EASY_CHECKPOINT_SQLITE_PATH`` 覆盖）；checkpoint 由 sqlite 自身持久化，
    不再每轮 pickle 全量写业务库。
    """
    global _sqlite_cm, _sqlite_saver
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
    logger.info("HITL checkpointer: AsyncSqliteSaver | path=%s", db_path)
    return _sqlite_saver


async def close_sqlite_checkpointer() -> None:
    global _sqlite_cm, _sqlite_saver, _active_selection
    cm = _sqlite_cm
    _sqlite_cm = None
    _sqlite_saver = None
    _active_selection = None
    if cm is None:
        return
    await cm.__aexit__(None, None, None)


async def shutdown_checkpointers() -> None:
    """停掉对账任务、关闭官方 saver 并清空自研缓存（应用 shutdown 与测试隔离都依赖它）。"""
    await stop_checkpoint_gc()
    await close_sqlite_checkpointer()
    _clear_legacy_checkpointers()


def per_pod_checkpoints_active() -> bool:
    """当前进程的官方 SQLite checkpoint 是否按 pod 隔离（路径里有 ``{pod}``）。"""
    return (
        _sqlite_saver is not None
        and _active_selection is not None
        and _active_selection.per_pod
    )


def checkpoint_instance() -> str:
    """checkpoint 所在的实例名：按 pod 隔离时是替换进路径的名字，否则是本实例名。"""
    if _active_selection is not None and _active_selection.instance:
        return _active_selection.instance
    from easy_agent.utils.deployment import instance_name

    return instance_name()


def _describe_target(selection: CheckpointSelection) -> str:
    if selection.kind == BACKEND_SQLITE:
        if selection.per_pod:
            return (
                f"{selection.sqlite_path}（模板 {selection.sqlite_template}，"
                f"实例 {selection.instance}）"
            )
        return selection.sqlite_path
    return "进程内 MemorySaver（不写业务库）"


async def _open_with_fallback(selection: CheckpointSelection) -> str:
    """打开官方 SQLite saver，失败时回退自研业务库快照；返回实际生效的后端名。"""
    if selection.kind == BACKEND_SQLITE:
        try:
            await open_sqlite_checkpointer(selection.sqlite_path)
            return BACKEND_SQLITE
        except Exception as exc:  # noqa: BLE001 - 降级不能阻断启动
            logger.error(
                "官方 sqlite checkpointer 启用失败，回退自研业务库快照 | %s: %s",
                type(exc).__name__,
                exc,
            )
    logger.warning("HITL checkpointer: PersistentMemorySaver（业务库快照）")
    return BACKEND_LEGACY


async def start_checkpoint_backend(
    selection: CheckpointSelection | None = None,
    *,
    agent_env: str | None = None,
    replicas: int | None = None,
    workers_per_pod: int | None = None,
    sticky: bool | None = None,
    allow_legacy: bool | None = None,
) -> str:
    """按解析结果打开 checkpointer，返回实际生效的后端名。

    ``selection`` 为空时读取环境变量解析。部署形态在打开之前校验（见
    :func:`validate_checkpoint_deployment`）。单 pod 时官方 saver 打不开会降级为自研快照；
    按 pod 隔离的多副本模式下打不开直接失败（``CheckpointDeploymentError``），不回退。
    """
    global _active_selection
    if selection is None:
        selection = resolve_checkpoint_backend()
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
    if per_pod_cluster:
        # 多副本只靠每个 pod 自己的 SQLite 文件：业务库快照既同步写库、又不能跨副本一致，不能回退
        try:
            await open_sqlite_checkpointer(selection.sqlite_path)
        except Exception as exc:  # noqa: BLE001 - 统一转成部署错误，app 启动会因此失败
            raise CheckpointDeploymentError(
                f"多副本模式下官方 SQLite checkpointer 打不开（{selection.sqlite_path}），"
                f"不回退业务库快照：{type(exc).__name__}: {exc}"
            ) from exc
        opened = BACKEND_SQLITE
    else:
        opened = await _open_with_fallback(selection)
    _active_selection = selection if opened == BACKEND_SQLITE else None
    return opened


# ── 删除会话后的对账：补删本 pod 文件里「会话已不存在」的 thread ─────────────────
async def _list_thread_ids(saver) -> list[str]:
    """本进程 SQLite 文件里的全部 thread_id（直接查官方 saver 的 checkpoints 表，不遍历 checkpoint）。"""
    async with saver.lock, saver.conn.execute(
        "SELECT DISTINCT thread_id FROM checkpoints"
    ) as cursor:
        rows = await cursor.fetchall()
    return [str(row[0]) for row in rows if row and row[0]]


async def prune_deleted_session_threads(db=None) -> int:
    """删掉本进程 SQLite 文件里「会话已不存在」的 thread，返回删除个数。

    删除会话时只有处理请求的进程能当场删（:func:`delete_session_threads`）；多副本下其余 pod
    文件里的残留由这里补删。只删能认出会话 id、且 ``sessions`` 表里已经没有该会话的 thread；
    查库失败时整轮跳过。
    """
    saver = _sqlite_saver
    if saver is None:
        return 0
    from easy_agent.persistence.thread_ids import session_id_of_thread

    by_session: dict[str, list[str]] = {}
    for thread_id in await _list_thread_ids(saver):
        session_id = session_id_of_thread(thread_id)
        if session_id:
            by_session.setdefault(session_id, []).append(thread_id)
    if not by_session:
        return 0

    from easy_agent.db.repositories import sessions as sessions_repo
    from easy_agent.utils.blocking import run_blocking

    if db is None:
        from easy_agent.db import get_database

        db = get_database()
    try:
        existing = await run_blocking(sessions_repo.existing_session_ids, db, list(by_session))
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
                logger.warning("checkpoint 对账：删除 thread 失败 | thread=%s | %s", thread_id, exc)
            # 每删一个让出一次事件循环：saver 的锁也是进行中的对话写 checkpoint 要用的
            await asyncio.sleep(0)
    if deleted:
        logger.info("checkpoint 对账：删除 %s 个已删除会话的 thread", deleted)
    return deleted


async def _checkpoint_gc_loop(interval: int, db) -> None:
    while True:
        try:
            await prune_deleted_session_threads(db)
        except Exception as exc:  # noqa: BLE001 - 对账失败不能拖垮应用，下一轮重试
            logger.warning("checkpoint 对账失败 | %s", exc)
        await asyncio.sleep(interval)


def start_checkpoint_gc(*, interval: int | None = None, db=None) -> asyncio.Task | None:
    """启动后台对账任务（官方 SQLite saver 激活时才启动），启动时先跑一轮。

    间隔取 ``EASY_CHECKPOINT_GC_INTERVAL``（秒，默认 600），不大于 0 时关闭。
    """
    global _gc_task
    if _sqlite_saver is None:
        return None
    if _gc_task is not None and not _gc_task.done():
        return _gc_task
    seconds = env_int(_GC_INTERVAL_ENV, DEFAULT_GC_INTERVAL) if interval is None else int(interval)
    if seconds <= 0:
        logger.info("checkpoint 对账已关闭（%s=%s）", _GC_INTERVAL_ENV, seconds)
        return None
    _gc_task = asyncio.create_task(_checkpoint_gc_loop(seconds, db), name="checkpoint-gc")
    return _gc_task


async def stop_checkpoint_gc() -> None:
    global _gc_task
    task, _gc_task = _gc_task, None
    if task is None:
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def get_session_checkpointer(session_id: str):
    """官方 SQLite saver 激活时全局共享；否则按 session 给一个进程内 MemorySaver。

    不传业务库：业务库已不存 checkpoint，传进去只会让每次写入白做一遍全量 pickle。
    """
    if _sqlite_saver is not None:
        return _sqlite_saver
    with _CHECKPOINTER_LOCK:
        checkpointer = _CHECKPOINTERS.get(session_id)
        if checkpointer is None:
            if len(_CHECKPOINTERS) >= _CHECKPOINTER_MAX:
                _CHECKPOINTERS.pop(next(iter(_CHECKPOINTERS)), None)
            checkpointer = PersistentMemorySaver(session_id)
            _CHECKPOINTERS[session_id] = checkpointer
        return checkpointer


def release_session_checkpointer(session_id: str) -> None:
    with _CHECKPOINTER_LOCK:
        _CHECKPOINTERS.pop(session_id, None)


async def adelete_threads(thread_ids) -> int:
    """从官方 SQLite saver 删除这些 thread，返回成功删除的个数。

    官方 saver 全局共享，删除会话不会顺带删掉它的 thread，只能按 thread_id 显式删除
    （公开 API ``adelete_thread``）。legacy 快照按会话存放，由
    :func:`release_session_checkpointer` 整体清理，这里不处理。单个失败只记日志。
    """
    saver = _sqlite_saver
    if saver is None:
        return 0
    deleted = 0
    for thread_id in dict.fromkeys(t for t in thread_ids if t):
        try:
            await saver.adelete_thread(thread_id)
            deleted += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("删除 checkpoint thread 失败 | thread=%s | %s", thread_id, exc)
    return deleted


async def adelete_agent_thread(agent, thread_id: str) -> bool:
    """用 agent 编译图自己的 checkpointer 删除一个 thread（官方 saver 与 legacy 快照都适用）。

    失败只记日志，返回是否删除成功。
    """
    checkpointer = getattr(getattr(agent, "agent", None), "checkpointer", None)
    if checkpointer is None or not thread_id:
        return False
    try:
        await checkpointer.adelete_thread(thread_id)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("删除 checkpoint thread 失败 | thread=%s | %s", thread_id, exc)
        return False


async def delete_session_threads(session_id: str, message_ids=()) -> int:
    """删除会话的全部 thread：会话 thread、每条消息一个的旧 thread，以及 legacy 快照（DA-03）。

    ``message_ids`` 来自该会话的 ``agent_runs``；旧 thread 名为 ``{session_id}-{message_id}``。
    """
    deleted = await adelete_threads(
        [session_id, *(f"{session_id}-{mid}" for mid in message_ids if mid)]
    )
    release_session_checkpointer(session_id)
    return deleted
