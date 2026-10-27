"""run 租约注册表（多副本 Phase 2B）。

把「一次聊天/恢复 run」持久化到共享库，回答三个问题：

1. 哪个 run 正在哪个 Pod 上跑：``lease_owner`` + ``lease_until``；
2. Pod 崩溃/驱逐后怎么收尾：租约过期后，启动时以及之后每隔
   ``EASY_RUN_RECOVERY_INTERVAL`` 秒由 :func:`recover_stale_runs` 把陈旧 ``running``
   标记为 ``interrupted``、append 一条 error 事件，并释放这条 run 留下的会话流锁；
3. 正常完成/失败/取消：由 ``_detached_event_stream`` 落终态。

周期恢复顺带清掉保留期之前的 ``stream_events``（:func:`purge_stale_stream_events`）。

本模块所有方法都是**薄封装 + 异常只记日志**：run 登记是观测/恢复能力，
绝不允许反过来打断聊天主链路。本增量**不做**自动重新执行 stale run（执行器
属于 Phase 2C），恢复后只保证前端不再停在「执行中」。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from easy_agent.db.repositories import agent_runs as agent_runs_repo
from easy_agent.db.repositories import session_runtime as session_runtime_repo
from easy_agent.db.repositories.stream_events import (
    append_stream_event,
    delete_stream_events_before,
)
from easy_agent.bootstrap.env import env_int
from easy_agent.services.session_runtime import instance_owner, is_stream_active
from easy_agent.services.sse_emitter import SSEEmitter
from easy_agent.utils.blocking import run_blocking

logger = logging.getLogger("easy_agent.run_registry")

# run 租约时长（秒）。默认 10 分钟：远长于一次正常聊天，短于「用户发现卡住后
# 重新发消息」的等待；Pod 崩溃后最多这么久就会被启动恢复识别（下一次 Pod 启动时）。
RUN_LEASE_SECONDS = env_int("EASY_RUN_LEASE_SECONDS", 600)

# 恢复时写入终态与 SSE 的提示文案（前端 error 事件直接展示）。
RECOVER_ERROR_MESSAGE = "上一次运行所在实例已中断，请重新发送或从审批处继续"

# 错误文本截断：agent_runs.error 是 TEXT，但栈/异常消息可能非常长，
# 只保留足够定位的前 500 字符。
MAX_ERROR_CHARS = 500

# 单页扫描上限；恢复循环按页处理直到没有 stale 行为止。
RECOVER_BATCH_LIMIT = 500

# stale run 周期恢复的间隔（秒）。新 Pod 往往在旧 Pod 的 run 租约过期之前就起来了，
# 启动时那一轮扫不到，之后靠它补上；不大于 0 时只在启动时扫一次。
RUN_RECOVERY_INTERVAL_ENV = "EASY_RUN_RECOVERY_INTERVAL"
DEFAULT_RUN_RECOVERY_INTERVAL = 60

# stream_events 只用于断线重连回放和中断后补发收尾帧，一天前的事件不会再有人回放。新一轮
# 对话开始时会清掉本会话的旧事件，但用户不再回来的会话会一直留着，由周期恢复顺带清理。
STREAM_EVENTS_RETENTION = timedelta(hours=24)
# 每条 DELETE 最多删这么多行：积压很多时分批删，避免长事务。
STREAM_EVENTS_PURGE_BATCH = 5000

_recovery_task: asyncio.Task | None = None


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _lease_seconds(lease_seconds: int | None = None) -> int:
    seconds = RUN_LEASE_SECONDS if lease_seconds is None else lease_seconds
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = RUN_LEASE_SECONDS
    return max(1, seconds)


def new_run_id(session_id: str, message_id: str = "") -> str:
    """生成 run_id：``session_id:message_id``，message_id 缺失时用 uuid 兜底。

    run_id 的列宽是 VARCHAR(255)；客户端 message_id 没有长度约束，这里截断
    保证 INSERT 不会因超长失败（确定性截断，不影响同一会话的正常幂等）。
    """
    mid = (message_id or "").strip() or uuid.uuid4().hex
    return f"{session_id}:{mid}"[:255]


def start_run(
    db,
    run_id: str,
    session_id: str,
    *,
    username: str = "",
    message_id: str = "",
    request_json: str = "",
    owner: str | None = None,
    lease_seconds: int | None = None,
) -> bool:
    """登记 run 为 running；异常只记日志，返回 False（不影响聊天）。"""
    try:
        until = _iso(_utc_now() + timedelta(seconds=_lease_seconds(lease_seconds)))
        return agent_runs_repo.start_run(
            db,
            run_id,
            session_id,
            username or "",
            message_id or "",
            request_json or "",
            owner or instance_owner(),
            until,
        )
    except Exception as exc:  # noqa: BLE001 - 观测能力绝不打断聊天
        logger.warning(f"登记 run 失败（忽略） | run={run_id} | {type(exc).__name__}: {exc}")
        return False


def renew_run(
    db,
    run_id: str,
    *,
    owner: str | None = None,
    lease_seconds: int | None = None,
) -> bool:
    """续约 running run；异常只记日志，返回 False。"""
    try:
        until = _iso(_utc_now() + timedelta(seconds=_lease_seconds(lease_seconds)))
        return agent_runs_repo.renew_run(db, run_id, owner or instance_owner(), until)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"续约 run 失败（忽略本次） | run={run_id} | {type(exc).__name__}: {exc}")
        return False


def finish_run(db, run_id: str, status: str, error: str = "") -> bool:
    """落 run 终态；异常只记日志，返回 False。"""
    try:
        return agent_runs_repo.finish_run(db, run_id, status, (error or "")[:MAX_ERROR_CHARS])
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"写入 run 终态失败（忽略） | run={run_id} | {type(exc).__name__}: {exc}")
        return False


def recover_stale_runs(db, now: datetime | None = None) -> int:
    """把租约过期的 running run 标记为 interrupted，并补一条 error 事件。

    多 Pod 同时启动时，只有 ``mark_interrupted`` 返回 True 的 Pod 才写 SSE
    事件（条件更新 ``WHERE status='running'``），不会重复收尾。

    返回成功标记的 run 数量。**只做恢复标记，不重新执行**（Phase 2C 执行器）。
    """
    try:
        current = now or _utc_now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        now_iso = _iso(current)
    except Exception as exc:  # noqa: BLE001 - 时间参数异常按当前时间处理
        logger.warning(f"recover_stale_runs 时间参数异常，改用当前时间: {exc}")
        current = _utc_now()
        now_iso = _iso(current)

    recovered = 0
    while True:
        try:
            rows = agent_runs_repo.list_stale_running(
                db, now_iso, RECOVER_BATCH_LIMIT
            )
        except Exception as exc:  # noqa: BLE001 - 启动恢复失败只告警
            logger.warning(f"扫描 stale run 失败（停止本轮恢复）: {exc}")
            break
        if not rows:
            break

        handled = 0
        for row in rows:
            run_id = str(row.get("run_id") or "")
            session_id = str(row.get("session_id") or "")
            try:
                marked = agent_runs_repo.mark_interrupted(
                    db, run_id, RECOVER_ERROR_MESSAGE
                )
            except Exception as exc:  # noqa: BLE001 - 单条失败不影响其它 run
                logger.warning(f"恢复 stale run 失败（忽略） | run={run_id} | {exc}")
                continue
            handled += 1
            if not marked:
                # 另一个 Pod 已先一步恢复（或 run 已自行落终态）：不写事件。
                continue
            recovered += 1
            owner = str(row.get("lease_owner") or "")
            if session_id:
                # 若该会话已经被新的流接管，不要往新流的事件表里追加旧 run 的
                # error——否则重连回放会把旧错误显示在新回复里。
                if _stream_taken_over(db, session_id, run_id, owner, current, now_iso):
                    logger.info(
                        f"stale run 所属会话已有新流，跳过恢复 error 事件 | "
                        f"run={run_id} | session={session_id}"
                    )
                    continue
                # 与业务 error 事件同格式：前端收到即结束“执行中”状态。
                # 事件写失败不影响 run 已落 interrupted 的事实。
                try:
                    frame = SSEEmitter(session_id).error(RECOVER_ERROR_MESSAGE)
                    append_stream_event(db, session_id, frame)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        f"写入恢复 error 事件失败（忽略） | run={run_id} | {exc}"
                    )
                # 崩溃的 Pod 来不及释放流锁。先写 error 再释放：重连的前端回放到 error
                # 即结束；用户照提示重发时也不会被这把锁挡住（否则要等它的 TTL 过期）
                _release_dead_stream_lock(db, session_id, owner, now_iso)
            logger.warning(
                f"恢复 stale run | run={run_id} | session={session_id} | "
                f"last_owner={row.get('lease_owner') or '(unknown)'}"
            )

        if len(rows) < RECOVER_BATCH_LIMIT:
            break
        if handled == 0:
            # 整页都写不了库（DB 故障）：立即停止，避免启动恢复无限循环。
            logger.warning("本轮 stale run 全部恢复失败，停止重试")
            break

    if recovered:
        logger.warning(
            f"stale run 恢复完成：{recovered} 个 running run 已标记为 interrupted"
            "（未自动重新执行，需用户重发或从审批处继续）"
        )
    else:
        # 周期恢复每分钟一轮，没找到时不刷 INFO
        logger.debug("未发现 stale running run")
    return recovered


def _stream_taken_over(
    db, session_id: str, run_id: str, owner: str, now: datetime, now_iso: str
) -> bool:
    """会话的流是否已被新的流接管：流锁仍有效，且锁换了实例、或同会话还有别的在跑的 run。

    锁仍归这个 stale run 的实例、又没有别的在跑的 run，说明锁是崩溃的 Pod 留下的。
    查询失败时按未接管处理（照常写 error 事件）。
    """
    try:
        if not is_stream_active(db, session_id, now=now):
            return False
        lock = session_runtime_repo.get_session_runtime(db, session_id) or {}
        if str(lock.get("stream_owner") or "") != owner:
            return True
        return agent_runs_repo.has_other_live_run(db, session_id, run_id, now_iso)
    except Exception:  # noqa: BLE001
        return False


def _release_dead_stream_lock(db, session_id: str, owner: str, now_iso: str) -> None:
    """释放 stale run 的实例留下的会话流锁；锁已换了主人时不动。"""
    if not owner:
        return
    try:
        if session_runtime_repo.release_session_stream(db, session_id, owner, now_iso):
            logger.info(f"释放 stale run 留下的流锁 | session={session_id} | owner={owner}")
    except Exception as exc:  # noqa: BLE001 - 释放失败只是要等锁的 TTL 过期
        logger.warning(f"释放 stale run 留下的流锁失败（忽略） | session={session_id} | {exc}")


def purge_stale_stream_events(
    db, now: datetime | None = None, *, batch: int = STREAM_EVENTS_PURGE_BATCH
) -> int:
    """分批删除保留期之前的 stream_events，返回删除条数。

    时间口径与 ``append_stream_event`` 一致（本地时间的 ISO 字符串），按字符串比较。
    """
    cutoff = ((now or datetime.now()) - STREAM_EVENTS_RETENTION).isoformat()
    total = 0
    while True:
        deleted = delete_stream_events_before(db, cutoff, limit=batch)
        total += deleted
        if deleted < batch:
            return total


async def _run_recovery_loop(interval: float) -> None:
    # 延迟导入：run_registry 在 db 层之上，避免导入期循环
    from easy_agent.db import get_database

    while True:
        await asyncio.sleep(interval)
        try:
            await run_blocking(recover_stale_runs, get_database())
        except Exception as exc:  # noqa: BLE001 - 恢复失败不能拖垮应用，下一轮重试
            logger.warning(f"stale run 周期恢复失败（下一轮重试）: {exc}")
        try:
            await run_blocking(purge_stale_stream_events, get_database())
        except Exception as exc:  # noqa: BLE001 - 清理失败不影响恢复，下一轮重试
            logger.warning(f"stream_events 过期清理失败（下一轮重试）: {exc}")


def start_run_recovery(*, interval: float | None = None) -> asyncio.Task | None:
    """在启动时那一轮扫描之后，每隔 ``interval`` 秒再恢复一次 stale run。

    间隔取 ``EASY_RUN_RECOVERY_INTERVAL``（秒，默认 60），不大于 0 时关闭。多个 Pod 同时
    扫描没关系：``mark_interrupted`` 的条件更新保证每个 run 只被收尾一次。
    """
    global _recovery_task
    if _recovery_task is not None and not _recovery_task.done():
        return _recovery_task
    seconds = (
        env_int(RUN_RECOVERY_INTERVAL_ENV, DEFAULT_RUN_RECOVERY_INTERVAL)
        if interval is None
        else interval
    )
    if seconds <= 0:
        logger.info(f"stale run 周期恢复已关闭（{RUN_RECOVERY_INTERVAL_ENV}={seconds}）")
        return None
    _recovery_task = asyncio.create_task(_run_recovery_loop(seconds), name="run-recovery")
    return _recovery_task


async def stop_run_recovery() -> None:
    global _recovery_task
    task, _recovery_task = _recovery_task, None
    if task is None:
        return
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


__all__ = [
    "DEFAULT_RUN_RECOVERY_INTERVAL",
    "MAX_ERROR_CHARS",
    "RECOVER_BATCH_LIMIT",
    "RECOVER_ERROR_MESSAGE",
    "RUN_LEASE_SECONDS",
    "RUN_RECOVERY_INTERVAL_ENV",
    "STREAM_EVENTS_RETENTION",
    "finish_run",
    "new_run_id",
    "purge_stale_stream_events",
    "recover_stale_runs",
    "renew_run",
    "start_run",
    "start_run_recovery",
    "stop_run_recovery",
]
