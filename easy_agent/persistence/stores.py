"""LangGraph Store：业务库 SqlStore。

跨 thread 的长期数据（deepagents 的 ``store=``、上传文件的 store 模式）存进业务库
（MySQL / 单机 SQLite）的键值表。生产只有 MySQL，官方 PostgresStore 已移除。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

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

_NS_SEP = "\x1f"

_store: BaseStore | None = None

# 单次 search 最多扫描的行数（filter 只在内存执行，防止超大表无界扫描）
_SEARCH_SCAN_LIMIT = 5000


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
    """返回当前进程共用的业务库 SqlStore（首次调用时绑定业务库）。"""
    global _store
    if _store is not None:
        return _store
    if db is None:
        from easy_agent.db import get_database

        db = get_database()
    _store = SqlStore(db)
    return _store


def reset_store() -> None:
    """丢掉缓存的 Store，下次 ``get_agent_store`` 重新绑定业务库（应用 shutdown 与测试隔离用）。"""
    global _store
    _store = None
