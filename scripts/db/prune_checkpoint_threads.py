#!/usr/bin/env python3
"""清理官方 checkpoint saver 里不再需要的 thread（DA-03 P2）。

改造前每条消息一个 ``{session_id}-{message_id}`` thread、每次定时任务一个
``sched-{task_id}-{run_id}`` thread，官方 saver（Postgres / SQLite）从不删除。P0 起删除会话、
定时任务结束时会删掉自己的 thread，本脚本清理此前留下的存量：

    python scripts/db/prune_checkpoint_threads.py                 # 预览，不删除
    python scripts/db/prune_checkpoint_threads.py --apply         # 执行删除
    python scripts/db/prune_checkpoint_threads.py --apply --include-pending   # 兼容期结束后，连同挂起的审批一起删

规则（只看每个 thread 根命名空间的最新 checkpoint；删除时连同子代理命名空间一起删）：

- 会话 thread（``thread_id`` 就是会话 id）与无法识别的 thread：保留；
- 每条消息一个的 thread、定时任务 thread：最新 checkpoint 早于 ``--min-age-hours``（默认 24）
  才删，避免删到正在运行的 thread；
- 每条消息一个的 thread 挂着待审批中断时保留（兼容期内还能恢复），``--include-pending`` 才删。

legacy 快照（业务库为 MySQL 且没配 PG / SQLite checkpoint）按 thread 数裁剪，不需要本脚本。
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_SESSION_THREAD = re.compile(rf"^{_UUID}$")
_PER_MESSAGE_THREAD = re.compile(rf"^{_UUID}-.+$")

#: LangGraph 记录中断的 pending write 通道（公开常量 ``langgraph.constants.INTERRUPT`` 已弃用）。
_INTERRUPT_CHANNEL = "__interrupt__"

KIND_LABELS = {
    "session": "会话 thread",
    "per-message": "每条消息一个的 thread",
    "scheduled": "定时任务 thread",
    "other": "无法识别的 thread",
}


def thread_kind(thread_id: str) -> str:
    if _SESSION_THREAD.match(thread_id):
        return "session"
    if _PER_MESSAGE_THREAD.match(thread_id):
        return "per-message"
    if thread_id.startswith("sched-"):
        return "scheduled"
    return "other"


@dataclass
class ThreadInfo:
    thread_id: str
    kind: str
    last_ts: datetime | None
    pending: bool


@dataclass
class PrunePlan:
    delete: list[ThreadInfo] = field(default_factory=list)
    keep: list[ThreadInfo] = field(default_factory=list)
    #: (kind, 保留原因) → 个数
    kept_because: Counter = field(default_factory=Counter)


def _parse_ts(value) -> datetime | None:
    try:
        ts = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


async def collect_threads(saver) -> list[ThreadInfo]:
    """遍历全部 checkpoint，取每个 thread 根命名空间的最新一个（checkpoint_id 时间有序）。"""
    latest: dict[str, object] = {}
    async for item in saver.alist(None):
        configurable = item.config["configurable"]
        if configurable.get("checkpoint_ns"):
            continue
        thread_id = configurable["thread_id"]
        current = latest.get(thread_id)
        if current is None or configurable["checkpoint_id"] > current.config["configurable"]["checkpoint_id"]:
            latest[thread_id] = item
    return [
        ThreadInfo(
            thread_id=thread_id,
            kind=thread_kind(thread_id),
            last_ts=_parse_ts(item.checkpoint.get("ts")),
            pending=any(write[1] == _INTERRUPT_CHANNEL for write in item.pending_writes or []),
        )
        for thread_id, item in latest.items()
    ]


def plan_prune(
    threads: list[ThreadInfo],
    *,
    now: datetime,
    min_age: timedelta,
    include_pending: bool = False,
) -> PrunePlan:
    plan = PrunePlan()
    for info in threads:
        if info.kind in ("session", "other"):
            reason = "保留"
        elif info.last_ts is None or now - info.last_ts < min_age:
            reason = "近期仍有写入"
        elif info.pending and not include_pending:
            reason = "挂着待审批"
        else:
            plan.delete.append(info)
            continue
        plan.keep.append(info)
        plan.kept_because[(info.kind, reason)] += 1
    return plan


async def apply_prune(saver, plan: PrunePlan) -> int:
    deleted = 0
    for info in plan.delete:
        await saver.adelete_thread(info.thread_id)
        deleted += 1
    return deleted


def format_report(plan: PrunePlan) -> str:
    total = Counter(info.kind for info in [*plan.delete, *plan.keep])
    to_delete = Counter(info.kind for info in plan.delete)
    lines = [f"thread 共 {sum(total.values())} 个，计划删除 {len(plan.delete)} 个"]
    for kind, label in KIND_LABELS.items():
        if not total[kind]:
            continue
        kept = "，".join(
            f"{reason} {count}" for (k, reason), count in sorted(plan.kept_because.items()) if k == kind
        )
        lines.append(f"  {label}: {total[kind]}（删除 {to_delete[kind]}" + (f"；{kept}" if kept else "") + "）")
    return "\n".join(lines)


async def _run(selection, args) -> int:
    from easy_agent.persistence import checkpoints

    if selection.kind == checkpoints.BACKEND_POSTGRES:
        saver = await checkpoints.open_postgres_checkpointer(selection.postgres_uri)
    else:
        saver = await checkpoints.open_sqlite_checkpointer(selection.sqlite_path)
    try:
        threads = await collect_threads(saver)
        plan = plan_prune(
            threads,
            now=datetime.now(UTC),
            min_age=timedelta(hours=args.min_age_hours),
            include_pending=args.include_pending,
        )
        print(format_report(plan))
        if not args.apply:
            print("预览模式：没有删除任何数据；确认后加 --apply 执行")
            return 0
        deleted = await apply_prune(saver, plan)
        print(f"已删除 {deleted} 个 thread")
        return 0
    finally:
        await checkpoints.shutdown_checkpointers()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="清理官方 checkpoint saver 里不再需要的 thread（DA-03）")
    parser.add_argument("--apply", action="store_true", help="执行删除（默认只预览）")
    parser.add_argument(
        "--min-age-hours", type=float, default=24.0, help="最新 checkpoint 早于这么多小时才删（默认 24）"
    )
    parser.add_argument(
        "--include-pending",
        action="store_true",
        help="连同挂着待审批的每消息 thread 一起删（审批兼容期结束后使用）",
    )
    args = parser.parse_args(argv)

    from easy_agent.bootstrap.initialization import initialize_runtime
    from easy_agent.persistence.checkpoints import resolve_checkpoint_backend

    runtime = initialize_runtime()
    if runtime.config is None:
        print("ERROR: 配置未加载", file=sys.stderr)
        return 2
    selection = resolve_checkpoint_backend(db_type=runtime.config.database.type)
    if not selection.is_official:
        print(f"当前是 legacy checkpoint 快照（{selection.reason}），按 thread 数裁剪，不需要清理")
        return 0
    print(f"checkpoint 后端：{selection.kind}（{selection.reason}）")
    return asyncio.run(_run(selection, args))


if __name__ == "__main__":
    sys.exit(main())
