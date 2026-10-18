"""Durable HITL checkpointer shared across workers / pods.

FW-09：默认使用 LangGraph 官方 saver（``AsyncPostgresSaver`` / ``AsyncSqliteSaver``），
由 ``EASY_CHECKPOINT_BACKEND=auto|postgres|sqlite|legacy`` 控制，选择规则集中在
``resolve_checkpoint_backend()``：Postgres URI 优先；业务库为 SQLite 时默认用官方
SQLite saver（``data/checkpoints.db``）；业务库为 MySQL 时官方没有 saver，回退到
自研 ``PersistentMemorySaver``（把 MemorySaver 状态 pickle 进业务库，多副本 HITL
恢复时按 session_id 回放）。

B1：``AGENT_ENV=prod`` 且检测到多 worker/副本时，checkpointer 必须是 Postgres
（``AsyncPostgresSaver``），否则启动期 fail-fast；只有显式设置
``EASY_ALLOW_LEGACY_CHECKPOINTER=1``（默认关闭）才允许降级。
"""

from __future__ import annotations

import logging
import pickle
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import MemorySaver

from easy_agent.bootstrap.env import env_bool, env_str

logger = logging.getLogger(__name__)

_CHECKPOINTERS: dict[str, "PersistentMemorySaver"] = {}
_CHECKPOINTER_MAX = 200
# 持久化裁剪：MemorySaver 会按 thread 保留全部 checkpoint。thread_id 形如
# "{session_id}-{message_id}"，每轮新增一个 thread 且永不裁剪 → 每轮全量 pickle
# 写库、blob 大小 O(轮数²)。这里只保留最近若干 thread / 每 thread 最近几个
# checkpoint：HITL 只会恢复最新未完成 thread，历史 thread 不再需要。
_PERSIST_MAX_CHECKPOINTS_PER_THREAD = 2
_PERSIST_MAX_THREADS = 3
_CHECKPOINTER_LOCK = threading.Lock()
_postgres_cm = None
_postgres_saver = None
_sqlite_cm = None
_sqlite_saver = None


# ── FW-09：checkpointer 后端选择（auto/postgres/sqlite/legacy） ──────────────
# auto（默认）：Postgres URI → 官方 AsyncPostgresSaver；否则业务库为 SQLite →
# 官方 AsyncSqliteSaver（data/checkpoints.db）；业务库为 MySQL（官方无 saver）→
# 回退自研 PersistentMemorySaver。EASY_CHECKPOINT_BACKEND 可强制指定后端。
BACKEND_AUTO = "auto"
BACKEND_POSTGRES = "postgres"
BACKEND_SQLITE = "sqlite"
BACKEND_LEGACY = "legacy"
BACKEND_CHOICES = (BACKEND_AUTO, BACKEND_POSTGRES, BACKEND_SQLITE, BACKEND_LEGACY)

_BACKEND_ENV = "EASY_CHECKPOINT_BACKEND"
_POSTGRES_URI_ENV = "EASY_CHECKPOINT_POSTGRES_URI"
_SQLITE_PATH_ENV = "EASY_CHECKPOINT_SQLITE_PATH"
_ALLOW_LEGACY_ENV = "EASY_ALLOW_LEGACY_CHECKPOINTER"
_AGENT_ENV = "AGENT_ENV"

# 相对路径按项目根解析，避免依赖进程 cwd（web_runner 会 chdir，测试注入绝对路径）
DEFAULT_SQLITE_PATH = "data/checkpoints.db"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_UNSET: Any = object()


class CheckpointDeploymentError(RuntimeError):
    """生产多副本却无法使用 Postgres checkpointer（启动期 fail-fast）。"""


@dataclass(frozen=True)
class CheckpointSelection:
    """checkpointer 后端解析结果（纯数据，便于单测注入，不打开任何连接）。"""

    kind: str
    reason: str
    postgres_uri: str = ""
    sqlite_path: str = ""
    warning: str = ""

    @property
    def is_official(self) -> bool:
        return self.kind in (BACKEND_POSTGRES, BACKEND_SQLITE)


