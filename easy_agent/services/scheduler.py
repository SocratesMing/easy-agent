"""Scheduled task runtime - APScheduler AsyncIOScheduler singleton.

Manages cron-based scheduled tasks created by the AI agent. On startup,
loads all enabled tasks from DB and registers them with APScheduler.
When a task fires, creates a new session, invokes the agent non-streaming,
and records the result.
"""

import asyncio
import logging
from dataclasses import dataclass
from easy_agent.bootstrap.env import env_bool
import uuid
from datetime import datetime
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from easy_agent.db.providers import (
    get_message_repository,
    get_scheduled_task_repository,
    get_session_repository,
)
from easy_agent.models.db import ScheduledTaskModel, ScheduledTaskRunModel, SessionModel
from easy_agent.utils.deployment import detect_worker_count as _detect_worker_count
from easy_agent.persistence.distributed_lock import get_distributed_lock
from easy_agent.utils.blocking import run_blocking
from easy_agent.utils.task_logger import log_task_event
from langchain_core.callbacks import BaseCallbackHandler
from easy_agent.agent.run_config import build_run_config, run_context_kwargs

logger = logging.getLogger("easy_agent.scheduler")

_agent_lifecycle_provider = None
_reconcile_task: asyncio.Task | None = None
RECONCILE_INTERVAL_SECONDS = 15.0


def set_agent_lifecycle(provider) -> None:
    """注册返回 (get_or_create_agent, remove_agent) 的 provider。

    用 provider 而非直接函数对象，保证测试/运行时对 agent_manager 函数的替换生效；
    scheduler 不导入 agent_manager（断 SCC 环），由 agent_manager 导入时调用本函数注册。
    """
    global _agent_lifecycle_provider
    _agent_lifecycle_provider = provider


def _require_agent_lifecycle():
    provider = _agent_lifecycle_provider
    if provider is None:
        raise RuntimeError(
            "定时任务拿不到 agent 生命周期：easy_agent.services.agents.manager 还没有导入"
            "（它在导入时调用 set_agent_lifecycle 注册，应用启动路径会自动完成）"
        )
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
            if not await run_blocking(handle.renew):
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
    global _scheduler_instance, _reconcile_task
    if _reconcile_task is not None and not _reconcile_task.done():
        _reconcile_task.cancel()
    _reconcile_task = None
    if _scheduler_instance is not None:
        _scheduler_instance.shutdown(wait=False)
        _scheduler_instance = None
        logger.info("AsyncIOScheduler 已关闭")


def preview_next_run(schedule_cron: str) -> str:
    """根据 cron 计算下一次触发时间，不向本进程调度器注册 job。"""
    trigger = CronTrigger.from_crontab(schedule_cron, timezone="Asia/Shanghai")
    nxt = trigger.get_next_fire_time(None, datetime.now(trigger.timezone))
    return nxt.strftime("%Y-%m-%d %H:%M:%S") if nxt else ""


def _read_enabled_scheduled_tasks() -> list[ScheduledTaskModel]:
    """同步查库：读取数据库里全部启用的定时任务。

    只碰数据库、不碰进程内状态；只应通过 ``asyncio.to_thread`` 调用，避免同步驱动
    （SQLite 单连接 + RLock、MySQL 连接池）占住事件循环。
    """
    return get_scheduled_task_repository().list_all_enabled_scheduled_tasks()


def _read_scheduled_task(task_id: str) -> ScheduledTaskModel | None:
    """同步查库：按任务 ID 读取任务的最新状态（对账复核用，只应通过 asyncio.to_thread 调用）。"""
    return get_scheduled_task_repository().get_scheduled_task(task_id)


def _is_stopping_in_flight(task_id: str) -> bool:
    """任务是否真的在停止中：有停止标记，且本进程有在途运行正在被取消。

    ``unregister_scheduled_task`` 对没有在途运行的任务也会写停止标记（要等下一次执行收尾
    才清），这种陈旧标记只该清掉、不能拦住重新启用：否则任务暂停后再开启就永远不会被
    注册。有在途运行时保留标记，交给在途执行收尾时清理。
    """
    if task_id not in stopping_tasks:
        return False
    running = running_tasks.get(task_id)
    return running is not None and not running.done()


