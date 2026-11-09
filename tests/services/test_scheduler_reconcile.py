"""``reconcile_scheduled_tasks`` 的事件循环模型与并发复核测试（T14 第 1 条）。

对账拆成两步：读库（``asyncio.to_thread``）+ 对齐（事件循环）。本文件用假调度器 /
假数据库验证：

- 正常对齐：新增注册、失效移除、``added`` 计数，``manual-`` 手动触发的 job 不参与；
- 停用竞态：批量读之后任务被停用/删除 → 复核阶段（按任务 ID 再读一次库）不注册；
- 停止中：``stopping_tasks`` 里有在途运行的任务不注册；没有在途运行的陈旧标记会被清掉
  （否则暂停后再启用就永远不会被注册）；
- 调度器未初始化/未运行时完全不读库；
- 读库 / 写库都发生在事件循环之外。

不启动真实 APScheduler 线程，也不碰真实数据库。
"""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from easy_agent.models.db import ScheduledTaskModel
from easy_agent.services import scheduler as sched

NEXT_RUN = "2026-11-02 09:00:00"


def _task(
    task_id: str, *, enabled: int = 1, last_run_at: str = ""
) -> ScheduledTaskModel:
    return ScheduledTaskModel(
        task_id=task_id,
        username="testuser",
        name=f"任务-{task_id}",
        schedule_cron="0 9 * * *",
        task_prompt="ping",
        enabled=enabled,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        last_run_at=last_run_at,
    )


class _FakeDB:
    """假数据库：可注入批量读结果，并按任务 ID 返回不同的「最新状态」。"""

    def __init__(self) -> None:
        self.enabled: list[ScheduledTaskModel] = []
        self.latest: dict[str, ScheduledTaskModel | None] = {}
        self.calls: list[str] = []
        self.run_time_updates: list[tuple[str, str, str]] = []

    def list_all_enabled_scheduled_tasks(self) -> list[ScheduledTaskModel]:
        self.calls.append("list_all_enabled_scheduled_tasks")
        return list(self.enabled)

    def get_scheduled_task(self, task_id: str) -> ScheduledTaskModel | None:
        self.calls.append(f"get_scheduled_task:{task_id}")
        if task_id in self.latest:
            return self.latest[task_id]
        return next((task for task in self.enabled if task.task_id == task_id), None)

    def update_scheduled_task_run_times(
        self, task_id: str, last_run_at: str, next_run: str
    ) -> None:
        self.calls.append(f"update_scheduled_task_run_times:{task_id}")
        self.run_time_updates.append((task_id, last_run_at, next_run))


class _FakeScheduler:
    """只提供对账用到的属性；不启动真实调度线程。"""

    def __init__(self, job_ids=(), running: bool = True) -> None:
        self.running = running
        self.jobs = [SimpleNamespace(id=job_id) for job_id in job_ids]

    def get_jobs(self):
        return list(self.jobs)


@pytest.fixture()
def env(monkeypatch):
    """替换调度器 / 数据库 / 进程内集合，返回测试用的记录容器。"""
    db = _FakeDB()
    scheduler = _FakeScheduler()
    registered: list[ScheduledTaskModel] = []
    unregistered: list[str] = []

    def fake_register(task: ScheduledTaskModel) -> str:
        registered.append(task)
        return NEXT_RUN

    def fake_unregister(task_id: str, task=None) -> None:
        unregistered.append(task_id)

    monkeypatch.setattr(sched, "get_scheduled_task_repository", lambda: db)
    monkeypatch.setattr(sched, "get_session_repository", lambda: db)
    monkeypatch.setattr(sched, "get_message_repository", lambda: db)
    monkeypatch.setattr(sched, "register_scheduled_task", fake_register)
    monkeypatch.setattr(sched, "unregister_scheduled_task", fake_unregister)
    monkeypatch.setattr(sched, "_scheduler_instance", scheduler)
    # 换成用例专属的 set / dict，避免跨用例污染模块级进程内状态
    monkeypatch.setattr(sched, "stopping_tasks", set())
    monkeypatch.setattr(sched, "running_tasks", {})
    return SimpleNamespace(
        db=db,
        scheduler=scheduler,
        registered=registered,
        unregistered=unregistered,
    )


@pytest.mark.asyncio
async def test_reconcile_registers_new_and_removes_stale_jobs(env):
    """正常对齐：库里新启用的任务注册上，调度器里已失效的 job 移除。"""
    env.db.enabled = [_task("t-new", last_run_at="2026-11-01 09:00:00")]
    env.scheduler.jobs = [
        SimpleNamespace(id="t-old"),
        SimpleNamespace(id="manual-t-old-101010"),
    ]

    added = await sched.reconcile_scheduled_tasks()

    assert added == 1
    assert [task.task_id for task in env.registered] == ["t-new"]
    # manual- 前缀的手动触发 job 不参与对账
    assert env.unregistered == ["t-old"]
    assert env.db.run_time_updates == [
        ("t-new", "2026-11-01 09:00:00", NEXT_RUN)
    ]


