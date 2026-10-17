"""scheduled_tasks 域 repository 对象（C1 剩余域）。

覆盖 ``api/scheduled_tasks.py`` 实际使用的任务 CRUD 与运行记录查询，
全部委托 ``db/repositories/scheduled_tasks.py`` 既有函数，不写 SQL。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...models.db import ScheduledTaskModel, ScheduledTaskRunModel
from . import scheduled_tasks as scheduled_tasks_repo

if TYPE_CHECKING:
    from ..database import Database


class ScheduledTaskRepository:
    """定时任务与执行记录的薄委托层。"""

    def __init__(self, db: "Database"):
        self._db = db

    @property
    def database(self) -> "Database":
        """底层 ``Database``，仅供 scheduler / token 认证服务使用。"""
        return self._db

    def get_scheduled_task(self, task_id: str) -> ScheduledTaskModel | None:
        return scheduled_tasks_repo.get_scheduled_task(self._db, task_id)

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
