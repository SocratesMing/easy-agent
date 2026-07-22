"""Scheduled task runtime - APScheduler AsyncIOScheduler singleton.

Manages cron-based scheduled tasks created by the AI agent. On startup,
loads all enabled tasks from DB and registers them with APScheduler.
When a task fires, creates a new session, invokes the agent non-streaming,
and records the result.
"""

import asyncio
import logging
import uuid
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..db import get_database
from ..models.db import ScheduledTaskModel, ScheduledTaskRunModel, SessionModel
from ..utils.task_logger import log_task_event
from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger("easy_agent.scheduler")

_scheduler_instance: AsyncIOScheduler | None = None

# 正在执行的任务：task_id -> asyncio.Task，用于暂停/删除时中断在途运行
running_tasks: dict[str, "asyncio.Task"] = {}

# 已进入「停止中」状态的任务（被暂停或删除），用于在途执行结束后的二次校验，
# 确保即使 asyncio 取消未能立即中断 agent 调用，运行结果也不会被记为成功。
stopping_tasks: set[str] = set()


class _TaskAborted(Exception):
    """任务在运行过程中被暂停/删除，主动中止 agent 调用。"""


class _PauseAwareCallback(BaseCallbackHandler):
    """在 agent 执行过程中周期性检查任务是否已被暂停/删除，命中即抛异常中止。"""

    def __init__(self, task_id: str) -> None:
        self.task_id = task_id

    def _check(self) -> None:
        if self.task_id in stopping_tasks:
            raise _TaskAborted(f"定时任务已暂停/删除，中止执行: {self.task_id}")

    def on_llm_start(self, *args, **kwargs) -> None:
        self._check()

    def on_chain_start(self, *args, **kwargs) -> None:
        self._check()


def init_scheduler() -> AsyncIOScheduler:
    global _scheduler_instance
    if _scheduler_instance is not None:
        return _scheduler_instance
    _scheduler_instance = AsyncIOScheduler(timezone="Asia/Shanghai")
    logger.info("AsyncIOScheduler 已初始化 (timezone=Asia/Shanghai)")
    return _scheduler_instance


def get_scheduler() -> AsyncIOScheduler:
    if _scheduler_instance is None:
        raise RuntimeError("Scheduler 未初始化，请先调用 init_scheduler()")
    return _scheduler_instance


def shutdown_scheduler():
    global _scheduler_instance
    if _scheduler_instance is not None:
        _scheduler_instance.shutdown(wait=False)
        _scheduler_instance = None
        logger.info("AsyncIOScheduler 已关闭")


def register_scheduled_task(task: ScheduledTaskModel) -> str | None:
    """注册定时任务到 scheduler，返回下次执行时间字符串。"""
    scheduler = get_scheduler()
    if not scheduler.running:
        logger.warning("Scheduler 未运行，尝试启动...")
        try:
            scheduler.start()
        except Exception as e:
            logger.error(f"Scheduler 启动失败: {e}")
    try:
        trigger = CronTrigger.from_crontab(task.schedule_cron)
    except ValueError as e:
        logger.error(f"无效的 cron 表达式 {task.schedule_cron}: {e}")
        return None
    scheduler.add_job(
        _execute_task,
        trigger=trigger,
        args=[task.task_id],
        id=task.task_id,
        replace_existing=True,
        misfire_grace_time=300,
        coalesce=True,
        max_instances=1,
    )
    job = scheduler.get_job(task.task_id)
    nrt = getattr(job, "next_run_time", None) if job else None
    next_run = nrt.strftime("%Y-%m-%d %H:%M:%S") if nrt else ""
    logger.info(f"定时任务已注册 | user={task.username} | task_id={task.task_id} | name={task.name} | cron={task.schedule_cron} | next_run={next_run}")
    log_task_event(
        username=task.username,
        task_id=task.task_id,
        operation="register",
        detail=f"任务注册到调度器: next_run={next_run}",
        cron=task.schedule_cron,
        next_run=next_run,
    )
    return next_run


