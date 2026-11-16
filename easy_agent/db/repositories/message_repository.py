"""消息领域 repository（T3 会话/消息域）。

把 ``db/repositories/sessions.py`` 里消息相关的 SQL 函数收拢成一个可注入对象：
消息追加 / 覆盖最后一条 assistant / UI 投影读取 / 轮次标注。本模块**不写 SQL**，
所有方法委托既有函数，A2「消息只写 ``session_messages`` 行表」语义保持不变。

调用方（API / 服务）只依赖本类，不再向 ``Database`` 门面取消息方法；``Database``
在 T3 之后只剩连接、事务与迁移，会话域的业务入口收敛到
``SessionRepository`` / ``MessageRepository`` / ``AgentRunRepository`` /
``SessionRuntimeRepository`` / ``StreamEventRepository``。

方法签名写全（不用 ``*args, **kwargs``），IDE 与类型检查都能看到参数。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from easy_agent.db.repositories import sessions as sessions_repo

if TYPE_CHECKING:
    from easy_agent.db.database import Database


class MessageRepository:
    """会话消息（``session_messages`` 行表）读写。

    构造入参是 ``Database``（连接/事务提供方）。消息正文的序列化、sanitize 与
    legacy ``sessions.messages`` 只读回退都留在 ``db/repositories/sessions.py``，
    本类只负责把「用哪个连接」和「消息动作」绑定成可注入对象。
    """

    def __init__(self, db: "Database"):
        self._db = db

    def add_message(self, session_id: str, message: dict) -> None:
        """追加一条消息（A2：只写 ``session_messages`` 行表）。"""
        sessions_repo.add_message(self._db, session_id, message)

    def add_message_row(self, session_id: str, message: dict) -> None:
        """只写行表；与 :meth:`add_message` 同义，保留原名字供迁移期调用方使用。"""
        sessions_repo.add_message_row(self._db, session_id, message)

    def update_last_assistant_message(self, session_id: str, message: dict) -> None:
        """整条覆盖最后一条 assistant 消息（末条不是 assistant 时追加一行）。"""
        sessions_repo.update_last_assistant_message(self._db, session_id, message)

    def update_last_assistant_message_row(
        self, session_id: str, message: dict
    ) -> None:
        """行表版本的「覆盖最后一条 assistant 消息」。"""
        sessions_repo.update_last_assistant_message_row(self._db, session_id, message)

    def annotate_user_turn(self, session_id: str, turn_id: str, fields: dict) -> bool:
        """把 ``fields`` 合并进本轮用户消息行的 ``extra_data``（DA-03，按 turn_id 定位）。"""
        return sessions_repo.annotate_user_turn(
            self._db, session_id, turn_id, fields
        )

    def get_messages(self, session_id: str) -> list[dict]:
        """UI 投影用的完整消息列表（行表优先；legacy JSON 只读回退）。"""
        return sessions_repo.get_messages(self._db, session_id)

    def get_messages_from_rows(self, session_id: str) -> list[dict]:
        """只读 ``session_messages`` 行表（不触发 legacy JSON 回退）。"""
        return sessions_repo.get_messages_from_rows(self._db, session_id)


__all__ = ["MessageRepository"]