async def reconcile_scheduled_tasks() -> int:
    """把本进程调度器对齐到数据库里启用的任务。

    每个运行中的调度器都执行：新建任务不必落在「当时处理对话的那台机器」上才会触发。
    执行本身仍由每任务分布式锁保证只跑一次。``manual-`` 前缀的手动触发 job 不参与对账。

    事件循环约束：数据库层是同步的，读库 / 写库都放进线程；对齐（读 job 列表、注册 /
    注销、``stopping_tasks`` 等进程内状态）留在事件循环上。注册新任务前还要按任务 ID
    复核「是否正在停止」和「库里是否仍启用」（读库放线程），避免把批量读之后刚被停用的
    任务装回去。调度器未初始化或未启动时不读库，直接返回 0。
    """
    scheduler = _scheduler_instance
    if scheduler is None or not scheduler.running:
        return 0

    # 第一步（线程）：批量读启用的任务；对齐只留在事件循环上做。
    tasks = await asyncio.to_thread(_read_enabled_scheduled_tasks)
    if not scheduler.running:
        # 读库期间调度器被关闭：没有可对齐的对象
        return 0
    wanted = {task.task_id: task for task in tasks}
    existing = {
        job.id
        for job in scheduler.get_jobs()
        if not str(job.id).startswith("manual-")
    }
    added = 0
    for task_id in wanted:
        if task_id in existing:
            continue
        # 复核一：暂停/删除请求（unregister）已经把任务标记为停止中，不要装回去。
        # 没有在途运行的标记是上一次暂停留下的陈旧值，清掉后按最新库状态处理。
        if task_id in stopping_tasks:
            if _is_stopping_in_flight(task_id):
                logger.info(f"定时任务对账跳过停止中的任务 | task_id={task_id}")
                continue
            stopping_tasks.discard(task_id)
        # 复核二（线程）：按任务 ID 读最新状态。批量读到这里之间任务可能刚被停用/删除。
        latest = await asyncio.to_thread(_read_scheduled_task, task_id)
        if latest is None or not latest.enabled or task_id in stopping_tasks:
            logger.info(f"定时任务对账跳过已停用/删除的任务 | task_id={task_id}")
            continue
        next_run = register_scheduled_task(latest)
        if next_run:
            # 写库同样放进线程，不占事件循环
            await asyncio.to_thread(
                get_scheduled_task_repository().update_scheduled_task_run_times,
                task_id,
                latest.last_run_at or "",
                next_run,
            )
            added += 1
    removed = existing - set(wanted)
    for job_id in removed:
        unregister_scheduled_task(job_id)
    if added or removed:
        logger.info(
            "定时任务对账 | 新增 %s | 移除 %s | 启用 %s",
            added,
            len(removed),
            len(wanted),
        )
    return added


async def _reconcile_loop() -> None:
    while True:
        try:
            await reconcile_scheduled_tasks()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 对账失败不拖垮调度器
            logger.warning(f"定时任务对账失败: {exc}")
        await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)


def start_scheduled_task_reconcile() -> None:
    """启动周期对账。调度器已 start 之后调用。"""
    global _reconcile_task
    if _reconcile_task is not None and not _reconcile_task.done():
        return
    _reconcile_task = asyncio.get_running_loop().create_task(
        _reconcile_loop(), name="scheduled-task-reconcile"
    )


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
    scheduled_repo = get_scheduled_task_repository()
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
    tasks = scheduled_repo.list_all_enabled_scheduled_tasks()
    count = 0
    for task in tasks:
        next_run = register_scheduled_task(task)
        if next_run:
            scheduled_repo.update_scheduled_task_run_times(
                task.task_id, task.last_run_at, next_run
            )
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


