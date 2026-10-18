"""LangGraph Store 后端：官方 PostgresStore 或业务库 SqlStore。

B1：``EASY_STORE_BACKEND=auto|postgres|sql``。auto 在存在 Postgres URI
（``EASY_STORE_POSTGRES_URI``，缺省复用 ``EASY_CHECKPOINT_POSTGRES_URI``）且
官方 ``langgraph.store.postgres`` 可用时使用 ``AsyncPostgresStore``；否则保持
业务库 ``SqlStore``（MySQL/单机 SQLite），不改变既有行为。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from easy_agent.bootstrap.env import env_str
from langgraph.store.base import (
    BaseStore,
    GetOp,
    Item,
    ListNamespacesOp,
    Op,
    PutOp,
    Result,
    SearchItem,
    SearchOp,
)

logger = logging.getLogger(__name__)

_NS_SEP = "\x1f"

_store: BaseStore | None = None
_pg_store_cm = None

# 单次 search 最多扫描的行数（filter 只在内存执行，防止超大表无界扫描）
_SEARCH_SCAN_LIMIT = 5000

# ── B1：Store 后端选择（auto/postgres/sql） ─────────────────────────────
BACKEND_AUTO = "auto"
BACKEND_POSTGRES = "postgres"
BACKEND_SQL = "sql"
BACKEND_CHOICES = (BACKEND_AUTO, BACKEND_POSTGRES, BACKEND_SQL)

_STORE_BACKEND_ENV = "EASY_STORE_BACKEND"
_STORE_POSTGRES_URI_ENV = "EASY_STORE_POSTGRES_URI"
_CHECKPOINT_POSTGRES_URI_ENV = "EASY_CHECKPOINT_POSTGRES_URI"


@dataclass(frozen=True)
class StoreSelection:
    """Store 后端解析结果（纯数据，不建立任何连接）。"""

    kind: str
    reason: str
    postgres_uri: str = ""
    warning: str = ""

    @property
    def is_official(self) -> bool:
        return self.kind == BACKEND_POSTGRES


def official_postgres_store_available() -> bool:
    """官方 PostgresStore 及其依赖是否可导入（不连接数据库）。"""
    try:
        from langgraph.store.postgres import AsyncPostgresStore  # noqa: F401
    except Exception:  # noqa: BLE001 - 依赖缺失/版本不兼容一律视为不可用
        return False
    return True


def resolve_store_backend(
    *,
    backend: str | None = None,
    postgres_uri: str | None = None,
    official_available: bool | None = None,
) -> StoreSelection:
    """解析 Store 后端（不打开连接）。

    入参可显式注入：``None`` 表示读环境变量；``official_available`` 供单测
    模拟官方包缺失。URI 留空时从 ``EASY_STORE_POSTGRES_URI`` 回退到
    ``EASY_CHECKPOINT_POSTGRES_URI``，一次 Secret 即可同时覆盖 HITL 与 Store。
    """
    raw_backend = (
        env_str(_STORE_BACKEND_ENV) if backend is None else str(backend)
    ).strip().lower()
    warnings: list[str] = []
    if not raw_backend:
        raw_backend = BACKEND_AUTO
    elif raw_backend not in BACKEND_CHOICES:
        warnings.append(
            f"未知的 {_STORE_BACKEND_ENV}={raw_backend!r}"
            "（可选 auto/postgres/sql），按 auto 处理"
        )
        raw_backend = BACKEND_AUTO

    if postgres_uri is None:
        uri = env_str(_STORE_POSTGRES_URI_ENV).strip() or env_str(
            _CHECKPOINT_POSTGRES_URI_ENV
        ).strip()
    else:
        uri = str(postgres_uri).strip()
    available = (
        official_postgres_store_available()
        if official_available is None
        else bool(official_available)
    )

    def result(kind: str, reason: str) -> StoreSelection:
        return StoreSelection(
            kind=kind,
            reason=reason,
            postgres_uri=uri if kind == BACKEND_POSTGRES else "",
            warning="；".join(warnings),
        )

    if raw_backend == BACKEND_SQL:
        return result(
            BACKEND_SQL,
            "EASY_STORE_BACKEND=sql：显式使用业务库 SqlStore",
        )

    if raw_backend == BACKEND_POSTGRES:
        if not uri:
            warnings.append(
                "EASY_STORE_BACKEND=postgres 但未配置 EASY_STORE_POSTGRES_URI / "
                "EASY_CHECKPOINT_POSTGRES_URI，按 auto 规则回退"
            )
        elif not available:
            warnings.append(
                "EASY_STORE_BACKEND=postgres 但官方 langgraph.store.postgres "
                "不可用，回退业务库 SqlStore"
            )
        else:
            return result(
                BACKEND_POSTGRES,
                "EASY_STORE_BACKEND=postgres：使用官方 AsyncPostgresStore",
            )

    if raw_backend == BACKEND_AUTO:
        if uri and available:
            return result(
                BACKEND_POSTGRES,
                "检测到 Postgres URI：使用官方 AsyncPostgresStore",
            )
        if uri and not available:
            warnings.append(
                "已配置 Postgres URI 但官方 langgraph.store.postgres 不可用，"
                "回退业务库 SqlStore"
            )

    return result(
        BACKEND_SQL,
        "未启用官方 PostgresStore：保持业务库 SqlStore",
    )


def _ns_key(namespace: tuple[str, ...]) -> str:
    return _NS_SEP.join(namespace)


def _ns_tuple(raw: str) -> tuple[str, ...]:
    if not raw:
        return ()
    return tuple(raw.split(_NS_SEP))


class SqlStore(BaseStore):
    """Durable cross-thread store. Official Deep Agents `store=` argument."""

    def __init__(self, db):
        self._db = db

    def batch(self, ops: Iterable[Op]) -> list[Result]:
        return [self._run_op(op) for op in ops]

    async def abatch(self, ops: Iterable[Op]) -> list[Result]:
        import asyncio

        return await asyncio.to_thread(self.batch, list(ops))

    def _run_op(self, op: Op) -> Result:
        if isinstance(op, GetOp):
            return self._get(op.namespace, op.key)
        if isinstance(op, PutOp):
            if op.value is None:
                self._db.store_delete(_ns_key(op.namespace), op.key)
            else:
                self._db.store_put(
                    _ns_key(op.namespace),
                    op.key,
                    json.dumps(op.value, ensure_ascii=False),
                )
            return None
        if isinstance(op, SearchOp):
            return self._search(op)
        if isinstance(op, ListNamespacesOp):
            return self._list_namespaces(op)
        raise ValueError(f"Unknown operation type: {type(op)}")

    def _row_item(self, row: dict[str, Any], *, search: bool = False) -> Item:
        created = datetime.fromisoformat(row["created_at"]) if row.get("created_at") else datetime.now(UTC)
        updated = datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") else created
        value = json.loads(row["value"]) if isinstance(row["value"], str) else row["value"]
        ns = _ns_tuple(row["namespace"])
        if search:
            return SearchItem(
                namespace=ns,
                key=row["item_key"],
                value=value,
                created_at=created,
                updated_at=updated,
                score=None,
            )
        return Item(
            value=value,
            key=row["item_key"],
            namespace=ns,
            created_at=created,
            updated_at=updated,
        )

    def _get(self, namespace: tuple[str, ...], key: str) -> Item | None:
        row = self._db.store_get(_ns_key(namespace), key)
        return self._row_item(row) if row else None

    @staticmethod
    def _matches_filter(value: dict[str, Any], filter_spec: dict[str, Any]) -> bool:
        """实现 LangGraph BaseStore 的 filter 语义（$eq/$ne/$gt/... + 直接相等）。"""
        for key, condition in filter_spec.items():
            actual = value.get(key)
            if not isinstance(condition, dict):
                if actual != condition:
                    return False
                continue
            for operator, expected in condition.items():
                if operator == "$eq":
                    ok = actual == expected
                elif operator == "$ne":
                    ok = actual != expected
                elif operator == "$gt":
                    ok = actual is not None and actual > expected
                elif operator == "$gte":
                    ok = actual is not None and actual >= expected
                elif operator == "$lt":
                    ok = actual is not None and actual < expected
                elif operator == "$lte":
                    ok = actual is not None and actual <= expected
                elif operator == "$in":
                    ok = actual in expected
                elif operator == "$nin":
                    ok = actual not in expected
                else:
                    # 未知操作符：保守地视为不匹配（避免静默返回错误数据）
                    ok = False
                if not ok:
                    return False
        return True

    def _search(self, op: SearchOp) -> list[SearchItem]:
        """先过滤再分页：SQL 侧没有 JSON 过滤能力，按批扫描直到收够 offset+limit。

        旧实现先 SQL LIMIT/OFFSET 再在内存过滤，过滤后结果可能少页；
        这里最多扫描 ``_SEARCH_SCAN_LIMIT`` 条，避免大表无界扫描。
        """
        prefix = _ns_key(op.namespace_prefix)
        wanted = op.offset + op.limit
        batch = max(200, op.limit)
        matched: list[SearchItem] = []
        scanned = 0
        offset = 0
        while len(matched) < wanted and scanned < _SEARCH_SCAN_LIMIT:
            rows = self._db.store_search(prefix, limit=batch, offset=offset)
            if not rows:
                break
            scanned += len(rows)
            offset += len(rows)
            for row in rows:
                item = self._row_item(row, search=True)
                if op.filter and not self._matches_filter(item.value, op.filter):
                    continue
                matched.append(item)
                if len(matched) >= wanted:
                    break
        return matched[op.offset : op.offset + op.limit]

    def _list_namespaces(self, op: ListNamespacesOp) -> list[tuple[str, ...]]:
        namespaces = [_ns_tuple(raw) for raw in self._db.store_list_namespaces()]
        if op.match_conditions:
            filtered = []
            for ns in namespaces:
                ok = True
                for cond in op.match_conditions:
                    path = tuple(cond.path)
                    if cond.match_type == "prefix":
                        if ns[: len(path)] != path:
                            ok = False
                    elif cond.match_type == "suffix":
                        if ns[-len(path) :] != path:
                            ok = False
                if ok:
                    filtered.append(ns)
            namespaces = filtered
        if op.max_depth is not None:
            namespaces = sorted({ns[: op.max_depth] for ns in namespaces})
        else:
            namespaces = sorted(namespaces)
        return namespaces[op.offset : op.offset + op.limit]


def get_agent_store(db=None) -> BaseStore:
    """返回当前进程生效的 Store：官方 PostgresStore 或业务库 SqlStore。"""
    global _store
    if _store is not None:
        return _store
    if db is None:
        from easy_agent.db import get_database

        db = get_database()
    _store = SqlStore(db)
    return _store


async def open_postgres_store(conn_string: str):
    """打开官方 AsyncPostgresStore 并 setup()（store / migrations 表）。

    连接池在进程生命周期内保持，由 ``shutdown_store`` 关闭。
    """
    global _pg_store_cm, _store
    if _store is not None and _pg_store_cm is not None:
        return _store
    from langgraph.store.postgres import AsyncPostgresStore

    cm = AsyncPostgresStore.from_conn_string(conn_string)
    store = await cm.__aenter__()
    try:
        await store.setup()
    except Exception:
        await cm.__aexit__(None, None, None)
        raise
    _pg_store_cm = cm
    _store = store
    logger.info("LangGraph Store: AsyncPostgresStore")
    return store


async def shutdown_store() -> None:
    """关闭官方 PostgresStore 并清空缓存（应用 shutdown 与测试隔离依赖它）。"""
    global _pg_store_cm, _store
    cm = _pg_store_cm
    _pg_store_cm = None
    _store = None
    if cm is not None:
        await cm.__aexit__(None, None, None)


async def start_store_backend(
    selection: "StoreSelection | None" = None,
    *,
    opener=None,
) -> str:
    """按解析结果启用 Store，返回实际生效的后端（postgres/sql）。

    ``opener`` 仅用于测试注入 fake：接收 conn_string，返回已 setup 的 store。
    官方 PostgresStore 打不开时回退业务库 SqlStore，不阻断启动（Store 是可选能力）。
    """
    global _store
    if selection is None:
        selection = resolve_store_backend()
    await shutdown_store()
    if selection.warning:
        logger.warning("LangGraph Store 配置 | %s", selection.warning)
    logger.info(
        "LangGraph Store 选择 | backend=%s | 原因: %s",
        selection.kind,
        selection.reason,
    )
    if selection.kind != BACKEND_POSTGRES:
        return BACKEND_SQL
    if not selection.postgres_uri:
        logger.warning("Postgres Store 选择缺少 URI，回退业务库 SqlStore")
        return BACKEND_SQL
    try:
        if opener is None:
            await open_postgres_store(selection.postgres_uri)
        else:
            _store = await opener(selection.postgres_uri)
    except Exception as exc:  # noqa: BLE001 - 降级不能阻断启动
        logger.error(
            "官方 PostgresStore 启用失败，回退业务库 SqlStore | %s: %s",
            type(exc).__name__,
            exc,
        )
        await shutdown_store()
        return BACKEND_SQL
    logger.info("LangGraph Store: AsyncPostgresStore")
    return BACKEND_POSTGRES


def active_store_backend() -> str:
    """当前进程实际生效的 Store 后端；未初始化时按默认的 SqlStore 处理。"""
    if _store is None or isinstance(_store, SqlStore):
        return BACKEND_SQL
    return BACKEND_POSTGRES