def resolve_sqlite_path(path: str) -> Path:
    """相对路径按项目根解析；``:memory:`` 保持原样交给 sqlite。"""
    if path == ":memory:":
        return Path(path)
    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate
    return _PROJECT_ROOT / candidate


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
    postgres_uri: str | None = None,
    sqlite_path: str | None = None,
    db_type: Any = _UNSET,
) -> CheckpointSelection:
    """解析 checkpointer 后端（不打开连接）。

    所有入参都可显式注入：``None`` 表示读环境变量；``db_type`` 保持 ``_UNSET``
    时读取已加载的业务库实例。单测依赖这一点，无需真实 Postgres/MySQL。
    """
    raw_backend = (env_str(_BACKEND_ENV) if backend is None else str(backend)).strip().lower()
    warnings: list[str] = []
    if not raw_backend:
        raw_backend = BACKEND_AUTO
    elif raw_backend not in BACKEND_CHOICES:
        warnings.append(
            f"未知的 {_BACKEND_ENV}={raw_backend!r}"
            "（可选 auto/postgres/sqlite/legacy），按 auto 处理"
        )
        raw_backend = BACKEND_AUTO

    pg_uri = (
        env_str(_POSTGRES_URI_ENV) if postgres_uri is None else str(postgres_uri)
    ).strip()
    sqlite = (
        env_str(_SQLITE_PATH_ENV) if sqlite_path is None else str(sqlite_path)
    ).strip()
    if db_type is _UNSET:
        business_db = detect_business_db_type()
    else:
        business_db = str(db_type).strip().lower() if db_type else None

    def result(
        kind: str,
        reason: str,
        *,
        pg: str = "",
        sqlite_value: str = "",
        extra: str = "",
    ) -> CheckpointSelection:
        messages = [*warnings]
        if extra:
            messages.append(extra)
        return CheckpointSelection(
            kind=kind,
            reason=reason,
            postgres_uri=pg,
            sqlite_path=sqlite_value,
            warning="；".join(messages),
        )

    if raw_backend == BACKEND_LEGACY:
        return result(BACKEND_LEGACY, "EASY_CHECKPOINT_BACKEND=legacy：显式使用自研业务库快照")

    if raw_backend == BACKEND_POSTGRES:
        if pg_uri:
            return result(
                BACKEND_POSTGRES,
                "EASY_CHECKPOINT_BACKEND=postgres：使用官方 AsyncPostgresSaver",
                pg=pg_uri,
            )
        warnings.append(
            "EASY_CHECKPOINT_BACKEND=postgres 但未配置 EASY_CHECKPOINT_POSTGRES_URI，"
            "按 auto 规则回退"
        )

    if pg_uri:
        return result(
            BACKEND_POSTGRES,
            "检测到 EASY_CHECKPOINT_POSTGRES_URI：使用官方 AsyncPostgresSaver",
            pg=pg_uri,
        )

    local_only_hint = (
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
            "业务库为 MySQL 且无 PostgreSQL URI：回退自研业务库快照",
            extra=(
                "官方 LangGraph 没有 MySQL checkpointer，HITL 回退到自研 "
                "PersistentMemorySaver 快照：功能可用，但每轮全量 pickle 写业务库、"
                "非官方实现，高并发/长会话性能较差；生产建议配置 "
                "EASY_CHECKPOINT_POSTGRES_URI（多副本）或 EASY_CHECKPOINT_SQLITE_PATH（单机）"
            ),
        )
    return result(
        BACKEND_LEGACY,
        "未识别业务库类型且未配置 checkpoint 路径：回退自研业务库快照",
        extra=(
            "未能确定业务库类型（数据库可能尚未初始化），且未配置 "
            "EASY_CHECKPOINT_POSTGRES_URI / EASY_CHECKPOINT_SQLITE_PATH，"
            "HITL 使用自研 MemorySaver 业务库快照"
        ),
    )


def active_checkpoint_backend() -> str:
    """当前进程实际生效的 checkpointer 后端。"""
    if _postgres_saver is not None:
        return BACKEND_POSTGRES
    if _sqlite_saver is not None:
        return BACKEND_SQLITE
    return BACKEND_LEGACY


def _resolve_agent_env(agent_env: str | None) -> str:
    if agent_env is None:
        return env_str(_AGENT_ENV, "").strip().lower()
    return str(agent_env).strip().lower()


def _resolve_worker_count(workers: int | None) -> int:
    if workers is not None:
        return int(workers)
    # 延迟导入：utils 包会拉起较重依赖，checkpoints 可能先于 app 初始化被导入。
    from easy_agent.utils.deployment import detect_worker_count

    return detect_worker_count()


def validate_checkpoint_deployment(
    kind: str,
    *,
    agent_env: str | None = None,
    workers: int | None = None,
    allow_legacy: bool | None = None,
) -> None:
    """生产多副本必须 Postgres；否则抛 ``CheckpointDeploymentError``。

    dev/test 或单 worker 不受影响；``allow_legacy`` 显式注入用于单测，
    默认读取 ``EASY_ALLOW_LEGACY_CHECKPOINTER``（1/true/yes/on，默认关闭）。
    """
    if kind == BACKEND_POSTGRES:
        return
    if _resolve_agent_env(agent_env) != "prod":
        return
    count = _resolve_worker_count(workers)
    if count <= 1:
        return
    allowed = (
        env_bool(_ALLOW_LEGACY_ENV, False)
        if allow_legacy is None
        else bool(allow_legacy)
    )
    if allowed:
        logger.warning(
            "生产环境多副本（%s）仍使用非 Postgres checkpointer=%s：已通过 %s 显式豁免，HITL 跨副本恢复不保证一致",
            count,
            kind,
            _ALLOW_LEGACY_ENV,
        )
        return
    raise CheckpointDeploymentError(
        f"生产环境检测到 {count} 个 worker/副本，但 HITL checkpointer 后端为 {kind!r}；"
        "多副本必须配置 EASY_CHECKPOINT_POSTGRES_URI 使用官方 AsyncPostgresSaver。"
        f"若确认只做单机调试，请显式设置 {_ALLOW_LEGACY_ENV}=1（默认关闭）。"
    )