@dataclass
class _TaskRun:
    """一次定时任务执行「建档」阶段生成的信息（供执行 / 收尾复用）。"""

    run_id: str
    started_at: str
    session_id: str
    workspace_name: str
    # 执行段回填：Agent 与 thread 在收尾清理时需要，即使执行中途抛异常也要能拿到
    agent: Any = None
    thread_id: str | None = None
    final_content: str = ""


async def _acquire_task_lock(task: ScheduledTaskModel):
    """抢本次执行的分布式锁；抢不到记录 skip 事件并返回 None（调用方直接结束）。

    多实例部署：同一 cron 会在每个 pod 各触发一次，只有抢到锁的实例真正执行。
    未启用分布式锁（单实例部署）时 acquired 恒为 True，行为与改造前一致。
    """
    lock_handle = await run_blocking(
        get_distributed_lock().acquire, task_lock_name(task.task_id)
    )
    if not lock_handle.acquired:
        logger.info(
            f"[{task.task_id[-5:]}] 任务已在其它实例执行中，本次触发跳过（分布式锁）| owner={lock_handle.owner}"
        )
        log_task_event(
            username=task.username,
            task_id=task.task_id,
            operation="skip_locked",
            detail="其它实例持有分布式锁，本次触发跳过",
        )
        return None

    # 抢锁成功也要留痕：多 pod 部署下这是判断「本次执行到底加没加锁」的唯一线索，
    # 只记失败/跳过会让日志里完全看不到分布式锁的存在。
    if lock_handle.enabled:
        logger.info(
            f"[{task.task_id[-5:]}] 🔒 已获取分布式锁 | task={task.name} | key={lock_handle.key} | "
            f"owner={lock_handle.owner} | ttl={lock_handle.ttl_seconds}s"
        )
    return lock_handle


async def _create_task_run(
    *,
    task: ScheduledTaskModel,
    scheduled_repo,
    session_repo,
    message_repo,
) -> _TaskRun:
    """建档：创建新 session、run 记录与首条 user 消息（全部写库完成后返回）。

    顺序约束：本函数内的写库全部完成后，调用方才会登记 ``running_tasks`` /
    启动锁心跳（见 :func:`_execute_task` 内注释）。
    """
    run_id = str(uuid.uuid4())
    now = datetime.now()
    started_at = now.isoformat()
    ts_label = now.strftime("%Y%m%d_%H%M%S")

    # 创建新 session 用于此次执行，但工作目录与任务绑定（复用创建会话的目录或独立目录），
    # 确保多次运行都写入同一工作目录，不再为每次运行新建目录。
    session_id = str(uuid.uuid4())
    workspace_name = await run_blocking(get_task_workspace_name, session_repo, task)
    await run_blocking(
        scheduled_repo.update_scheduled_task_workspace, task.task_id, workspace_name
    )
    session_data = SessionModel(
        session_id=session_id,
        title=f"[定时任务] {task.name} - {ts_label}",
        messages=[],
        created_at=started_at,
        updated_at=started_at,
        username=task.username,
        workspace_name=workspace_name,
    )
    await run_blocking(session_repo.create_session, session_data)
    await run_blocking(
        session_repo.update_session_workspace_name, session_id, workspace_name
    )

    # 创建 run 记录
    run = ScheduledTaskRunModel(
        run_id=run_id,
        task_id=task.task_id,
        session_id=session_id,
        status="running",
        started_at=started_at,
    )
    await run_blocking(scheduled_repo.add_scheduled_task_run, run)

    # 保存 user 消息
    user_msg = {
        "role": "user",
        "content": task.task_prompt,
        "timestamp": started_at,
    }
    await run_blocking(message_repo.add_message, session_id, user_msg)

    return _TaskRun(
        run_id=run_id,
        started_at=started_at,
        session_id=session_id,
        workspace_name=workspace_name,
    )


