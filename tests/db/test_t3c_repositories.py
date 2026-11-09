"""T3c 收尾新增的领域 repository：定时任务 / LangGraph store / 限流计数。

这些域此前只经 ``Database`` 转发或直接拼 SQL 访问；T3c 把入口收敛到
``db/repositories/`` 的领域对象，Database 只保留连接/事务/迁移。
"""

from __future__ import annotations

from easy_agent.db.database import Database
from easy_agent.db.repositories.agent_state import AgentStateRepository
from easy_agent.db.repositories.rate_limits import RateLimitRepository
from easy_agent.db.repositories.scheduled_task_repository import (
    ScheduledTaskRepository,
)
from easy_agent.models.db import ScheduledTaskModel, ScheduledTaskRunModel


def _make_db(tmp_path, name: str = "t3c.db") -> Database:
    db = Database({"type": "sqlite", "sqlite": {"path": str(tmp_path / name)}})
    db.init_tables()
    return db


def _task(task_id: str = "task-1", username: str = "alice") -> ScheduledTaskModel:
    return ScheduledTaskModel(
        task_id=task_id,
        username=username,
        name="每日报告",
        schedule_cron="0 9 * * *",
        task_prompt="写日报",
        enabled=1,
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
        workspace_name="ws-task",
    )


def test_scheduled_task_repository_delegates_full_lifecycle(tmp_path):
    db = _make_db(tmp_path)
    repo = ScheduledTaskRepository(db)

    repo.create_scheduled_task(_task())
    loaded = repo.get_scheduled_task("task-1")
    assert loaded is not None and loaded.name == "每日报告"
    assert [t.task_id for t in repo.list_all_enabled_scheduled_tasks()] == ["task-1"]
    # 工作目录解析与 scheduler 口径一致（任务自带 workspace_name 优先）
    assert repo.get_workspace_name(loaded) == "ws-task"

    repo.update_scheduled_task_run_times("task-1", "2026-01-01 09:00:00", "2026-01-02 09:00:00")
    repo.update_scheduled_task_workspace("task-1", "ws-renamed")
    assert repo.get_scheduled_task("task-1").workspace_name == "ws-renamed"

    run = ScheduledTaskRunModel(
        run_id="run-1",
        task_id="task-1",
        session_id="sess-1",
        status="running",
        started_at="2026-01-01 09:00:00",
    )
    repo.add_scheduled_task_run(run)
    assert [r.status for r in repo.list_scheduled_task_runs("task-1")] == ["running"]

    repo.update_scheduled_task_run(
        "run-1", status="succeeded", finished_at="2026-01-01 09:00:10"
    )
    [finished] = repo.list_scheduled_task_runs("task-1")
    assert finished.status == "succeeded"
    assert finished.finished_at == "2026-01-01 09:00:10"

    repo.delete_scheduled_task("task-1")
    assert repo.get_scheduled_task("task-1") is None


def test_agent_state_repository_roundtrip(tmp_path):
    db = _make_db(tmp_path, "state.db")
    repo = AgentStateRepository(db)

    repo.store_put("user/alice", "k1", '{"v": 1}')
    assert repo.store_get("user/alice", "k1")["value"] == '{"v": 1}'
    assert repo.store_get("user/alice", "missing") is None

    repo.store_put("user/bob", "k2", '{"v": 2}')
    assert repo.store_list_namespaces() == ["user/alice", "user/bob"]
    rows = repo.store_search("user/", limit=10)
    assert {row["item_key"] for row in rows} == {"k1", "k2"}

    repo.store_delete("user/alice", "k1")
    assert repo.store_get("user/alice", "k1") is None


def test_rate_limit_repository_fixed_window(tmp_path):
    db = _make_db(tmp_path, "rate.db")
    repo = RateLimitRepository(db)

    assert repo.try_allow("login:ip", 2, 60.0, now=1_000_000.0) is True
    assert repo.try_allow("login:ip", 2, 60.0, now=1_000_001.0) is True
    assert repo.try_allow("login:ip", 2, 60.0, now=1_000_002.0) is False
    # 下一个固定窗口重新计数
    assert repo.try_allow("login:ip", 2, 60.0, now=1_000_060.0) is True
    # 不同 key 互不影响
    assert repo.try_allow("login:other", 1, 60.0, now=1_000_060.0) is True
    assert repo.try_allow("login:other", 1, 60.0, now=1_000_060.0) is False