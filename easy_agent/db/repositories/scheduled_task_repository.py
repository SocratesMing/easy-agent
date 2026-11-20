"""定时任务与执行记录 repository（scheduled_tasks + scheduled_task_runs）。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from easy_agent.db.repositories import sessions as sessions_repo
from easy_agent.models.db import ScheduledTaskModel, ScheduledTaskRunModel

if TYPE_CHECKING:
    from easy_agent.db.database import Database


class ScheduledTaskRepository:
    """定时任务与执行记录的数据访问对象。"""

    def __init__(self, db: "Database"):
        self._db = db

    def create_scheduled_task(
        self, task: ScheduledTaskModel
    ) -> ScheduledTaskModel:
        with self._db.get_connection() as conn:
            cursor = conn.cursor()
            self._db._execute(
                cursor,
                """INSERT INTO scheduled_tasks
                   (task_id, username, session_id, workspace_name, name, description, schedule_cron,
                    task_prompt, enabled, created_at, updated_at, last_run_at, next_run_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task.task_id,
                    task.username,
                    task.session_id,
                    task.workspace_name,
                    task.name,
                    task.description,
                    task.schedule_cron,
                    task.task_prompt,
                    task.enabled,
                    task.created_at,
                    task.updated_at,
                    task.last_run_at,
                    task.next_run_at,
                ),
            )
        return task

    def get_scheduled_task(self, task_id: str) -> ScheduledTaskModel | None:
        with self._db.get_connection() as conn:
            cursor = conn.cursor()
            self._db._execute(
                cursor, "SELECT * FROM scheduled_tasks WHERE task_id=?", (task_id,)
            )
            row = cursor.fetchone()
        if not row:
            return None
        return ScheduledTaskModel(**dict(row))

    def get_workspace_name(self, task: ScheduledTaskModel) -> str:
        """解析任务工作目录名（优先任务自带，其次来源会话目录，否则 scheduled_{task_id}）。"""
        if getattr(task, "workspace_name", ""):
            return task.workspace_name
        if task.session_id:
            session = sessions_repo.get_session(self._db, task.session_id)
            if session and getattr(session, "workspace_name", ""):
                return session.workspace_name
        return f"scheduled_{task.task_id}"

    def list_scheduled_tasks(
        self, username: str, enabled_only: bool = False
    ) -> list[ScheduledTaskModel]:
        sql = "SELECT * FROM scheduled_tasks WHERE username=?"
        params: list = [username]
        if enabled_only:
            sql += " AND enabled=1"
        sql += " ORDER BY created_at DESC"
        with self._db.get_connection() as conn:
            cursor = conn.cursor()
            self._db._execute(cursor, sql, params)
            rows = cursor.fetchall()
        return [ScheduledTaskModel(**dict(r)) for r in rows]

    def list_all_enabled_scheduled_tasks(self) -> list[ScheduledTaskModel]:
        """全部用户已启用的任务（启动重载 / 周期对账用）。"""
        with self._db.get_connection() as conn:
            cursor = conn.cursor()
            self._db._execute(cursor, "SELECT * FROM scheduled_tasks WHERE enabled=1")
            rows = cursor.fetchall()
        return [ScheduledTaskModel(**dict(r)) for r in rows]

    def delete_scheduled_task(self, task_id: str) -> bool:
        with self._db.get_connection() as conn:
            cursor = conn.cursor()
            self._db._execute(
                cursor, "DELETE FROM scheduled_task_runs WHERE task_id=?", (task_id,)
            )
            self._db._execute(
                cursor, "DELETE FROM scheduled_tasks WHERE task_id=?", (task_id,)
            )
            return cursor.rowcount > 0

    def update_scheduled_task_status(self, task_id: str, enabled: bool) -> None:
        with self._db.get_connection() as conn:
            cursor = conn.cursor()
            self._db._execute(
                cursor,
                "UPDATE scheduled_tasks SET enabled=?, updated_at=? WHERE task_id=?",
                (1 if enabled else 0, datetime.now().isoformat(), task_id),
            )

    def update_scheduled_task_run_times(
        self, task_id: str, last_run_at: str, next_run_at: str
    ) -> None:
        with self._db.get_connection() as conn:
            cursor = conn.cursor()
            self._db._execute(
                cursor,
                "UPDATE scheduled_tasks SET last_run_at=?, next_run_at=? WHERE task_id=?",
                (last_run_at, next_run_at, task_id),
            )

    def update_scheduled_task_workspace(
        self, task_id: str, workspace_name: str
    ) -> None:
        with self._db.get_connection() as conn:
            cursor = conn.cursor()
            self._db._execute(
                cursor,
                "UPDATE scheduled_tasks SET workspace_name=? WHERE task_id=?",
                (workspace_name, task_id),
            )

    def add_scheduled_task_run(
        self, run: ScheduledTaskRunModel
    ) -> ScheduledTaskRunModel:
        with self._db.get_connection() as conn:
            cursor = conn.cursor()
            self._db._execute(
                cursor,
                """INSERT INTO scheduled_task_runs
                   (run_id, task_id, session_id, status, started_at, finished_at, result_summary, error_message)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run.run_id,
                    run.task_id,
                    run.session_id,
                    run.status,
                    run.started_at,
                    run.finished_at,
                    run.result_summary,
                    run.error_message,
                ),
            )
        return run

    def update_scheduled_task_run(
        self,
        run_id: str,
        status: str,
        finished_at: str,
        result_summary: str = "",
        error_message: str = "",
    ) -> None:
        with self._db.get_connection() as conn:
            cursor = conn.cursor()
            self._db._execute(
                cursor,
                """UPDATE scheduled_task_runs
                   SET status=?, finished_at=?, result_summary=?, error_message=?
                   WHERE run_id=?""",
                (status, finished_at, result_summary, error_message, run_id),
            )

    def list_scheduled_task_runs(
        self, task_id: str, limit: int = 50
    ) -> list[ScheduledTaskRunModel]:
        with self._db.get_connection() as conn:
            cursor = conn.cursor()
            self._db._execute(
                cursor,
                "SELECT * FROM scheduled_task_runs WHERE task_id=? ORDER BY started_at DESC LIMIT ?",
                (task_id, limit),
            )
            rows = cursor.fetchall()
        return [ScheduledTaskRunModel(**dict(r)) for r in rows]


__all__ = ["ScheduledTaskRepository"]
