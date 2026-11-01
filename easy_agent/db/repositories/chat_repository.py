"""聊天入口的领域 repository 组合（T3 会话/消息域）。

``api/chat.py`` / ``api/chat_live.py`` 通过 ``api/deps.get_chat_repository`` 注入本
类；内部由五个领域 repository 组成：

- ``sessions``：:class:`SessionRepository`（会话 CRUD / 归属 / 上传文件登记）
- ``messages``：:class:`MessageRepository`（``session_messages`` 行表读写）
- ``runs``：:class:`AgentRunRepository`（run 租约 / 终态）
- ``runtime``：:class:`SessionRuntimeRepository`（跨副本流锁 / 连接器 / 待修改专家）
- ``stream_events``：:class:`StreamEventRepository`（SSE 事件持久化 / 回放）
- ``experts``：:class:`ExpertRepository`（专家记录 / 归属 / 专家会话）

T3 之前这里是一组手写薄委托方法；现在只是组合门面。为兼容既有调用方与测试，
下方同名方法保留（实现全部委托给组合出来的领域 repository）。新代码请直接用
``repo.sessions`` / ``repo.messages`` / ``repo.runs`` 等，不要再通过
``repo.database`` 逃生口向 ``Database`` 取业务方法。

``database`` property 仅用于把底层实例交给知识库鉴权、专家域、流式引擎等尚未
迁移的服务（见设计文档 T3 / T3b / T3c 的范围划分）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from easy_agent.models.db import SessionModel
from easy_agent.utils.deployment import instance_owner
from easy_agent.db.repositories.agent_runs import AgentRunRepository
from easy_agent.db.repositories.expert_repository import ExpertRepository
from easy_agent.db.repositories.message_repository import MessageRepository
from easy_agent.db.repositories.session_repository import SessionRepository
from easy_agent.db.repositories.session_runtime import SessionRuntimeRepository
from easy_agent.db.repositories.stream_events import StreamEventRepository

if TYPE_CHECKING:
    from easy_agent.db.database import Database


class ChatRepository:
    """聊天入口所需的领域 repository 组合（会话 / 消息 / run / 流运行时 / 流事件）。"""

    def __init__(self, db: "Database"):
        self._db = db
        self.sessions = SessionRepository(db)
        self.messages = MessageRepository(db)
        self.runs = AgentRunRepository(db)
        self.runtime = SessionRuntimeRepository(db)
        self.stream_events = StreamEventRepository(db)
        self.experts = ExpertRepository(db)

    @property
    def database(self) -> "Database":
        """底层 ``Database``，仅供知识域/专家域/流式引擎等尚未迁移的边界使用。"""
        return self._db

    # ── 会话（兼容方法；新代码用 ``repo.sessions``）───────────────────

    def create_session(self, session_data: SessionModel) -> SessionModel:
        return self.sessions.create_session(session_data)

    def update_session_workspace_name(
        self, session_id: str, workspace_name: str
    ) -> None:
        self.sessions.update_session_workspace_name(session_id, workspace_name)

    def get_session(self, session_id: str) -> SessionModel | None:
        return self.sessions.get_session(session_id)

    def get_session_files(self, session_id: str) -> list[dict]:
        """会话上传文件行（``session_uploads`` 解析/重解析的数据端口）。"""
        return self.sessions.get_session_files(session_id)

    def get_owned_session_meta(self, session_id: str, username: str) -> dict:
        """返回会话归属元信息；不存在或跨用户统一 404（领域异常）。"""
        return self.sessions.get_owned_session_meta(session_id, username)

    def update_session_meta(
        self,
        session_id: str,
        title: str,
        updated_at: str,
        username: str | None = None,
    ) -> None:
        self.sessions.update_session_meta(session_id, title, updated_at, username)

    # ── 消息（兼容方法；新代码用 ``repo.messages``）───────────────────

    def add_message(self, session_id: str, message: dict) -> None:
        """追加一条消息（A2：只写 ``session_messages`` 行表）。"""
        self.messages.add_message(session_id, message)

    def update_last_assistant_message(self, session_id: str, message: dict) -> None:
        """整条覆盖最后一条 assistant 消息（作废上一轮遗留的待审批时用，DA-03）。"""
        self.messages.update_last_assistant_message(session_id, message)

    # ── 流锁续约（供 _detached_event_stream 心跳） ────────────────────

    def renew_stream(self, session_id: str) -> bool:
        return self.runtime.renew_stream_lease(session_id, instance_owner())


__all__ = ["ChatRepository"]