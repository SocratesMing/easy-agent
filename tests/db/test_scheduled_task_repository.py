"""C1 剩余域：``ScheduledTaskRepository`` 委托与用户范围过滤。"""

from easy_agent.db.database import Database
from easy_agent.db.repositories.scheduled_task_repository import (
    ScheduledTaskRepository,
)
from easy_agent.models.db import ScheduledTaskModel, ScheduledTaskRunModel


def _make_db(tmp_path) -> Database:
    db = Database(
        {"type": "sqlite", "sqlite": {"path": str(tmp_path / "tasks.db")}}
    )
    db.init_tables()
    return db


def _task(task_id: str, username: str = "alice") -> ScheduledTaskModel:
    return ScheduledTaskModel(
        task_id=task_id,
        username=username,
        name=f"任务-{task_id}",
        schedule_cron="0 0 * * *",
        task_prompt="ping",
        enabled=1,
    )


def test_repository_delegates_task_crud_and_run_records(tmp_path):
    db = _make_db(tmp_path)
    repo = ScheduledTaskRepository(db)

    assert repo.database is db
    db.create_scheduled_task(_task("task-1"))
    db.create_scheduled_task(_task("task-2", username="bob"))

    assert repo.get_scheduled_task("task-1").name == "任务-task-1"
    assert repo.get_scheduled_task("missing") is None
    assert [t.task_id for t in repo.list_scheduled_tasks("alice")] == ["task-1"]
    assert repo.list_scheduled_tasks("bob")[0].task_id == "task-2"

    repo.update_scheduled_task_status("task-1", False)
    assert repo.get_scheduled_task("task-1").enabled == 0
    repo.update_scheduled_task_run_times("task-1", "2026-10-18T00:00:00", "2026-10-19T00:00:00")
    refreshed = repo.get_scheduled_task("task-1")
    assert refreshed.last_run_at == "2026-10-18T00:00:00"
    assert refreshed.next_run_at == "2026-10-19T00:00:00"

    db.add_scheduled_task_run(
        ScheduledTaskRunModel(
            run_id="run-1",
            task_id="task-1",
            session_id="",
            status="success",
            started_at="2026-10-18T00:00:00",
            finished_at="2026-10-18T00:00:01",
        )
    )
    runs = repo.list_scheduled_task_runs("task-1")
    assert [r.run_id for r in runs] == ["run-1"]

    assert repo.delete_scheduled_task("task-1") is True
    assert repo.get_scheduled_task("task-1") is None
    assert repo.delete_scheduled_task("task-1") is False


def test_repository_list_is_scoped_to_requested_username(tmp_path):
    db = _make_db(tmp_path)
    repo = ScheduledTaskRepository(db)
    db.create_scheduled_task(_task("alice-task", username="alice"))
    db.create_scheduled_task(_task("bob-task", username="bob"))

    alice_tasks = repo.list_scheduled_tasks("alice")
    bob_tasks = repo.list_scheduled_tasks("bob")
    assert [t.task_id for t in alice_tasks] == ["alice-task"]
    assert [t.task_id for t in bob_tasks] == ["bob-task"]
    assert repo.list_scheduled_tasks("nobody") == []