def _extract_final_content(messages: list) -> str:
    """取本次运行的最终 assistant 文本（无副作用）。

    优先最后一条有内容的 AI 消息；都没有时兜底最后一条消息的内容。
    """
    final_content = ""
    for msg in reversed(messages):
        content = getattr(msg, "content", None)
        if content and getattr(msg, "type", "") == "ai":
            final_content = content
            break
    if not final_content and messages:
        last = messages[-1]
        final_content = getattr(last, "content", "") or str(last)
    return final_content


async def _invoke_task_agent(
    *,
    task: ScheduledTaskModel,
    execution: _TaskRun,
    get_or_create_agent_for_session,
    scheduled_repo,
) -> None:
    """执行段：创建 Agent → 二次「暂停/删除」校验 → 调一次共享图；结果回填 execution。

    顺序约束：二次校验必须在调用 Agent 之前（初始守卫之后、agent 调用之前仍可能被暂停）。
    异常原样上抛，由调用方按原策略记 failed / cancelled。
    """
    from langchain_core.messages import HumanMessage

    execution.agent = await get_or_create_agent_for_session(
        execution.session_id, task.username, execution.workspace_name, enable_hitl=False
    )
    # 调用 agent 前再次确认任务未被暂停/删除（初始守卫之后、agent 调用之前仍可能被暂停）
    pre = await run_blocking(scheduled_repo.get_scheduled_task, task.task_id)
    if not pre or not pre.enabled or task.task_id in stopping_tasks:
        raise _TaskAborted(f"任务已被暂停/删除: {task.task_id}")
    execution.thread_id = f"sched-{task.task_id}-{execution.run_id}"
    handler = _PauseAwareCallback(task.task_id)

    # 与聊天流同一套调用参数：共享编译图必须带 SessionContext，否则 backend 在
    # before_agent 读记忆时就抛「缺少运行期 SessionContext」，任务直接失败。
    result = await execution.agent.agent.ainvoke(
        {"messages": [HumanMessage(content=task.task_prompt)]},
        config=build_run_config(execution.agent, execution.thread_id, callbacks=[handler]),
        **run_context_kwargs(execution.agent),
    )
    _raise_if_waiting_for_approval(result)
    messages = result.get("messages", []) if isinstance(result, dict) else []
    execution.final_content = _extract_final_content(messages)


async def _commit_task_success(
    *,
    task: ScheduledTaskModel,
    execution: _TaskRun,
    scheduled_repo,
    message_repo,
) -> bool:
    """执行成功后二次校验并提交结果；若任务已被暂停/删除，返回 True（本次按取消收尾）。

    运行结束后再次校验：若任务在本次执行过程中被暂停/删除（DB 已禁用或已标记停止中），
    则中止提交，标记为取消，避免一个「已暂停」的任务记录为执行成功。
    """
    current = await run_blocking(scheduled_repo.get_scheduled_task, task.task_id)
    if not current or not current.enabled or task.task_id in stopping_tasks:
        logger.info(
            f"[{task.task_id[-5:]}] 任务在运行中已被暂停/删除，本次执行标记为取消（不记录成功）| run_id={execution.run_id[-5:]}"
        )
        return True

    # 保存 assistant 消息
    assistant_msg = {
        "role": "assistant",
        "content": execution.final_content,
        "timestamp": datetime.now().isoformat(),
    }
    await run_blocking(message_repo.add_message, execution.session_id, assistant_msg)

    finished_at = datetime.now().isoformat()
    result_summary = execution.final_content[:5000] if execution.final_content else "(无输出)"
    await run_blocking(
        scheduled_repo.update_scheduled_task_run,
        execution.run_id, status="succeeded", finished_at=finished_at,
        result_summary=result_summary,
    )
    logger.info(f"[{task.task_id[-5:]}] 定时任务执行成功 | run_id={execution.run_id[-5:]}")
    duration = (datetime.fromisoformat(finished_at) - datetime.fromisoformat(execution.started_at)).total_seconds()
    log_task_event(
        username=task.username,
        task_id=task.task_id,
        operation="execute_success",
        detail=f"任务执行成功: result={result_summary[:200]}",
        run_id=execution.run_id,
        session_id=execution.session_id,
        started_at=execution.started_at,
        finished_at=finished_at,
        duration_seconds=round(duration, 3),
    )
    return False


