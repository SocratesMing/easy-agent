"""知识面板流式入口（A1 下沉）。

「知识库问答」右侧面板与主对话共用同一条流式聊天链路；改造前
``knowledges/api.py`` 直接 ``from ..api.chat import chat_stream``，使知识域
反向依赖宿主路由。本模块把该调用下沉到 ``knowledges/chat/``，通过
:mod:`easy_agent.knowledges.chat.host_port` 端口取得宿主实现。

行为约束（与改造前逐字一致）：

- ``ChatRequest`` 字段不变：``message=question``、``session_id=隐藏会话``、
  ``message_id=随机 UUID``、``enable_deep_think=True``；
- 实参顺序不变：``chat_stream(chat_request, db, http_request, username)``；
- 鉴权不变：``username`` 由调用方用 ``get_knowledge_principal`` 从 Bearer
  令牌解析并校验 ``token_version``，这里不做二次身份判断；
- 路径/SSE 不变：返回值仍是宿主 ``api.chat.chat_stream`` 产出的
  ``StreamingResponse``，知识证据事件由宿主生成器原样输出。

设计文档：``docs/design/2026-10-18-a1-knowledge-streaming.md``。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, Request, status

from easy_agent.db import Database
from easy_agent.models.api import ChatRequest
from easy_agent.knowledges.chat.host_port import HostCapabilities, get_host_capabilities


def require_host_capabilities() -> HostCapabilities:
    """取宿主能力；未注册时转 503（fail-closed，带稳定错误码）。"""

    try:
        return get_host_capabilities()
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "KNOWLEDGE_CHAT_UNAVAILABLE",
                "message": str(exc),
            },
        ) from exc


async def stream_panel_answer(
    *,
    session_id: str,
    question: str,
    request: Request,
    db: Database,
    username: str,
) -> Any:
    """把知识面板问题委托给宿主聊天端点（返回 ``StreamingResponse``）。

    隐藏会话的定位/创建/知识范围锁定由调用方在进入本函数前完成；本函数
    只负责构造与主聊天完全相同的请求并透传。
    """

    capabilities = require_host_capabilities()
    return await capabilities.chat_stream(
        ChatRequest(
            message=question,
            session_id=session_id,
            message_id=str(uuid.uuid4()),
            enable_deep_think=True,
        ),
        db,
        request,
        username,
    )


__all__ = ["require_host_capabilities", "stream_panel_answer"]