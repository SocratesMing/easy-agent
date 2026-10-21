"""Scheduled task runtime - APScheduler AsyncIOScheduler singleton.

Manages cron-based scheduled tasks created by the AI agent. On startup,
loads all enabled tasks from DB and registers them with APScheduler.
When a task fires, creates a new session, invokes the agent non-streaming,
and records the result.
"""

import asyncio
import logging
from easy_agent.bootstrap.env import env_bool
import uuid
from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from easy_agent.db import get_database
from easy_agent.models.db import ScheduledTaskModel, ScheduledTaskRunModel, SessionModel
from easy_agent.utils.deployment import detect_worker_count as _detect_worker_count
from easy_agent.utils.distributed_lock import get_distributed_lock
from easy_agent.utils.task_logger import log_task_event
from langchain_core.callbacks import BaseCallbackHandler

logger = logging.getLogger("easy_agent.scheduler")

_agent_lifecycle_provider = None


def set_agent_lifecycle(provider) -> None:
    """注册返回 (get_or_create_agent, remove_agent) 的 provider。

    用 provider 而非直接函数对象，保证测试/运行时对 agent_manager 函数的替换生效；
    scheduler 不在模块级 import agent_manager（断 SCC 环）。
    """
    global _agent_lifecycle_provider
    _agent_lifecycle_provider = provider


def _require_agent_lifecycle():
    provider = _agent_lifecycle_provider
    if provider is None:
        # 兼容绕过 agent_manager 初始化的单测/独立调用：动态导入，不形成 AST 边。
        import importlib

        module = importlib.import_module("easy_agent.services.agent_manager")
        provider = lambda: (  # noqa: E731
            module.get_or_create_agent_for_session,
            module.remove_session_agent,
        )
        set_agent_lifecycle(provider)
    return provider()

_scheduler_instance: AsyncIOScheduler | None = None

# 正在执行的任务：task_id -> asyncio.Task，用于暂停/删除时中断在途运行
running_tasks: dict[str, "asyncio.Task"] = {}

# 已进入「停止中」状态的任务（被暂停或删除），用于在途执行结束后的二次校验，
# 确保即使 asyncio 取消未能立即中断 agent 调用，运行结果也不会被记为成功。
stopping_tasks: set[str] = set()

# 因分布式锁丢失（续约失败）被中止的任务：用于区分「暂停/删除」与「锁被抢占」
# 两种取消原因，运行记录里写出真实原因。
lock_lost_tasks: set[str] = set()


def get_task_workspace_name(db, task: ScheduledTaskModel) -> str:
    """解析定时任务的工作目录名。

    - 若任务由某个会话生成（session_id 指向有效会话），复用该会话的工作目录，
      保证后续定时任务产生的文件都落在该会话目录下，不新建目录。
    - 否则使用每个任务独立的工作目录 ``scheduled_{task_id}``。
    """
    if getattr(task, "workspace_name", ""):
        return task.workspace_name
    if task.session_id:
        session = db.get_session(task.session_id)
        if session and getattr(session, "workspace_name", ""):
            return session.workspace_name
    return f"scheduled_{task.task_id}"


def task_lock_name(task_id: str) -> str:
    """定时任务的分布式锁逻辑名（最终 key 由锁模块拼上配置里的 key_prefix）。

    锁覆盖「本次运行」全程：同一 task 在任意实例上并发触发（cron 到点、手动
    触发、本机重复触发）时，只有一个实例能真正执行。
    """
    return f"scheduled_task:{task_id}"


class _TaskAborted(Exception):
    """任务在运行过程中被暂停/删除，主动中止 agent 调用。"""


async def _renew_task_lock(handle, task_id: str, running: "asyncio.Task | None") -> None:
    """锁续约心跳：续约失败说明锁已被其它实例抢占，取消在途执行避免重复写入。"""
    try:
        while True:
            await asyncio.sleep(handle.renew_interval_seconds)
            if not handle.renew():
                logger.error(
                    f"[{task_id[-5:]}] 分布式锁已丢失（过期后被其它实例接管），中止本次执行"
                )
                lock_lost_tasks.add(task_id)
                if running is not None and not running.done():
                    running.cancel()
                return
    except asyncio.CancelledError:
        return


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
    lock = get_distributed_lock()
    try:
        lock.cleanup_expired()
    except Exception as e:
        logger.warning(f"清理过期分布式锁失败（忽略）: {e}")
    if lock.enabled:
        logger.info(
            f"🔒 分布式锁已启用 | key_prefix={lock.config.key_prefix} | "
            f"ttl={lock.config.ttl_seconds}s"
        )
    else:
        workers = _detect_worker_count()
        if workers > 1 and not env_bool("EASY_ALLOW_DUPLICATE_SCHEDULER"):
            # 多 worker/多副本 + 未开分布式锁 = 同一 cron 被 N 个实例各执行一次：
            # 重复扣费、重复写会话/消息。默认拒绝注册调度任务（fail-closed），
            # 单实例仍可正常工作；确需关闭锁请显式设置 EASY_ALLOW_DUPLICATE_SCHEDULER=1。
            logger.error(
                "❌ 检测到多 worker/多副本（%s）但 distributed_lock.enabled=false："
                "为避免定时任务重复执行，本次不注册任何定时任务。"
                "请开启 distributed_lock，或显式设置 EASY_ALLOW_DUPLICATE_SCHEDULER=1。",
                workers,
            )
            return
        logger.info(
            "🔒 分布式锁未启用（单实例部署无需开启；多 pod 部署请设置 distributed_lock.enabled）"
        )
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


#: LangGraph 在运行结果里放待处理中断的键（公开常量 ``langgraph.constants.INTERRUPT`` 已弃用）
_INTERRUPT_KEY = "__interrupt__"


