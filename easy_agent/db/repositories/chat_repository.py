"""chat 域 repository 对象（C1 剩余域）。

覆盖 ``api/chat.py`` 实际使用的会话创建、归属查询、消息追加与流锁续约。
所有方法委托 ``db/repositories/sessions.py`` 或 ``utils.session`` 既有函数，
不写 SQL。

``database`` property 仅用于把底层实例交给知识库鉴权、流式生成等尚未迁移的
服务（见设计文档 §4）；API 层不对它调用 ``Database`` 业务方法。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ...models.db import SessionModel
from . import files as files_repo
from . import sessions as sessions_repo

if TYPE_CHECKING:
    from ..database import Database


class ChatRepository:
    """聊天入口所需的会话与消息薄委托层。"""

    def __init__(self, db: "Database"):
        self._db = db

    @property
    def database(self) -> "Database":
        """底层 ``Database``，仅供流式引擎与知识域等未迁移服务使用。"""
        return self._db

    # ── 会话 ──────────────────────────────────────────────────────────

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

    def get_owned_session_meta(self, session_id: str, username: str) -> dict:
        """返回会话归属元信息；不存在或跨用户统一 404。"""
        from ...utils.session import get_owned_session_meta

        return get_owned_session_meta(self._db, session_id, username)

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

    # ── 消息 ──────────────────────────────────────────────────────────

    def add_message(self, session_id: str, message: dict) -> None:
        """追加一条消息（A2：只写 ``session_messages`` 行表）。"""
        sessions_repo.add_message(self._db, session_id, message)

    # ── 流锁续约（供 _detached_event_stream 心跳） ────────────────────

    def renew_stream(self, session_id: str) -> bool:
        from ...services.session_runtime import renew_stream

        return renew_stream(self._db, session_id)


__all__ = ["ChatRepository"]