def unregister_scheduled_task(task_id: str, task: ScheduledTaskModel | None = None):
    scheduler = get_scheduler()
    # 标记为停止中：即便在途执行的 agent 调用未能被立即取消，
    # 也会在 _execute_task 结束前的二次校验中被识别并标记为取消。
    stopping_tasks.add(task_id)
    # 中断正在执行的本次运行（暂停/删除时不应继续跑完）
    running = running_tasks.get(task_id)
    if running is not None and not running.done():
        try:
            running.cancel()
            logger.info(f"定时任务在途运行已取消 | task_id={task_id}")
        except Exception as e:
            logger.warning(f"取消在途运行失败: {e}")
    try:
        scheduler.remove_job(task_id)
        user_info = f"user={task.username}" if task else "user=unknown"
        logger.info(f"定时任务已移除 | {user_info} | task_id={task_id}")
        if task is not None:
            log_task_event(
                username=task.username,
                task_id=task_id,
                operation="unregister",
                detail="任务从调度器移除",
            )
    except Exception as e:
        # 不应静默失败：若 job 移除失败，任务仍会按 cron 继续触发
        logger.warning(f"移除定时任务 job 失败（任务可能继续触发）: {e} | task_id={task_id}")


def reload_all_tasks():
    """启动时从 DB 加载所有 enabled 的定时任务。"""
    db = get_database()
    tasks = db.list_all_enabled_scheduled_tasks()
    count = 0
    for task in tasks:
        next_run = register_scheduled_task(task)
        if next_run:
            db.update_scheduled_task_run_times(task.task_id, task.last_run_at, next_run)
            count += 1
            log_task_event(
                username=task.username,
                task_id=task.task_id,
                operation="reload",
                detail=f"启动时重载任务: name={task.name}",
                name=task.name,
                cron=task.schedule_cron,
                next_run=next_run,
            )
    logger.info(f"从 DB 重载 {count}/{len(tasks)} 个定时任务")


