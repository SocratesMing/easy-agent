"""expert 域 repository 对象（C1 剩余域）。

把 ``api/expert.py`` 实际使用的 ``ExpertStore``、用户归属、会话与消息方法
收拢为可注入对象。``ExpertStore`` 在方法内 lazy import / 实例化，避免
``db.database -> db.repositories.__init__ -> expert_store -> db.database``
的导入期环。

本模块不写 SQL；所有方法委托 ``db/expert_store.py`` 或
``db/repositories/sessions.py`` / ``users.py`` 既有函数。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from easy_agent.models.db import SessionModel
from easy_agent.utils.deployment import instance_owner
from easy_agent.db.repositories import files as files_repo
from easy_agent.db.repositories import session_runtime as session_runtime_repo
from easy_agent.db.repositories import sessions as sessions_repo
from easy_agent.db.repositories import users as users_repo

if TYPE_CHECKING:
    from easy_agent.models.db import UserModel
    from easy_agent.db.database import Database


class ExpertRepository:
    """专家 CRUD、归属校验与专家会话持久化的薄委托层。"""

    def __init__(self, db: "Database"):
        self._db = db

    @property
    def database(self) -> "Database":
        """底层 ``Database``，仅供流式引擎等未迁移服务使用。"""
        return self._db

    def _store(self):
        """延迟构造 ``ExpertStore``（避免 db 层导入期环）。"""
        from easy_agent.db.expert_store import ExpertStore

        return ExpertStore(self._db)

    # ── 用户 ──────────────────────────────────────────────────────────

    def get_user_by_username(self, username: str) -> Optional["UserModel"]:
        return users_repo.get_user_by_username(self._db, username)

    # ── 专家记录 ──────────────────────────────────────────────────────

    def create(self, fields: dict) -> None:
        self._store().create(fields)

    def update_fields(self, expert_agent_id: str, fields: dict) -> None:
        self._store().update_fields(expert_agent_id, fields)

    def get(self, expert_agent_id: str) -> Optional[dict]:
        return self._store().get(expert_agent_id)

    def get_owned(self, expert_agent_id: str, user_id: str) -> Optional[dict]:
        return self._store().get_owned(expert_agent_id, user_id)

    def find_by_name_cn(
        self, user_id: str, name_cn: str, exclude_agent_id: str = ""
    ) -> Optional[dict]:
        return self._store().find_by_name_cn(
            user_id, name_cn, exclude_agent_id=exclude_agent_id
        )

    def list_by_user(
        self,
        user_id: str,
        keyword: str = "",
        status: str = "",
        include_disabled: bool = False,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[int, list[dict]]:
        return self._store().list_by_user(
            user_id,
            keyword=keyword,
            status=status,
            include_disabled=include_disabled,
            page=page,
            page_size=page_size,
        )

    def list_by_session(self, user_id: str, session_id: str) -> list[dict]:
        return self._store().list_by_session(user_id, session_id)

    def delete(self, expert_agent_id: str, user_id: str, hard: bool = False) -> bool:
        return self._store().delete(expert_agent_id, user_id, hard=hard)

    # ── 会话 / 消息（专家执行链路） ───────────────────────────────────

    def create_session(self, session_data: SessionModel) -> SessionModel:
        return sessions_repo.create_session(self._db, session_data)

    def update_session_workspace_name(
        self, session_id: str, workspace_name: str
    ) -> None:
        sessions_repo.update_session_workspace_name(
            self._db, session_id, workspace_name
        )

    def get_session(self, session_id: str) -> Optional[SessionModel]:
        return sessions_repo.get_session(self._db, session_id)

    def get_session_files(self, session_id: str) -> list[dict]:
        """会话上传文件行（``session_uploads`` 解析/重解析的数据端口）。"""
        return files_repo.get_session_files(self._db, session_id)

    def update_session_meta(
        self,
        session_id: str,
        title: str,
        updated_at: str,
        username: str | None = None,
    ) -> None:
        sessions_repo.update_session_meta(
            self._db, session_id, title, updated_at, username
        )

    def add_message(self, session_id: str, message: dict) -> None:
        sessions_repo.add_message(self._db, session_id, message)

    def update_last_assistant_message(self, session_id: str, message: dict) -> None:
        sessions_repo.update_last_assistant_message(self._db, session_id, message)

    def update_last_assistant_message_row(
        self, session_id: str, message: dict
    ) -> None:
        sessions_repo.update_last_assistant_message_row(
            self._db, session_id, message
        )

    # ── 流锁续约（供 _detached_event_stream 心跳） ────────────────────

    def renew_stream(self, session_id: str) -> bool:
        return session_runtime_repo.renew_stream_lease(self._db, session_id, instance_owner())


__all__ = ["ExpertRepository"]
