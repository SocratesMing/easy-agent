"""Session management routes"""

import json
import logging
import uuid
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..database import Database, SessionModel, get_database
from ..dependencies import get_current_username
from ..models import (
    AddMessageRequest,
    AddMessageResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    DeleteSessionResponse,
    GetChatHistoryResponse,
    SessionCountResponse,
    SessionDetail,
    SessionInfo,
    UpdateTitleRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/sessions",
    tags=["Sessions"],
)


@router.post("", summary="创建新会话", response_model=CreateSessionResponse)
async def create_session(
    request: CreateSessionRequest,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    session_id = str(uuid.uuid4())
    now = datetime.now().isoformat()

    title = request.title or "新会话"

    session_data = SessionModel(
        session_id=session_id,
        title=title,
        messages=[],
        created_at=now,
        updated_at=now,
        username=username or "",
    )

    db.create_session(session_data)

    logger.info(f"创建会话 | ID: {session_id} | 标题: {title}")

    return CreateSessionResponse(
        session_id=session_id,
        title=title,
        created_at=now,
        updated_at=now,
        message_count=0,
    )


@router.get("", summary="获取会话列表", response_model=list[SessionInfo])
async def list_sessions(
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    sessions = db.list_sessions(limit=limit, offset=offset, username=username)

    return [
        SessionInfo(
            session_id=s.session_id,
            title=s.title,
            created_at=s.created_at,
            updated_at=s.updated_at,
            message_count=len(s.messages),
        )
        for s in sessions
    ]


@router.get("/count", summary="获取会话总数", response_model=SessionCountResponse)
async def count_sessions(
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    total = db.count_sessions(username=username)
    return SessionCountResponse(total_sessions=total)


@router.get("/{session_id}", summary="获取会话详情", response_model=SessionDetail)
async def get_session_detail(
    session_id: str,
    db: Annotated[Database, Depends(get_database)],
):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    return SessionDetail(
        session_id=session.session_id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        messages=session.messages,
    )


@router.get("/{session_id}/history", summary="获取聊天历史", response_model=GetChatHistoryResponse)
async def get_chat_history(
    session_id: str,
    db: Annotated[Database, Depends(get_database)],
):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    return GetChatHistoryResponse(
        session_id=session.session_id,
        title=session.title,
        messages=session.messages,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


@router.put("/{session_id}/title", summary="更新会话标题")
async def update_session_title(
    session_id: str,
    request: UpdateTitleRequest,
    db: Annotated[Database, Depends(get_database)],
):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    if request.title:
        db.update_session_title(session_id, request.title)
        return {"status": "success", "title": request.title}

    raise HTTPException(status_code=400, detail="标题不能为空")


@router.delete("/{session_id}", summary="删除会话", response_model=DeleteSessionResponse)
async def delete_session(
    session_id: str,
    db: Annotated[Database, Depends(get_database)],
):
    success = db.delete_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="会话不存在")

    logger.info(f"删除会话 | ID: {session_id}")

    from ..service import remove_session_agent
    remove_session_agent(session_id)

    return DeleteSessionResponse(status="deleted", session_id=session_id)


@router.post("/{session_id}/messages", summary="添加消息", response_model=AddMessageResponse)
async def add_message(
    session_id: str,
    request: AddMessageRequest,
    db: Annotated[Database, Depends(get_database)],
):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    message = {
        "role": request.role,
        "content": request.content,
        "timestamp": datetime.now().isoformat(),
    }
    db.add_message(session_id, message)

    return AddMessageResponse(
        status="success",
        session_id=session_id,
        message_count=len(db.get_messages(session_id)),
    )


@router.get("/{session_id}/tool-calls", summary="获取工具调用记录")
async def get_tool_calls(
    session_id: str,
    db: Annotated[Database, Depends(get_database)],
):
    tool_calls = db.get_tool_calls(session_id)
    return {"tool_calls": tool_calls}