def _clear_legacy_checkpointers() -> None:
    with _CHECKPOINTER_LOCK:
        _CHECKPOINTERS.clear()


class PersistentMemorySaver(MemorySaver):
    """MemorySaver that snapshots itself into ``agent_checkpoints`` after writes."""

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
        """裁剪内存态 checkpoint，避免全量 pickle 无限增长。

        - 每个 thread/namespace 只保留最近 N 个 checkpoint（按 checkpoint_id 排序，
          LangGraph 使用时间有序的 UUID6）；
        - 只保留最近 M 个 thread；
        - 同步清理 writes/blobs 中不再被引用的条目。

        裁剪只影响持久化快照，不影响当前运行中的图（最新 checkpoint 一定保留）。
        """
        try:
            # 1) 每个 thread 每个 ns 只留最新 N 个
            for thread_id, ns_map in list(self.storage.items()):
                if not isinstance(ns_map, dict):
                    continue
                for ns, ckpts in list(ns_map.items()):
                    if not isinstance(ckpts, dict):
                        continue
                    if len(ckpts) > _PERSIST_MAX_CHECKPOINTS_PER_THREAD:
                        ordered = sorted(ckpts)
                        drop = set(ordered[: -_PERSIST_MAX_CHECKPOINTS_PER_THREAD])
                        for cid in drop:
                            ckpts.pop(cid, None)
                        for key in [
                            k for k in list(self.writes)
                            if k[0] == thread_id and k[1] == ns and k[2] in drop
                        ]:
                            self.writes.pop(key, None)
                        # 只保留仍被存活 checkpoint 引用的 blob（按 channel/version）
                        referenced: set[tuple[str, Any]] = set()
                        for entry in ckpts.values():
                            try:
                                cp = self.serde.loads_typed(entry[0])
                            except Exception:  # noqa: BLE001 - 兼容测试里的假数据
                                continue
                            for ch, ver in (cp.get("channel_versions") or {}).items():
                                referenced.add((ch, ver))
                        if referenced:
                            for key in list(self.blobs):
                                if (
                                    key[0] == thread_id
                                    and key[1] == ns
                                    and (key[2], key[3]) not in referenced
                                ):
                                    self.blobs.pop(key, None)
            # 2) thread 数量上限（按最新 checkpoint id 排序，保留最近的）
            if len(self.storage) > _PERSIST_MAX_THREADS:
                def _latest(thread_id: str) -> str:
                    ids = [
                        cid
                        for ns_map in self.storage.get(thread_id, {}).values()
                        if isinstance(ns_map, dict)
                        for cid in ns_map
                        if isinstance(cid, str)
                    ]
                    return max(ids) if ids else ""

                keep = set(
                    sorted(self.storage, key=_latest)[-_PERSIST_MAX_THREADS:]
                )
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


def postgres_checkpointer_active() -> bool:
    return _postgres_saver is not None


async def open_postgres_checkpointer(conn_string: str):
    """打开官方 AsyncPostgresSaver，并 setup() 建 checkpoint 表。

    连接在进程生命周期内保持。``thread_id`` 区分会话，不再按 session 各持一份 MemorySaver。
    """
    global _postgres_cm, _postgres_saver
    if _postgres_saver is not None:
        return _postgres_saver
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    _postgres_cm = AsyncPostgresSaver.from_conn_string(conn_string)
    _postgres_saver = await _postgres_cm.__aenter__()
    await _postgres_saver.setup()
    logger.info("HITL checkpointer: AsyncPostgresSaver")
    return _postgres_saver


async def close_postgres_checkpointer() -> None:
    global _postgres_cm, _postgres_saver
    cm = _postgres_cm
    _postgres_cm = None
    _postgres_saver = None
    if cm is None:
        return
    await cm.__aexit__(None, None, None)


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
    logger.info("HITL checkpointer: AsyncSqliteSaver | path=%s", db_path)
    return _sqlite_saver


