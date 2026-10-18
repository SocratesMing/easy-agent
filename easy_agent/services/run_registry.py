"""run 租约注册表（多副本 Phase 2B）。

把「一次聊天/恢复 run」持久化到共享库，回答三个问题：

1. 哪个 run 正在哪个 Pod 上跑：``lease_owner`` + ``lease_until``；
2. Pod 崩溃/驱逐后怎么收尾：租约过期 → 启动时 :func:`recover_stale_runs`
   把陈旧 ``running`` 标记为 ``interrupted`` 并 append 一条 error 事件；
3. 正常完成/失败/取消：由 ``_detached_event_stream`` 落终态。

本模块所有方法都是**薄封装 + 异常只记日志**：run 登记是观测/恢复能力，
绝不允许反过来打断聊天主链路。本增量**不做**自动重新执行 stale run（执行器
属于 Phase 2C），恢复后只保证前端不再停在「执行中」。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from ..db.repositories import agent_runs as agent_runs_repo
from ..db.repositories.stream_events import append_stream_event
from ..env import env_int
from .session_runtime import instance_owner, is_stream_active
from .sse_emitter import SSEEmitter

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
        now_iso = _iso(_utc_now())

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
            if session_id:
                # 若该会话已经被新的 run 接管（新流仍在跑），不要往新流的事件表里
                # 追加旧 run 的 error——否则重连回放会把旧错误显示在新回复里。
                try:
                    active = is_stream_active(db, session_id)
                except Exception:  # noqa: BLE001
                    active = False
                if active:
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
            f"启动恢复完成：{recovered} 个 stale running run 已标记为 interrupted"
            "（未自动重新执行，需用户重发或从审批处继续）"
        )
    else:
        logger.info("启动恢复：未发现 stale running run")
    return recovered


__all__ = [
    "MAX_ERROR_CHARS",
    "RECOVER_BATCH_LIMIT",
    "RECOVER_ERROR_MESSAGE",
    "RUN_LEASE_SECONDS",
    "finish_run",
    "new_run_id",
    "recover_stale_runs",
    "renew_run",
    "start_run",
]
