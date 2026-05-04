"""Session management routes"""

import json
import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import Database, SessionModel, get_database
from ..dependencies import get_current_username
from ...config import Config
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
from ..utils.session_logger import SessionLogger

logger = logging.getLogger(__name__)


def generate_workspace_name(session_id: str) -> str:
    """生成工作区名称: 年月日_时分秒_会话id前5个字符"""
    now = datetime.now()
    return f"{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}_{session_id[:5]}"

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
    workspace_name = generate_workspace_name(session_id)

    title = request.title or "新会话"

    session_data = SessionModel(
        session_id=session_id,
        title=title,
        messages=[],
        created_at=now,
        updated_at=now,
        username=username or "",
        workspace_name=workspace_name,
    )

    db.create_session(session_data)
    db.update_session_workspace_name(session_id, workspace_name)

    logger.info(f"创建会话 | ID: {session_id} | 标题: {title} | 工作区: {workspace_name}")

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
    username: Annotated[str, Depends(get_current_username)],
):
    # Read session BEFORE deletion to get workspace_name
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    workspace_name = getattr(session, 'workspace_name', '') or ''

    success = db.delete_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="会话不存在")

    # Delete workspace directory (try new naming first, fallback to session_id)
    workspace_base = Path("workspace") / Config.sanitize_username(username)

    workspace_dir_to_delete = None
    if workspace_name:
        candidate = workspace_base / workspace_name
        if candidate.exists() and candidate.is_dir():
            workspace_dir_to_delete = candidate

    if workspace_dir_to_delete is None:
        candidate = workspace_base / session_id
        if candidate.exists() and candidate.is_dir():
            workspace_dir_to_delete = candidate

    if workspace_dir_to_delete:
        try:
            shutil.rmtree(workspace_dir_to_delete)
            logger.info(f"删除会话工作区 | ID: {session_id} | 路径: {workspace_dir_to_delete}")
        except Exception as e:
            logger.warning(f"删除会话工作区失败 | ID: {session_id} | 错误: {e}")

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


@router.get("/{session_id}/log", summary="获取会话日志")
async def get_session_log(
    session_id: str,
):
    log_path = SessionLogger.get_session_log_path(session_id)
    if not log_path.exists():
        raise HTTPException(status_code=404, detail="会话日志不存在")
    import json as _json
    return _json.loads(log_path.read_text(encoding="utf-8"))


@router.get("/logs", summary="列出所有会话日志")
async def list_session_logs():
    logs = SessionLogger.get_all_session_logs()
    return {"logs": logs}