async def _execute_task(task_id: str):
    """定时任务执行核心逻辑：创建新 session → 调用 agent → 记录结果。"""
    from .agent_manager import get_or_create_agent_for_session, remove_session_agent
    from langchain_core.messages import HumanMessage

    db = get_database()
    task = db.get_scheduled_task(task_id)
    if not task or not task.enabled:
        return

    # 登记在途运行，便于暂停/删除时中断
    running_tasks[task_id] = asyncio.current_task()
    cancelled = False

    run_id = str(uuid.uuid4())
    now = datetime.now()
    started_at = now.isoformat()
    ts_label = now.strftime("%Y%m%d_%H%M%S")

    # 创建新 session 用于此次执行
    from ..api.sessions import generate_workspace_name
    session_id = str(uuid.uuid4())
    workspace_name = generate_workspace_name(session_id)
    session_title = f"[定时任务] {task.name} - {ts_label}"
    session_data = SessionModel(
        session_id=session_id,
        title=session_title,
        messages=[],
        created_at=started_at,
        updated_at=started_at,
        username=task.username,
        workspace_name=workspace_name,
    )
    db.create_session(session_data)
    db.update_session_workspace_name(session_id, workspace_name)

    # 创建 run 记录
    run = ScheduledTaskRunModel(
        run_id=run_id,
        task_id=task_id,
        session_id=session_id,
        status="running",
        started_at=started_at,
    )
    db.add_scheduled_task_run(run)

    # 保存 user 消息
    user_msg = {
        "role": "user",
        "content": task.task_prompt,
        "timestamp": started_at,
    }
    db.add_message(session_id, user_msg)
    db.add_message_row(session_id, user_msg)

    logger.info(f"[{task_id[-5:]}] 定时任务开始执行 | run_id={run_id[-5:]} | session={session_id[-5:]} | prompt={task.task_prompt[:80]}")
    log_task_event(
        username=task.username,
        task_id=task_id,
        operation="execute_start",
        detail=f"定时任务开始执行: prompt={task.task_prompt[:80]}",
        run_id=run_id,
        session_id=session_id,
        started_at=started_at,
    )

    agent = None
    try:
        agent = await get_or_create_agent_for_session(
            session_id, task.username, workspace_name, enable_hitl=False
        )
        # 调用 agent 前再次确认任务未被暂停/删除（初始守卫之后、agent 调用之前仍可能被暂停）
        pre = db.get_scheduled_task(task_id)
        if not pre or not pre.enabled or task_id in stopping_tasks:
            raise _TaskAborted(f"任务已被暂停/删除: {task_id}")
        thread_id = f"sched-{task_id}-{run_id}"
        handler = _PauseAwareCallback(task_id)
        result = await agent.agent.ainvoke(
            {"messages": [HumanMessage(content=task.task_prompt)]},
            config={"configurable": {"thread_id": thread_id}, "callbacks": [handler]},
        )
        messages = result.get("messages", []) if isinstance(result, dict) else []
        final_content = ""
        for msg in reversed(messages):
            content = getattr(msg, "content", None)
            if content and getattr(msg, "type", "") == "ai":
                final_content = content
                break
        if not final_content and messages:
            last = messages[-1]
            final_content = getattr(last, "content", "") or str(last)

        result_summary = final_content[:5000] if final_content else "(无输出)"

        # 运行结束后再次校验：若任务在本次执行过程中被暂停/删除（DB 已禁用或已标记停止中），
        # 则中止提交，标记为取消，避免一个「已暂停」的任务记录为执行成功。
        current = db.get_scheduled_task(task_id)
        if not current or not current.enabled or task_id in stopping_tasks:
            cancelled = True
            logger.info(
                f"[{task_id[-5:]}] 任务在运行中已被暂停/删除，本次执行标记为取消（不记录成功）| run_id={run_id[-5:]}"
            )
        else:
            # 保存 assistant 消息
            assistant_msg = {
                "role": "assistant",
                "content": final_content,
                "timestamp": datetime.now().isoformat(),
            }
            db.add_message(session_id, assistant_msg)
            db.add_message_row(session_id, assistant_msg)

            finished_at = datetime.now().isoformat()
            db.update_scheduled_task_run(
                run_id, status="succeeded", finished_at=finished_at,
                result_summary=result_summary,
            )
            logger.info(f"[{task_id[-5:]}] 定时任务执行成功 | run_id={run_id[-5:]}")
            duration = (datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds()
            log_task_event(
                username=task.username,
                task_id=task_id,
                operation="execute_success",
                detail=f"任务执行成功: result={result_summary[:200]}",
                run_id=run_id,
                session_id=session_id,
                started_at=started_at,
                finished_at=finished_at,
                duration_seconds=round(duration, 3),
            )

    except asyncio.CancelledError:
        cancelled = True
        logger.info(f"[{task_id[-5:]}] 定时任务执行被取消（已暂停/删除） | run_id={run_id[-5:]}")
    except _TaskAborted:
        cancelled = True
        logger.info(f"[{task_id[-5:]}] 定时任务执行被中止（运行中已被暂停/删除） | run_id={run_id[-5:]}")
    except Exception as e:
        error_msg = str(e)[:2000]
        logger.error(f"[{task_id[-5:]}] 定时任务执行失败: {e}", exc_info=True)
        finished_at = datetime.now().isoformat()
        db.update_scheduled_task_run(
            run_id, status="failed", finished_at=finished_at,
            error_message=error_msg,
        )
        duration = (datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds()
        log_task_event(
            username=task.username,
            task_id=task_id,
            operation="execute_failed",
            detail=f"任务执行失败: error={error_msg[:200]}",
            run_id=run_id,
            session_id=session_id,
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=round(duration, 3),
        )
    finally:
        running_tasks.pop(task_id, None)
        stopping_tasks.discard(task_id)
        if agent is not None:
            remove_session_agent(session_id)
        # 更新 last_run_at 和 next_run_at
        scheduler = get_scheduler()
        job = scheduler.get_job(task_id)
        nrt = getattr(job, "next_run_time", None) if job else None
        next_run = nrt.strftime("%Y-%m-%d %H:%M:%S") if nrt else ""
        db.update_scheduled_task_run_times(task_id, started_at, next_run)
        if cancelled:
            db.update_scheduled_task_run(
                run_id, status="cancelled", finished_at=datetime.now().isoformat(),
                error_message="任务被暂停/删除，在途执行已取消",
            )