async def _record_task_failure(
    *, task: ScheduledTaskModel, execution: _TaskRun, error: Exception, scheduled_repo
) -> None:
    """执行异常：落 failed 终态并记审计事件（写库异常仍原样上抛，与原实现一致）。"""
    error_msg = str(error)[:2000]
    finished_at = datetime.now().isoformat()
    await run_blocking(
        scheduled_repo.update_scheduled_task_run,
        execution.run_id, status="failed", finished_at=finished_at,
        error_message=error_msg,
    )
    duration = (datetime.fromisoformat(finished_at) - datetime.fromisoformat(execution.started_at)).total_seconds()
    log_task_event(
        username=task.username,
        task_id=task.task_id,
        operation="execute_failed",
        detail=f"任务执行失败: error={error_msg[:200]}",
        run_id=execution.run_id,
        session_id=execution.session_id,
        started_at=execution.started_at,
        finished_at=finished_at,
        duration_seconds=round(duration, 3),
    )


async def _release_task_lock(task: ScheduledTaskModel, lock_handle) -> None:
    """最后释放锁：等本次运行的记录都写完之后，其它实例才能接管这个任务。"""
    try:
        released = await run_blocking(lock_handle.release)
        if released and lock_handle.enabled:
            logger.info(f"[{task.task_id[-5:]}] 🔓 已释放分布式锁 | task={task.name}")
    except Exception as e:
        logger.warning(
            f"[{task.task_id[-5:]}] 释放分布式锁失败（TTL 到期后会自动失效）: {e}"
        )


async def _finalize_task_run(
    *,
    task: ScheduledTaskModel,
    execution: _TaskRun | None,
    lock_handle,
    cancelled: bool,
    lock_heartbeat,
    remove_session_agent,
    scheduled_repo,
) -> None:
    """收尾（必走）：停心跳 → 清进程内状态 / Agent / thread → 回写运行时间与取消原因 → 释放锁。"""
    task_id = task.task_id
    lock_lost = task_id in lock_lost_tasks
    lock_lost_tasks.discard(task_id)
    if lock_heartbeat is not None:
        lock_heartbeat.cancel()
    running_tasks.pop(task_id, None)
    stopping_tasks.discard(task_id)

    if execution is None:  # 建档前就退出的极端路径：没有 session/run，直接还锁
        await _release_task_lock(task, lock_handle)
        return

    if execution.agent is not None:
        remove_session_agent(execution.session_id)
    # 每次运行一个独立 thread，且关闭了 HITL，运行结束后没有需要恢复的状态；
    # checkpointer 不会自动清理，这里删掉（DA-03）
    if execution.agent is not None and execution.thread_id:
        from easy_agent.persistence.checkpoints import adelete_agent_thread  # 延迟导入：测试 monkeypatch 语义：按模块属性替换，函数内导入才能取到替换后的对象

        await adelete_agent_thread(execution.agent, execution.thread_id)

    # 更新 last_run_at 和 next_run_at
    scheduler = get_scheduler()
    job = scheduler.get_job(task_id)
    nrt = getattr(job, "next_run_time", None) if job else None
    next_run = nrt.strftime("%Y-%m-%d %H:%M:%S") if nrt else ""
    await run_blocking(
        scheduled_repo.update_scheduled_task_run_times,
        task_id, execution.started_at, next_run,
    )
    if cancelled:
        cancel_reason = (
            "分布式锁已丢失（可能已被其它实例接管），在途执行已取消"
            if lock_lost
            else "任务被暂停/删除，在途执行已取消"
        )
        await run_blocking(
            scheduled_repo.update_scheduled_task_run,
            execution.run_id, status="cancelled", finished_at=datetime.now().isoformat(),
            error_message=cancel_reason,
        )
    await _release_task_lock(task, lock_handle)


