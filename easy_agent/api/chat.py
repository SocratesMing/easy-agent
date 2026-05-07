"""Chat routes - streaming and non-streaming"""

import json
import logging
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..db import Database, get_database
from ..models.db import SessionModel
from ..models.api import ChatRequest
from ..middleware import get_current_username
from ..services import chat_stream_generator, get_or_create_agent_for_session, remove_session_agent
from ..utils import parse_file_content, SessionLogger
from .sessions import generate_workspace_name

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/chat",
    tags=["Chat"],
)


@router.post(
    "/stream",
    summary="流式聊天",
    description="发送聊天消息并接收实时流式响应，包括思考过程和最终内容。",
)
async def chat_stream(
    request: ChatRequest,
    db: Annotated[Database, Depends(get_database)],
    http_request: Request,
    username: Annotated[str, Depends(get_current_username)],
):
    start_time = time.time()

    if not request.message:
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    session_id = request.session_id
    message_id = request.message_id or str(uuid.uuid4())

    sid = session_id[-5:] if session_id else "new"

    logger.info(f"[{sid}] 聊天请求 | message: {request.message[:50]}{'...' if len(request.message) > 50 else ''} | deep_think: {request.enable_deep_think}")

    def generate_session_title(message, files):
        if message and message.strip():
            title = message.strip()
            return title[:15] + "..." if len(title) > 15 else title
        elif files and len(files) > 0:
            filename = files[0].get('filename', '文件')
            return filename[:15] + "..." if len(filename) > 15 else filename
        else:
            return "未命名会话"

    if session_id is None:
        session_id = str(uuid.uuid4())
        now = datetime.now().isoformat()
        session_title = generate_session_title(request.message, request.files)
        workspace_name = generate_workspace_name(session_id)
        session_data = SessionModel(
            session_id=session_id,
            title=session_title,
            messages=[],
            created_at=now,
            updated_at=now,
            username=username,
            workspace_name=workspace_name,
        )
        db.create_session(session_data)
        db.update_session_workspace_name(session_id, workspace_name)
    else:
        session = db.get_session(session_id)
        if not session:
            session_id = str(uuid.uuid4())
            now = datetime.now().isoformat()
            session_title = generate_session_title(request.message, request.files)
            workspace_name = generate_workspace_name(session_id)
            session_data = SessionModel(
                session_id=session_id,
                title=session_title,
                messages=[],
                created_at=now,
                updated_at=now,
                username=username,
                workspace_name=workspace_name,
            )
            db.create_session(session_data)
            db.update_session_workspace_name(session_id, workspace_name)
        elif len(session.messages) == 0:
            session_title = generate_session_title(request.message, request.files)
            session.title = session_title
            session.updated_at = datetime.now().isoformat()
            db.update_session(session)

    parsed_content = request.message
    if request.files:
        file_contents = []
        for file_info in request.files:
            file_path = file_info.get("file_path", "")
            if file_path:
                content = parse_file_content(file_path)
                if content:
                    file_contents.append(f"[文件: {file_info.get('filename', '')}]\n{content}")
        if file_contents:
            parsed_content = "\n\n".join(file_contents) + "\n\n" + request.message

    user_message = {
        "role": "user",
        "content": request.message,
        "timestamp": datetime.now().isoformat(),
    }
    if request.files:
        user_message["files"] = request.files
    db.add_message(session_id, user_message)
    db.add_message_row(session_id, user_message)

    session_logger = SessionLogger(
        session_id=session_id,
        username=username,
        workspace="",
        system_prompt="",
    )
    session_logger.log_user_message(
        message=request.message,
        files=request.files,
        message_id=message_id,
    )

    agent = await get_or_create_agent_for_session(session_id, username)

    async def event_generator():
        async for chunk in chat_stream_generator(
            request=request,
            db=db,
            agent=agent,
            session_id=session_id,
            message_id=message_id,
            username=username,
            http_request=http_request,
            parsed_content=parsed_content,
            session_logger=session_logger,
        ):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/cancel",
    summary="取消当前聊天",
    description="取消当前正在进行的流式聊天请求。",
)
async def cancel_chat(
    session_id: str,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    sid = session_id[-5:] if session_id else "unknown"
    logger.info(f"[{sid}] 取消聊天请求 | 用户: {username}")

    remove_session_agent(session_id)

    return {"status": "cancelled", "session_id": session_id}
