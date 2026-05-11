"""Session management routes"""

import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from ..db import Database, get_database
from ..models.db import SessionModel
from ..models.api import (
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
from ..middleware import get_current_username
from ..config import Config

logger = logging.getLogger(__name__)


def generate_workspace_name(session_id: str) -> str:
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

    logger.info(
        f"创建会话 | ID: {session_id} | 标题: {title} | 工作区: {workspace_name}"
    )

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
async def get_session(
    session_id: str,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
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


@router.put("/{session_id}/title", summary="更新会话标题")
async def update_title(
    session_id: str,
    request: UpdateTitleRequest,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    title = request.title or "未命名会话"
    db.update_session_title(session_id, title)

    logger.info(f"更新会话标题 | ID: {session_id} | 新标题: {title}")

    return {"status": "updated", "session_id": session_id, "title": title}


@router.delete(
    "/{session_id}", summary="删除会话", response_model=DeleteSessionResponse
)
async def delete_session(
    session_id: str,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    session_files = db.get_session_files(session_id)
    for f in session_files:
        file_path = Path(f["file_path"])
        if file_path.exists():
            try:
                file_path.unlink()
                logger.info(f"删除会话文件 | 路径: {file_path}")
            except Exception as e:
                logger.warning(f"删除会话文件失败: {file_path} | {e}")

    workspace_dir = Config.get_user_workspace_dir(username) / session.workspace_name
    if workspace_dir.exists():
        try:
            shutil.rmtree(workspace_dir)
            logger.info(f"删除工作区目录 | 路径: {workspace_dir}")
        except Exception as e:
            logger.warning(f"删除工作区目录失败: {e}")

    db.delete_session(session_id)

    logger.info(f"删除会话 | ID: {session_id}")

    return DeleteSessionResponse(session_id=session_id)


@router.get(
    "/{session_id}/history",
    summary="获取聊天历史",
    response_model=GetChatHistoryResponse,
)
async def get_chat_history(
    session_id: str,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
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


@router.post(
    "/{session_id}/messages", summary="添加消息", response_model=AddMessageResponse
)
async def add_message(
    session_id: str,
    request: AddMessageRequest,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
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
        session_id=session_id,
        message_count=len(session.messages) + 1,
    )


@router.post("/{session_id}/upload", summary="上传文件到会话")
async def upload_session_file(
    session_id: str,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
    file: UploadFile = File(...),
):
    session = db.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    Config.sanitize_username(username)
    upload_dir = Config.get_user_workspace_dir(username) / "uploadfiles"
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_filename = os.path.basename(file.filename or "unknown")
    file_id = str(uuid.uuid4())[:8]
    saved_name = f"{file_id}_{safe_filename}"
    file_path = upload_dir / saved_name

    content = await file.read()
    file_path.write_bytes(content)

    ext = os.path.splitext(safe_filename)[1].lstrip(".") or "unknown"

    db.add_session_file(
        session_id=session_id,
        filename=safe_filename,
        file_path=str(file_path),
        file_type=ext,
        size=len(content),
        username=username,
    )

    logger.info(
        f"文件上传成功 | 会话: {session_id} | 文件名: {safe_filename} | 大小: {len(content)} bytes | 用户: {username}"
    )

    return {
        "id": file_id,
        "filename": safe_filename,
        "file_path": str(file_path),
        "file_type": ext,
        "size": len(content),
        "uploaded_at": datetime.now().isoformat(),
    }