async def _execute_task(task_id: str):
    """定时任务执行核心：抢锁 → 建档 → 执行 → 收尾（README: 三段职责见各私有函数）。"""
    get_or_create_agent_for_session, remove_session_agent = _require_agent_lifecycle()

    # 数据库层是同步的：查库 / 写库都经 run_blocking 放进线程，不占事件循环；
    # running_tasks 等进程内状态只在事件循环上读写。
    scheduled_repo = get_scheduled_task_repository()
    session_repo = get_session_repository()
    message_repo = get_message_repository()
    task = await run_blocking(scheduled_repo.get_scheduled_task, task_id)
    if not task or not task.enabled:
        return

    # ① 抢锁：抢不到直接结束（已记录 skip 事件）
    lock_handle = await _acquire_task_lock(task)
    if lock_handle is None:
        return

    # ② 建档：session / run 记录 / user 消息（全部写库完成后才进入下一步）
    execution = await _create_task_run(
        task=task,
        scheduled_repo=scheduled_repo,
        session_repo=session_repo,
        message_repo=message_repo,
    )

    logger.info(f"[{task_id[-5:]}] 定时任务开始执行 | run_id={execution.run_id[-5:]} | session={execution.session_id[-5:]} | prompt={task.task_prompt[:80]}")
    log_task_event(
        username=task.username,
        task_id=task_id,
        operation="execute_start",
        detail=f"定时任务开始执行: prompt={task.task_prompt[:80]}",
        run_id=execution.run_id,
        session_id=execution.session_id,
        started_at=execution.started_at,
    )

    # 顺序约束（6150ca1）：登记在途运行、启动锁心跳必须放在上面的建档写库之后。
    # 那几处 await 不在 try/finally 里，提前登记的话，被暂停/删除取消或建档出错时
    # 都走不到收尾（锁不释放、心跳一直续约）；登记之前被暂停的，执行段二次校验会拦下。
    running = asyncio.current_task()
    running_tasks[task_id] = running
    cancelled = False

    # 执行时长可能超过锁的 TTL，需周期性续约；续约失败说明锁已被其它实例接管
    lock_heartbeat = (
        asyncio.create_task(_renew_task_lock(lock_handle, task_id, running))
        if lock_handle.enabled
        else None
    )

    try:
        # ③ 执行：建 Agent → 二次校验 → 调共享图，成功后提交结果
        await _invoke_task_agent(
            task=task,
            execution=execution,
            get_or_create_agent_for_session=get_or_create_agent_for_session,
            scheduled_repo=scheduled_repo,
        )
        cancelled = await _commit_task_success(
            task=task,
            execution=execution,
            scheduled_repo=scheduled_repo,
            message_repo=message_repo,
        )
    except asyncio.CancelledError:
        cancelled = True
        logger.info(f"[{task_id[-5:]}] 定时任务执行被取消（已暂停/删除） | run_id={execution.run_id[-5:]}")
    except _TaskAborted:
        cancelled = True
        logger.info(f"[{task_id[-5:]}] 定时任务执行被中止（运行中已被暂停/删除） | run_id={execution.run_id[-5:]}")
    except Exception as e:
        logger.error(f"[{task_id[-5:]}] 定时任务执行失败: {e}", exc_info=True)
        await _record_task_failure(
            task=task, execution=execution, error=e, scheduled_repo=scheduled_repo
        )
    finally:
        # ④ 收尾：不论成功/失败/取消都走同一段
        await _finalize_task_run(
            task=task,
            execution=execution,
            lock_handle=lock_handle,
            cancelled=cancelled,
            lock_heartbeat=lock_heartbeat,
            remove_session_agent=remove_session_agent,
            scheduled_repo=scheduled_repo,
        )
