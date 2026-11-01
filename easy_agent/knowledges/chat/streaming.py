"""知识模块流式层：主聊天的知识上下文适配。

``chat_stream_generator``：主聊天（带工具的宿主 agent 流）的知识适配——
注入知识上下文前缀、透传宿主流、结束后持久化证据元数据。
"""

from __future__ import annotations

from easy_agent.db.database import Database
from easy_agent.db.repositories.message_repository import MessageRepository
from easy_agent.db.repositories.session_repository import SessionRepository
from easy_agent.services.sse import is_terminal_event
from easy_agent.services.sse_emitter import SSEEmitter, sse_stream
from easy_agent.knowledges.chat.host_port import get_host_capabilities
from easy_agent.utils.blocking import run_blocking


def _session_port(db) -> SessionRepository:
    """会话域数据端口：``SessionRepository``、``Database`` 连接或测试替身（T3c）。"""
    if isinstance(db, SessionRepository):
        return db
    if isinstance(db, Database):
        return SessionRepository(db)
    return db


def _message_port(db) -> MessageRepository:
    """消息域数据端口：``MessageRepository``、``Database`` 连接或测试替身（T3c）。"""
    if isinstance(db, MessageRepository):
        return db
    if isinstance(db, Database):
        return MessageRepository(db)
    return db


def _persist_assistant_metadata(db, session_id: str, assistant_metadata) -> None:
    """把证据元数据合并进最后一条 assistant 消息并双写持久化。

    只做同步查库 / 写库；调用方经 ``run_blocking`` 放进线程，避免占住事件循环。
    """

    if not assistant_metadata:
        return
    session = _session_port(db).get_session(session_id)
    if session and session.messages and session.messages[-1].get("role") == "assistant":
        message = {**session.messages[-1], **assistant_metadata}
        messages = _message_port(db)
        messages.update_last_assistant_message(session_id, message)
        messages.update_last_assistant_message_row(session_id, message)


# ---------------------------------------------------------------------------
# 主聊天：知识上下文前缀注入 + 证据元数据持久化
# ---------------------------------------------------------------------------
async def chat_stream_generator(*, context_prefix=None, initial_events=None,
                                assistant_metadata=None, **kwargs):
    """主聊天流的知识适配：注入上下文前缀、转发宿主流并持久化证据元数据。

    首个宿主事件后插入 initial_events（知识证据事件）；流结束
    （done/error/approval_required）或生成器被关闭时，把 assistant_metadata
    合并进最后一条 assistant 消息落库（写库放进线程）。
    """

    content = kwargs.get("parsed_content") or kwargs["request"].message
    if context_prefix:
        kwargs["parsed_content"] = f"{context_prefix}\n\n## 用户当前问题\n{content}"
    stream = get_host_capabilities().host_chat_stream_generator(**kwargs)
    emitter = SSEEmitter(kwargs.get("session_id"))

    async def persist_metadata() -> None:
        """把证据元数据落库；写库经 run_blocking 放进线程。"""

        await run_blocking(
            _persist_assistant_metadata,
            kwargs["db"],
            kwargs["session_id"],
            assistant_metadata,
        )

    try:
        first = True
        async for event in stream:
            if assistant_metadata and is_terminal_event(event):
                await persist_metadata()
            yield event
            if first:
                first = False
                async for frame in sse_stream(initial_events or [], emitter=emitter):
                    yield frame
    finally:
        await stream.aclose()
        await persist_metadata()
