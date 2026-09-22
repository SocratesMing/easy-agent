"""定时任务在多实例部署下的互斥执行测试。

多 pod 部署时每个实例都会注册同一批 cron，同一个任务到点后会被所有实例触发。
``_execute_task`` 依赖分布式锁保证只有一个实例真正执行；本测试用「同一 SQLite
文件上的两个 DistributedLock 实例」模拟两个 pod。
"""

import asyncio
from types import SimpleNamespace

import pytest

import easy_agent.services.agent_manager as agent_manager
import easy_agent.services.scheduler as sched
from easy_agent.config import DistributedLockConfig
from easy_agent.models.db import ScheduledTaskModel
from easy_agent.utils.distributed_lock import DistributedLock

TASK_ID = "t-1"

LOCK_CONFIG = DistributedLockConfig(
    enabled=True, ttl_seconds=60, retry_interval_seconds=0.05
)


class _StubScheduler:
    """`_execute_task` 收尾时会读 job 的下次执行时间，测试里没有真实调度器。"""

    def get_job(self, task_id):
        return None


class _FakeAgentCore:
    def __init__(self):
        self.calls = []

    async def ainvoke(self, payload, config=None):
        self.calls.append(payload)
        return {"messages": [SimpleNamespace(type="ai", content="任务执行完成")]}


class _FakeAgent:
    def __init__(self):
        self.agent = _FakeAgentCore()


def _create_task(db, task_id: str = TASK_ID) -> ScheduledTaskModel:
    task = ScheduledTaskModel(
        task_id=task_id,
        username="testuser",
        name="演示任务",
        schedule_cron="0 9 * * *",
        task_prompt="写一份日报",
        enabled=1,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )
    db.create_scheduled_task(task)
    return task


def _use_lock(db, owner: str, enabled: bool = True) -> DistributedLock:
    config = LOCK_CONFIG if enabled else DistributedLockConfig(enabled=False)
    return DistributedLock(config=config, db=db, owner=owner)


@pytest.fixture()
def patched(monkeypatch, db):
    """替换 scheduler 的 agent / 数据库 / 审计日志依赖，返回 (fake_agent, events)。"""
    fake_agent = _FakeAgent()
    events: list[dict] = []

    async def _fake_get_agent(session_id, username="default", workspace_name="", **kwargs):
        return fake_agent

    monkeypatch.setattr(agent_manager, "get_or_create_agent_for_session", _fake_get_agent)
    monkeypatch.setattr(agent_manager, "remove_session_agent", lambda session_id: None)
    monkeypatch.setattr(sched, "get_database", lambda: db)
    monkeypatch.setattr(sched, "get_scheduler", lambda: _StubScheduler())
    monkeypatch.setattr(sched, "log_task_event", lambda **kwargs: events.append(kwargs))
    return fake_agent, events


@pytest.mark.asyncio
async def test_execute_task_skipped_when_other_instance_holds_lock(
    db, monkeypatch, patched
):
    fake_agent, events = patched
    task = _create_task(db)

    other_pod = _use_lock(db, owner="pod-other")
    assert other_pod.acquire(sched.task_lock_name(task.task_id)).acquired is True
    monkeypatch.setattr(sched, "get_distributed_lock", lambda: _use_lock(db, "pod-self"))

    await sched._execute_task(task.task_id)

    assert fake_agent.agent.calls == []                      # 未调用 agent
    assert db.list_scheduled_task_runs(task.task_id) == []   # 未产生执行记录
    assert [e["operation"] for e in events] == ["skip_locked"]


@pytest.mark.asyncio
async def test_execute_task_takes_lock_and_releases_after_run(db, monkeypatch, patched):
    fake_agent, _ = patched
    task = _create_task(db)
    monkeypatch.setattr(sched, "get_distributed_lock", lambda: _use_lock(db, "pod-self"))

    await sched._execute_task(task.task_id)

    assert len(fake_agent.agent.calls) == 1
    assert [run.status for run in db.list_scheduled_task_runs(task.task_id)] == [
        "succeeded"
    ]
    # 执行结束后锁已释放，其它实例可以立刻接管这个任务
    assert (
        _use_lock(db, owner="pod-other")
        .acquire(sched.task_lock_name(task.task_id))
        .acquired
        is True
    )


@pytest.mark.asyncio
async def test_disabled_lock_keeps_previous_behaviour(db, monkeypatch, patched):
    """未开启分布式锁时（单实例部署）行为与引入锁之前一致，且不写锁表。"""
    fake_agent, _ = patched
    task = _create_task(db)
    monkeypatch.setattr(
        sched, "get_distributed_lock", lambda: _use_lock(db, "pod-self", enabled=False)
    )

    await sched._execute_task(task.task_id)

    assert len(fake_agent.agent.calls) == 1
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, "SELECT COUNT(*) FROM distributed_locks")
        assert cursor.fetchone()[0] == 0


@pytest.mark.asyncio
async def test_lock_heartbeat_cancels_run_after_lock_lost(db):
    """续约失败（锁过期后被其它实例接管）时，心跳应取消在途执行避免重复写入。"""
    lock = DistributedLock(
        config=DistributedLockConfig(enabled=True, ttl_seconds=3),
        db=db,
        owner="pod-a",
    )
    handle = lock.acquire(sched.task_lock_name(TASK_ID))
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE distributed_locks SET expires_at=? WHERE lock_key=?",
            ("2000-01-01T00:00:00.000000+00:00", handle.key),
        )

    running = asyncio.create_task(asyncio.sleep(30))
    heartbeat = asyncio.create_task(sched._renew_task_lock(handle, TASK_ID, running))
    await asyncio.wait({running}, timeout=10)

    assert running.cancelled() is True
    heartbeat.cancel()


@pytest.mark.asyncio
async def test_execute_task_marked_cancelled_when_lock_lost(db, monkeypatch, patched):
    """锁被抢占（续约失败）时中止在途执行，运行记录写明真实原因。"""
    task = _create_task(db)
    lock = _use_lock(db, owner="pod-self")
    lock_key = lock.scoped_key(sched.task_lock_name(task.task_id))
    # 心跳间隔取 min(配置值, ttl/3)，这里用短 TTL 让续约在 1 秒内发生
    monkeypatch.setattr(
        sched,
        "get_distributed_lock",
        lambda: DistributedLock(
            config=DistributedLockConfig(enabled=True, ttl_seconds=3),
            db=db,
            owner="pod-self",
        ),
    )

    async def _fake_get_agent(session_id, username="default", workspace_name="", **kwargs):
        class _Agent:
            class agent:  # noqa: N801 - 模拟 EasyAgent.agent.ainvoke
                @staticmethod
                async def ainvoke(payload, config=None):
                    # 模拟「长时间执行期间锁过期并被其它实例接管」
                    with db.get_connection() as conn:
                        cursor = conn.cursor()
                        db._execute(
                            cursor,
                            "UPDATE distributed_locks SET expires_at=? WHERE lock_key=?",
                            ("2000-01-01T00:00:00.000000+00:00", lock_key),
                        )
                    await asyncio.sleep(30)

        return _Agent()

    monkeypatch.setattr(
        agent_manager, "get_or_create_agent_for_session", _fake_get_agent
    )

    await asyncio.create_task(sched._execute_task(task.task_id))

    runs = db.list_scheduled_task_runs(task.task_id)
    assert [run.status for run in runs] == ["cancelled"]
    assert "分布式锁" in runs[0].error_message
