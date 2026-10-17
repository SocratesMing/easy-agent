"""定时任务 CRUD repository（ARCH-03 Phase 2）。

从 `Database` 抽出的 third 领域 SQL：scheduled_tasks + scheduled_task_runs。
连接/事务仍由传入的 db 对象提供。
"""

from __future__ import annotations

from datetime import datetime

from ...models.db import ScheduledTaskModel, ScheduledTaskRunModel


def create_scheduled_task(db, task: ScheduledTaskModel) -> ScheduledTaskModel:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """INSERT INTO scheduled_tasks
               (task_id, username, session_id, workspace_name, name, description, schedule_cron,
                task_prompt, enabled, created_at, updated_at, last_run_at, next_run_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task.task_id, task.username, task.session_id, task.workspace_name, task.name,
             task.description, task.schedule_cron, task.task_prompt, task.enabled,
             task.created_at, task.updated_at, task.last_run_at, task.next_run_at),
        )
    return task


def get_scheduled_task(db, task_id: str) -> ScheduledTaskModel | None:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, "SELECT * FROM scheduled_tasks WHERE task_id=?", (task_id,))
        row = cursor.fetchone()
    if not row:
        return None
    return ScheduledTaskModel(**dict(row))


def list_scheduled_tasks(db, username: str, enabled_only: bool = False) -> list[ScheduledTaskModel]:
    sql = "SELECT * FROM scheduled_tasks WHERE username=?"
    params: list = [username]
    if enabled_only:
        sql += " AND enabled=1"
    sql += " ORDER BY created_at DESC"
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, sql, params)
        rows = cursor.fetchall()
    return [ScheduledTaskModel(**dict(r)) for r in rows]


def list_all_enabled_scheduled_tasks(db) -> list[ScheduledTaskModel]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, "SELECT * FROM scheduled_tasks WHERE enabled=1")
        rows = cursor.fetchall()
    return [ScheduledTaskModel(**dict(r)) for r in rows]


def delete_scheduled_task(db, task_id: str) -> bool:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(cursor, "DELETE FROM scheduled_task_runs WHERE task_id=?", (task_id,))
        db._execute(cursor, "DELETE FROM scheduled_tasks WHERE task_id=?", (task_id,))
        return cursor.rowcount > 0


def update_scheduled_task_status(db, task_id: str, enabled: bool):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE scheduled_tasks SET enabled=?, updated_at=? WHERE task_id=?",
            (1 if enabled else 0, datetime.now().isoformat(), task_id),
        )


def update_scheduled_task_run_times(db, task_id: str, last_run_at: str, next_run_at: str):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE scheduled_tasks SET last_run_at=?, next_run_at=? WHERE task_id=?",
            (last_run_at, next_run_at, task_id),
        )


def update_scheduled_task_workspace(db, task_id: str, workspace_name: str):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "UPDATE scheduled_tasks SET workspace_name=? WHERE task_id=?",
            (workspace_name, task_id),
        )


def add_scheduled_task_run(db, run: ScheduledTaskRunModel) -> ScheduledTaskRunModel:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """INSERT INTO scheduled_task_runs
               (run_id, task_id, session_id, status, started_at, finished_at, result_summary, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (run.run_id, run.task_id, run.session_id, run.status,
             run.started_at, run.finished_at, run.result_summary, run.error_message),
        )
    return run


def update_scheduled_task_run(
    db,
    run_id: str,
    status: str,
    finished_at: str,
    result_summary: str = "",
    error_message: str = "",
):
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            """UPDATE scheduled_task_runs
               SET status=?, finished_at=?, result_summary=?, error_message=?
               WHERE run_id=?""",
            (status, finished_at, result_summary, error_message, run_id),
        )


def list_scheduled_task_runs(db, task_id: str, limit: int = 50) -> list[ScheduledTaskRunModel]:
    with db.get_connection() as conn:
        cursor = conn.cursor()
        db._execute(
            cursor,
            "SELECT * FROM scheduled_task_runs WHERE task_id=? ORDER BY started_at DESC LIMIT ?",
            (task_id, limit),
        )
        rows = cursor.fetchall()
    return [ScheduledTaskRunModel(**dict(r)) for r in rows]
