"""Scheduled task runtime - APScheduler AsyncIOScheduler singleton.

Manages cron-based scheduled tasks created by the AI agent. On startup,
loads all enabled tasks from DB and registers them with APScheduler.
When a task fires, creates a new session, invokes the agent non-streaming,
and records the result.
"""

import logging
import uuid
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..db import get_database
from ..models.db import ScheduledTaskModel, ScheduledTaskRunModel, SessionModel
from ..utils.task_logger import log_task_event

logger = logging.getLogger("easy_agent.scheduler")

_scheduler_instance: AsyncIOScheduler | None = None


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
    logger.info(f"定时任务已注册 | task_id={task.task_id} | name={task.name} | cron={task.schedule_cron} | next_run={next_run}")
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
    try:
        scheduler.remove_job(task_id)
        logger.info(f"定时任务已移除 | task_id={task_id}")
        if task is not None:
            log_task_event(
                username=task.username,
                task_id=task_id,
                operation="unregister",
                detail="任务从调度器移除",
            )
    except Exception:
        pass


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
        thread_id = f"sched-{task_id}-{run_id}"
        result = await agent.agent.ainvoke(
            {"messages": [HumanMessage(content=task.task_prompt)]},
            config={"configurable": {"thread_id": thread_id}},
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
        if agent is not None:
            remove_session_agent(session_id)
        # 更新 last_run_at 和 next_run_at
        scheduler = get_scheduler()
        job = scheduler.get_job(task_id)
        nrt = getattr(job, "next_run_time", None) if job else None
        next_run = nrt.strftime("%Y-%m-%d %H:%M:%S") if nrt else ""
        db.update_scheduled_task_run_times(task_id, started_at, next_run)