async def close_sqlite_checkpointer() -> None:
    global _sqlite_cm, _sqlite_saver
    cm = _sqlite_cm
    _sqlite_cm = None
    _sqlite_saver = None
    if cm is None:
        return
    await cm.__aexit__(None, None, None)


async def shutdown_checkpointers() -> None:
    """关闭官方 saver 并清空自研缓存（应用 shutdown 与测试隔离都依赖它）。"""
    await close_postgres_checkpointer()
    await close_sqlite_checkpointer()
    _clear_legacy_checkpointers()


def _describe_target(selection: CheckpointSelection) -> str:
    if selection.kind == BACKEND_POSTGRES:
        return "PostgreSQL（URI 不外显）"
    if selection.kind == BACKEND_SQLITE:
        return selection.sqlite_path
    return "业务库 agent_checkpoints 表"


async def _open_with_fallback(selection: CheckpointSelection) -> str:
    """按 postgres → sqlite → legacy 顺序打开，返回实际生效的后端名。"""
    candidates: list[CheckpointSelection] = [selection]
    if selection.kind == BACKEND_POSTGRES:
        fallback = resolve_checkpoint_backend(
            backend=BACKEND_AUTO, postgres_uri="", db_type=_UNSET
        )
        if fallback.kind != BACKEND_POSTGRES:
            candidates.append(fallback)
    candidates.append(
        CheckpointSelection(BACKEND_LEGACY, "官方 saver 不可用，回退自研业务库快照")
    )

    for candidate in candidates:
        if candidate.kind == BACKEND_LEGACY:
            logger.warning("HITL checkpointer: PersistentMemorySaver（业务库快照）")
            return BACKEND_LEGACY
        try:
            if candidate.kind == BACKEND_POSTGRES:
                await open_postgres_checkpointer(candidate.postgres_uri)
            else:
                await open_sqlite_checkpointer(candidate.sqlite_path)
        except Exception as exc:  # noqa: BLE001 - 降级不能阻断启动
            logger.error(
                "官方 %s checkpointer 启用失败，尝试降级 | %s: %s",
                candidate.kind,
                type(exc).__name__,
                exc,
            )
            continue
        return candidate.kind
    logger.warning(
        "HITL checkpointer: 无官方 saver 可用，PersistentMemorySaver（业务库快照）"
    )
    return BACKEND_LEGACY


async def start_checkpoint_backend(
    selection: CheckpointSelection | None = None,
    *,
    agent_env: str | None = None,
    workers: int | None = None,
    allow_legacy: bool | None = None,
) -> str:
    """按解析结果打开 checkpointer，返回实际生效的后端名。

    ``selection`` 为空时读取环境变量解析。官方 saver 打不开时默认只降级；
    但 prod 多副本必须 Postgres：选择非 Postgres 或降级后非 Postgres 都会
    fail-fast（``CheckpointDeploymentError``），除非显式设置
    ``EASY_ALLOW_LEGACY_CHECKPOINTER=1``。
    """
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
    # 先校验选择，避免在 prod 多副本下白连一次错误的后端。
    validate_checkpoint_deployment(
        selection.kind,
        agent_env=agent_env,
        workers=workers,
        allow_legacy=allow_legacy,
    )
    opened = await _open_with_fallback(selection)
    if opened != selection.kind:
        # Postgres 打不开而降级时同样必须 fail-fast：否则多副本会静默各写各的。
        try:
            validate_checkpoint_deployment(
                opened,
                agent_env=agent_env,
                workers=workers,
                allow_legacy=allow_legacy,
            )
        except CheckpointDeploymentError:
            await shutdown_checkpointers()
            raise
    return opened


def get_session_checkpointer(session_id: str, db=None):
    """官方 saver（Postgres/SQLite）激活时全局共享；否则按 session 回放 MemorySaver。"""
    if _postgres_saver is not None:
        return _postgres_saver
    if _sqlite_saver is not None:
        return _sqlite_saver
    with _CHECKPOINTER_LOCK:
        checkpointer = _CHECKPOINTERS.get(session_id)
        if checkpointer is None:
            if len(_CHECKPOINTERS) >= _CHECKPOINTER_MAX:
                _CHECKPOINTERS.pop(next(iter(_CHECKPOINTERS)), None)
            if db is None:
                try:
                    from easy_agent.db import get_database

                    db = get_database()
                except Exception:  # noqa: BLE001
                    db = None
            checkpointer = PersistentMemorySaver(session_id, db=db)
            _CHECKPOINTERS[session_id] = checkpointer
        return checkpointer


def release_session_checkpointer(session_id: str) -> None:
    with _CHECKPOINTER_LOCK:
        _CHECKPOINTERS.pop(session_id, None)
    try:
        from easy_agent.db import get_database

        get_database().delete_agent_checkpoint(session_id)
    except Exception:  # noqa: BLE001
        pass
