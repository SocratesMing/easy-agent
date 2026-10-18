"""Sessions 域 repository 对象（C1 第一批）。

把 ``Database`` 门面上的 sessions / session_files 领域方法收拢为一个可注入
对象，供 ``api/sessions.py`` 通过 FastAPI ``Depends`` 使用。

边界（刻意保持最小）：
- 本模块**不写 SQL**，所有方法委托给既有
  ``db/repositories/sessions.py`` / ``db/repositories/files.py`` 函数，
  因此 A2 的「消息只写 session_messages 行表」与 legacy ``sessions.messages``
  只读回退语义完全不变；
- API 层只依赖 :class:`SessionRepository`，不再 import ``Database`` 类型；
- 模块导入期不 import FastAPI（owner 校验在方法内 lazy import
  ``utils.session``），避免 db 层对 Web 框架产生导入期依赖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from easy_agent.models.db import SessionModel
from easy_agent.db.repositories import files as files_repo
from easy_agent.db.repositories import sessions as sessions_repo

if TYPE_CHECKING:
    from easy_agent.db.database import Database


class SessionRepository:
    """会话 CRUD、消息与会话上传文件登记的薄委托层。

    构造入参是 ``Database``（A2 之后它只剩连接/迁移与纯委托）。仓库层仍是
    ``db/repositories/*`` 的既有函数，本类只负责把「用哪个连接」和「业务动作」
    绑定成一个可注入对象，便于 API 层测试时替换依赖。
    """

    def __init__(self, db: "Database"):
        self._db = db

    @property
    def database(self) -> "Database":
        """底层 ``Database``，仅供尚未迁移的服务使用（边界例外）。

        目前只有 ``services/session_uploads.py`` 的解析落盘/清单函数仍以
        ``db=`` 参数接收 ``Database``；API 层不应对它调用业务方法，C1 后续
        批次会把该服务也改为依赖 repository。
        """
        return self._db

    # ── 会话 ──────────────────────────────────────────────────────────

    def create_session(self, session_data: SessionModel) -> SessionModel:
        """创建会话元数据（A2：不写 legacy messages JSON 正文）。"""
        return sessions_repo.create_session(self._db, session_data)

    def update_session_workspace_name(
        self, session_id: str, workspace_name: str
    ) -> None:
        sessions_repo.update_session_workspace_name(
            self._db, session_id, workspace_name
        )

    def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        username: str | None = None,
    ) -> list[SessionModel]:
        return sessions_repo.list_sessions(
            self._db, limit=limit, offset=offset, username=username
        )

    def get_session(self, session_id: str) -> SessionModel | None:
        return sessions_repo.get_session(self._db, session_id)

    def get_session_owner(self, session_id: str) -> dict | None:
        """轻量归属查询（username / workspace_name / title，不加载消息）。"""
        return sessions_repo.get_session_owner(self._db, session_id)

    def get_owned_session(
        self, session_id: str, username: str
    ) -> SessionModel:
        """返回归属当前用户的会话；不存在或跨用户统一 404。

        委托 ``utils.session.get_owned_session``，保持与现有 API 完全一致的
        错误语义（404 + "会话不存在"，不泄露会话是否存在）。
        """
        from easy_agent.utils.session import get_owned_session as _get_owned_session

        return _get_owned_session(self._db, session_id, username)

    def get_messages(self, session_id: str) -> list[dict]:
        return sessions_repo.get_messages(self._db, session_id)

    def count_sessions(self, username: str | None = None) -> int:
        return sessions_repo.count_sessions(self._db, username=username)

    def update_session_title(self, session_id: str, title: str) -> None:
        sessions_repo.update_session_title(self._db, session_id, title)

    def toggle_session_pin(self, session_id: str) -> int:
        """切换置顶并返回新的 pinned 值（0/1）。"""
        return sessions_repo.toggle_session_pin(self._db, session_id)

    def delete_session(self, session_id: str) -> bool:
        """删除会话及其 session_messages 行；返回是否删除了会话行。"""
        return sessions_repo.delete_session(self._db, session_id)

    def add_message(self, session_id: str, message: dict) -> None:
        """追加一条消息（只写 session_messages 行表）。"""
        sessions_repo.add_message(self._db, session_id, message)

    # ── 会话上传文件 ──────────────────────────────────────────────────

    def add_session_file(
        self,
        session_id: str,
        filename: str,
        file_path: str,
        file_type: str,
        size: int,
        username: str = "",
    ) -> None:
        files_repo.add_session_file(
            self._db,
            session_id,
            filename,
            file_path,
            file_type,
            size,
            username,
        )

    def get_session_files(self, session_id: str) -> list[dict]:
        return files_repo.get_session_files(self._db, session_id)


__all__ = ["SessionRepository"]