@pytest.mark.asyncio
async def test_already_registered_task_is_left_alone(env):
    """已在调度器里的任务不做任何事，也不再额外读库。"""
    env.db.enabled = [_task("t-keep")]
    env.scheduler.jobs = [SimpleNamespace(id="t-keep")]

    added = await sched.reconcile_scheduled_tasks()

    assert added == 0
    assert env.registered == []
    assert env.unregistered == []
    assert env.db.calls == ["list_all_enabled_scheduled_tasks"]


@pytest.mark.asyncio
async def test_task_disabled_after_batch_read_is_not_registered(env):
    """停用竞态：批量读时任务还启用，复核阶段发现已被停用 → 不注册。"""
    env.db.enabled = [_task("t-race")]
    env.db.latest = {"t-race": _task("t-race", enabled=0)}

    added = await sched.reconcile_scheduled_tasks()

    assert added == 0
    assert env.registered == []
    assert env.db.run_time_updates == []
    # 复核阶段确实按任务 ID 再读了一次库
    assert "get_scheduled_task:t-race" in env.db.calls


@pytest.mark.asyncio
async def test_task_deleted_after_batch_read_is_not_registered(env):
    """删除竞态：批量读之后任务被删除 → 复核读不到任务，不注册。"""
    env.db.enabled = [_task("t-gone")]
    env.db.latest = {"t-gone": None}

    added = await sched.reconcile_scheduled_tasks()

    assert added == 0
    assert env.registered == []


@pytest.mark.asyncio
async def test_stopping_task_is_not_registered(env):
    """停止中（有在途运行正在被取消）的任务不注册回去。"""
    env.db.enabled = [_task("t-stop")]
    sched.stopping_tasks.add("t-stop")
    running = asyncio.create_task(asyncio.sleep(3600))
    sched.running_tasks["t-stop"] = running
    try:
        added = await sched.reconcile_scheduled_tasks()
    finally:
        running.cancel()

    assert added == 0
    assert env.registered == []
    # 停止标记留给在途执行收尾清理
    assert "t-stop" in sched.stopping_tasks
    # 停止中的任务直接跳过，不再读库复核
    assert env.db.calls == ["list_all_enabled_scheduled_tasks"]


@pytest.mark.asyncio
async def test_stale_stopping_marker_is_cleared_when_task_reenabled(env):
    """没有在途运行的陈旧停止标记要清掉，否则暂停后再启用永远不会注册。"""
    env.db.enabled = [_task("t-reenable")]
    sched.stopping_tasks.add("t-reenable")  # 上一次暂停留下的标记，当时没有在途运行

    added = await sched.reconcile_scheduled_tasks()

    assert added == 1
    assert [task.task_id for task in env.registered] == ["t-reenable"]
    assert "t-reenable" not in sched.stopping_tasks


@pytest.mark.asyncio
async def test_stop_during_recheck_read_is_not_registered(env):
    """复核读库期间收到暂停（unregister 已写停止标记）→ 不注册。"""
    env.db.enabled = [_task("t-late")]

    def get_scheduled_task(task_id):
        # 模拟 asyncio.to_thread 读库期间事件循环上执行了 unregister
        sched.stopping_tasks.add(task_id)
        return _task(task_id)

    env.db.get_scheduled_task = get_scheduled_task

    added = await sched.reconcile_scheduled_tasks()

    assert added == 0
    assert env.registered == []


@pytest.mark.asyncio
async def test_scheduler_not_running_never_reads_database(env):
    """调度器未运行时直接返回 0，不读库。"""
    env.scheduler.running = False

    assert await sched.reconcile_scheduled_tasks() == 0
    assert env.db.calls == []


@pytest.mark.asyncio
async def test_uninitialized_scheduler_never_reads_database(env, monkeypatch):
    """调度器未初始化时直接返回 0，不读库。"""
    monkeypatch.setattr(sched, "_scheduler_instance", None)

    assert await sched.reconcile_scheduled_tasks() == 0
    assert env.db.calls == []


@pytest.mark.asyncio
async def test_database_calls_run_off_the_event_loop(env):
    """批量读、按 ID 复核读、写 next_run 都在线程里执行，不占事件循环。"""
    loop_thread = threading.get_ident()
    seen: list[tuple[str, bool]] = []
    db = env.db

    real_list = db.list_all_enabled_scheduled_tasks
    real_get = db.get_scheduled_task
    real_update = db.update_scheduled_task_run_times

    def list_all():
        seen.append(("list_all_enabled_scheduled_tasks", threading.get_ident() != loop_thread))
        return real_list()

    def get(task_id):
        seen.append(("get_scheduled_task", threading.get_ident() != loop_thread))
        return real_get(task_id)

    def update(task_id, last_run_at, next_run):
        seen.append(("update_scheduled_task_run_times", threading.get_ident() != loop_thread))
        return real_update(task_id, last_run_at, next_run)

    db.list_all_enabled_scheduled_tasks = list_all
    db.get_scheduled_task = get
    db.update_scheduled_task_run_times = update
    env.db.enabled = [_task("t-thread")]

    added = await sched.reconcile_scheduled_tasks()

    assert added == 1
    assert {name for name, _off_loop in seen} == {
        "list_all_enabled_scheduled_tasks",
        "get_scheduled_task",
        "update_scheduled_task_run_times",
    }
    assert [name for name, off_loop in seen if not off_loop] == []
