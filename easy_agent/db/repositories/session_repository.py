"""会话领域 repository（T3 会话/消息域）。

T3 把 ``db/session_repository.py`` 收敛到本模块（旧路径留一行 re-export，见
``easy_agent/db/session_repository.py``）。本类只做会话聚合：会话 CRUD、归属
校验、标题/置顶/工作区名等元数据，以及会话上传文件登记的过渡方法（文件域由
T3b 继续迁移，方法先留在这里避免 API 层回退到底层函数）。

边界（刻意保持最小）：

- 本模块**不写 SQL**：所有方法委托 ``db/repositories/sessions.py`` /
  ``files.py`` 与 ``db/session_ownership.py`` 的既有函数；SQL、事务边界与表结构
  都不在本任务范围内，A2「消息只写 ``session_messages`` 行表」语义不变；
- 方法签名写全（不用 ``*args, **kwargs``），调用方与 IDE/类型检查都能看到参数；
- ``database`` property 只服务于尚未迁移到 repository 的边界
  （``services/session_uploads.py`` 的 ``db=`` 端口已能直接接受 repository，
  这里保留是给流式引擎/知识域等未迁移调用方的过渡出口，新增代码不要用）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from easy_agent.models.db import SessionModel
from easy_agent.db.repositories import agent_runs as agent_runs_repo
from easy_agent.db.repositories import files as files_repo
from easy_agent.db.repositories import sessions as sessions_repo

if TYPE_CHECKING:
    from easy_agent.db.database import Database


class SessionRepository:
    """会话 CRUD、归属校验与会话上传文件登记的薄委托层。

    构造入参是 ``Database``（A2 之后它只剩连接/迁移与其余域的纯委托）。仓库层仍是
    ``db/repositories/*`` 的既有函数，本类只负责把「用哪个连接」和「业务动作」绑定成
    一个可注入对象，便于 API 层测试时替换依赖。
    """

    def __init__(self, db: "Database"):
        self._db = db

    @property
    def database(self) -> "Database":
        """底层 ``Database``，仅供尚未迁移的边界使用（见模块 docstring）。"""
        return self._db

    # ── 会话 ──────────────────────────────────────────────────────────

    def create_session(self, session_data: SessionModel) -> SessionModel:
        """创建会话元数据（A2：不写 legacy messages JSON 正文）。"""
        return sessions_repo.create_session(self._db, session_data)

    def get_session(self, session_id: str) -> SessionModel | None:
        return sessions_repo.get_session(self._db, session_id)

    def get_session_owner(self, session_id: str) -> dict | None:
        """轻量归属查询（username / workspace_name / title，不加载消息）。"""
        return sessions_repo.get_session_owner(self._db, session_id)

    def get_owned_session(self, session_id: str, username: str) -> SessionModel:
        """返回归属当前用户的会话；不存在或跨用户统一 404（领域异常）。"""
        from easy_agent.db.session_ownership import (
            get_owned_session as _get_owned_session,
        )

        return _get_owned_session(self._db, session_id, username)

    def get_owned_session_meta(self, session_id: str, username: str) -> dict:
        """轻量版归属校验：只返回归属/工作区所需字段，不加载消息。"""
        from easy_agent.db.session_ownership import (
            get_owned_session_meta as _get_owned_session_meta,
        )

        return _get_owned_session_meta(self._db, session_id, username)

    def list_sessions(
        self,
        limit: int = 50,
        offset: int = 0,
        username: str | None = None,
    ) -> list[SessionModel]:
        return sessions_repo.list_sessions(
            self._db, limit=limit, offset=offset, username=username
        )

    def count_sessions(self, username: str | None = None) -> int:
        return sessions_repo.count_sessions(self._db, username=username)

    def update_session_meta(
        self,
        session_id: str,
        title: str,
        updated_at: str,
        username: str | None = None,
    ) -> None:
        """只更新会话元信息（标题/时间/用户名），不重写 legacy messages 大字段。"""
        sessions_repo.update_session_meta(
            self._db, session_id, title, updated_at, username
        )

    def update_session(self, session_data: SessionModel) -> None:
        """更新会话元信息（A2：不把 messages 序列化回写 legacy 列）。"""
        sessions_repo.update_session(self._db, session_data)

    def update_session_title(self, session_id: str, title: str) -> None:
        sessions_repo.update_session_title(self._db, session_id, title)

    def update_session_workspace_name(
        self, session_id: str, workspace_name: str
    ) -> None:
        sessions_repo.update_session_workspace_name(
            self._db, session_id, workspace_name
        )

    def update_session_todos(self, session_id: str, todos: list) -> None:
        """更新会话的 Todo list（整表覆盖）。"""
        sessions_repo.update_session_todos(self._db, session_id, todos)

    def delete_session(self, session_id: str) -> bool:
        """删除会话及其 ``session_messages`` 行；返回是否删除了会话行。"""
        return sessions_repo.delete_session(self._db, session_id)

    def toggle_session_pin(self, session_id: str) -> int:
        """切换置顶并返回新的 pinned 值（0/1）。"""
        return sessions_repo.toggle_session_pin(self._db, session_id)

    # ── 消息（读取入口；写入请用 MessageRepository）────────────────────

    def get_messages(self, session_id: str) -> list[dict]:
        """会话 UI 投影消息列表（行表优先 + legacy JSON 只读回退）。"""
        return sessions_repo.get_messages(self._db, session_id)

    def add_message(self, session_id: str, message: dict) -> None:
        """追加一条消息（A2：只写 ``session_messages`` 行表）。"""
        sessions_repo.add_message(self._db, session_id, message)

    def add_message_row(self, session_id: str, message: dict) -> None:
        sessions_repo.add_message_row(self._db, session_id, message)

    def update_last_assistant_message(self, session_id: str, message: dict) -> None:
        sessions_repo.update_last_assistant_message(self._db, session_id, message)

    def annotate_user_turn(self, session_id: str, turn_id: str, fields: dict) -> bool:
        """把 ``fields`` 合并进本轮用户消息行的 ``extra_data``（DA-03）。"""
        return sessions_repo.annotate_user_turn(
            self._db, session_id, turn_id, fields
        )

    def list_run_message_ids(self, session_id: str) -> list[str]:
        """该会话所有 run 的 message_id，用于删除每条消息一个的旧 checkpoint thread。"""
        return agent_runs_repo.list_session_message_ids(self._db, session_id)

    # ── 会话上传文件（过渡：文件域由 T3b 收敛）─────────────────────────

    def add_session_file(
        self,
        session_id: str,
        filename: str,
        file_path: str,
        file_type: str,
        size: int,
        username: str = "",
    ) -> int | None:
        return files_repo.add_session_file(
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