def _raise_if_waiting_for_approval(result: Any) -> None:
    """运行停在人工审批处就算失败：定时任务没人审批（``enable_hitl=False``）。

    不能按成功记录：结束后 thread 会被删掉，等待审批的操作（例如写记忆）就此丢失，
    却显示为执行成功。
    """
    interrupts = result.get(_INTERRUPT_KEY) if isinstance(result, dict) else None
    if not interrupts:
        return
    names = []
    for item in interrupts:
        value = getattr(item, "value", None)
        requests = value.get("action_requests") if isinstance(value, dict) else None
        names.extend(str(action.get("name")) for action in requests or () if isinstance(action, dict))
    raise RuntimeError(
        f"运行停在了人工审批处（{', '.join(names) or '未知操作'}）：定时任务没有人工审批，本次未完成"
    )


async def _execute_task(task_id: str):
    """定时任务执行核心逻辑：创建新 session → 调用 agent → 记录结果。"""
    from langchain_core.messages import HumanMessage

    get_or_create_agent_for_session, remove_session_agent = _require_agent_lifecycle()

    db = get_database()
    task = db.get_scheduled_task(task_id)
    if not task or not task.enabled:
        return

    # 多实例部署：同一 cron 会在每个 pod 各触发一次，先抢分布式锁，只有抢到的实例
    # 真正执行。未启用分布式锁（单实例部署）时 acquired 恒为 True，行为与之前一致。
    lock_handle = get_distributed_lock().acquire(task_lock_name(task_id))
    if not lock_handle.acquired:
        logger.info(
            f"[{task_id[-5:]}] 任务已在其它实例执行中，本次触发跳过（分布式锁）| owner={lock_handle.owner}"
        )
        log_task_event(
            username=task.username,
            task_id=task_id,
            operation="skip_locked",
            detail="其它实例持有分布式锁，本次触发跳过",
        )
        return

    # 抢锁成功也要留痕：多 pod 部署下这是判断「本月次执行到底加没加锁」的唯一线索，
    # 只记失败/跳过会让日志里完全看不到分布式锁的存在。
    if lock_handle.enabled:
        logger.info(
            f"[{task_id[-5:]}] 🔒 已获取分布式锁 | task={task.name} | key={lock_handle.key} | "
            f"owner={lock_handle.owner} | ttl={lock_handle.ttl_seconds}s"
        )

    # 登记在途运行，便于暂停/删除时中断
    running = asyncio.current_task()
    running_tasks[task_id] = running
    cancelled = False

    # 执行时长可能超过锁的 TTL，需周期性续约；续约失败说明锁已被其它实例接管
    lock_heartbeat = (
        asyncio.create_task(_renew_task_lock(lock_handle, task_id, running))
        if lock_handle.enabled
        else None
    )

    run_id = str(uuid.uuid4())
    now = datetime.now()
    started_at = now.isoformat()
    ts_label = now.strftime("%Y%m%d_%H%M%S")

    # 创建新 session 用于此次执行，但工作目录与任务绑定（复用创建会话的目录或独立目录），
    # 确保多次运行都写入同一工作目录，不再为每次运行新建目录。
    session_id = str(uuid.uuid4())
    workspace_name = get_task_workspace_name(db, task)
    db.update_scheduled_task_workspace(task.task_id, workspace_name)
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
    thread_id = None
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
        from easy_agent.agent.run_config import build_run_config, run_context_kwargs

        # 与聊天流同一套调用参数：共享编译图必须带 SessionContext，否则 backend 在
        # before_agent 读记忆时就抛「缺少运行期 SessionContext」，任务直接失败。
        result = await agent.agent.ainvoke(
            {"messages": [HumanMessage(content=task.task_prompt)]},
            config=build_run_config(agent, thread_id, callbacks=[handler]),
            **run_context_kwargs(agent),
        )
        _raise_if_waiting_for_approval(result)
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
        lock_lost = task_id in lock_lost_tasks
        lock_lost_tasks.discard(task_id)
        if lock_heartbeat is not None:
            lock_heartbeat.cancel()
        running_tasks.pop(task_id, None)
        stopping_tasks.discard(task_id)
        if agent is not None:
            remove_session_agent(session_id)
        # 每次运行一个独立 thread，且关闭了 HITL，运行结束后没有需要恢复的状态；
        # checkpointer 不会自动清理，这里删掉（DA-03）
        if agent is not None and thread_id:
            from easy_agent.persistence.checkpoints import adelete_agent_thread

            await adelete_agent_thread(agent, thread_id)
        # 更新 last_run_at 和 next_run_at
        scheduler = get_scheduler()
        job = scheduler.get_job(task_id)
        nrt = getattr(job, "next_run_time", None) if job else None
        next_run = nrt.strftime("%Y-%m-%d %H:%M:%S") if nrt else ""
        db.update_scheduled_task_run_times(task_id, started_at, next_run)
        if cancelled:
            cancel_reason = (
                "分布式锁已丢失（可能已被其它实例接管），在途执行已取消"
                if lock_lost
                else "任务被暂停/删除，在途执行已取消"
            )
            db.update_scheduled_task_run(
                run_id, status="cancelled", finished_at=datetime.now().isoformat(),
                error_message=cancel_reason,
            )
        # 最后释放锁：等本次运行的记录都写完之后，其它实例才能接管这个任务
        try:
            released = lock_handle.release()
            if released and lock_handle.enabled:
                logger.info(f"[{task_id[-5:]}] 🔓 已释放分布式锁 | task={task.name}")
        except Exception as e:
            logger.warning(
                f"[{task_id[-5:]}] 释放分布式锁失败（TTL 到期后会自动失效）: {e}"
            )
