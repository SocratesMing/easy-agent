"""scheduled_tasks 域 repository 对象（C1 剩余域）。

覆盖 ``api/scheduled_tasks.py`` 实际使用的任务 CRUD、运行记录查询与工作目录名
解析；全部委托 ``db/repositories/scheduled_tasks.py`` / ``sessions.py`` 既有函数，
不写 SQL。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from easy_agent.models.db import ScheduledTaskModel, ScheduledTaskRunModel
from easy_agent.db.repositories import scheduled_tasks as scheduled_tasks_repo
from easy_agent.db.repositories import sessions as sessions_repo

if TYPE_CHECKING:
    from easy_agent.db.database import Database


class ScheduledTaskRepository:
    """定时任务与执行记录的薄委托层。"""

    def __init__(self, db: "Database"):
        self._db = db

    def get_scheduled_task(self, task_id: str) -> ScheduledTaskModel | None:
        return scheduled_tasks_repo.get_scheduled_task(self._db, task_id)

    def get_workspace_name(self, task: ScheduledTaskModel) -> str:
        """解析任务工作目录名（复用来源会话目录，否则用任务独立目录）。

        与 ``services.scheduler.get_task_workspace_name`` 口径逐条一致：任务自带
        ``workspace_name`` 优先；其次复用来源会话的 ``workspace_name``；都没有时
        使用 ``scheduled_{task_id}``。两条实现的等价性由
        ``tests/db/test_scheduled_task_repository.py`` 的 parity 用例守住。

        API 层经本方法解析目录，不再把 ``Database`` 传给 scheduler 模块。
        """
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
        return scheduled_tasks_repo.list_scheduled_tasks(
            self._db, username, enabled_only=enabled_only
        )

    def delete_scheduled_task(self, task_id: str) -> bool:
        return scheduled_tasks_repo.delete_scheduled_task(self._db, task_id)

    def update_scheduled_task_status(self, task_id: str, enabled: bool) -> None:
        scheduled_tasks_repo.update_scheduled_task_status(self._db, task_id, enabled)

    def update_scheduled_task_run_times(
        self, task_id: str, last_run_at: str, next_run_at: str
    ) -> None:
        scheduled_tasks_repo.update_scheduled_task_run_times(
            self._db, task_id, last_run_at, next_run_at
        )

    def list_scheduled_task_runs(
        self, task_id: str, limit: int = 50
    ) -> list[ScheduledTaskRunModel]:
        return scheduled_tasks_repo.list_scheduled_task_runs(
            self._db, task_id, limit
        )


__all__ = ["ScheduledTaskRepository"]
