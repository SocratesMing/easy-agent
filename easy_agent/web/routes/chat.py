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

from ..db import Database, SessionModel, get_database
from ..dependencies import get_current_username
from ..models import ChatRequest
from ..service import chat_stream_generator, get_or_create_agent_for_session, remove_session_agent
from ..utils.file_parser import parse_file_content
from ..utils.session_logger import SessionLogger
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

    user_message = {
        "role": "user",
        "content": request.message,
        "timestamp": datetime.now().isoformat(),
        "files": request.files or [],
    }
    db.add_message(session_id, user_message)
    db.add_message_row(session_id, user_message)

    # 如果有文件，解析文件内容并拼接到消息中
    parsed_content = request.message
    if request.files:
        file_context_parts = []
        for file_info in request.files:
            file_path = file_info.get("file_path") or file_info.get("path", "")
            filename = file_info.get("filename", "")
            if file_path and Path(file_path).exists():
                content_type = file_info.get("type", "")
                extracted = parse_file_content(file_path, content_type)
                if extracted and len(extracted) > 20:  # 有实质内容才拼接
                    file_context_parts.append(
                        f"--- 文件: {filename} ---\n{extracted[:10000]}\n--- 文件结束 ---"
                    )
                else:
                    file_context_parts.append(f"[文件: {filename}，无文本内容可提取]")
            else:
                file_context_parts.append(f"[文件: {filename}，路径不可用: {file_path}]")

        if file_context_parts:
            file_context = "\n\n".join(file_context_parts)
            parsed_content = f"{request.message}\n\n[用户上传了以下文件，文件内容已提取供参考]:\n\n{file_context}"

    logger.info(f"[{sid}] 最终消息长度: {len(parsed_content)} 字符 | 包含文件: {bool(request.files)}")

    # 获取 workspace_name 用于创建 agent
    session = db.get_session(session_id)
    workspace_name = session.workspace_name if session else ""

    agent = await get_or_create_agent_for_session(session_id, username, workspace_name)

    from ..service import get_agent_config
    _cfg = get_agent_config()
    sys_prompt = (_cfg or {}).get("system_prompt", "")
    session_logger = SessionLogger(
        session_id=session_id,
        username=username,
        workspace=str(agent.workspace_dir.absolute()) if agent else "",
        system_prompt=sys_prompt,
    )
    session_logger.log_user_message(request.message, request.files, message_id=message_id)

    return StreamingResponse(
        chat_stream_generator(
            request=request,
            db=db,
            agent=agent,
            session_id=session_id,
            message_id=message_id,
            username=username,
            http_request=http_request,
            parsed_content=parsed_content,
            session_logger=session_logger,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete(
    "/session/{session_id}/agent",
    summary="清除会话Agent缓存",
    description="清除指定会话的Agent实例缓存，下次请求会创建新的Agent。",
)
async def clear_session_agent(session_id: str):
    sid = session_id[-5:] if session_id else "new"
    remove_session_agent(session_id)
    logger.info(f"[{sid}] Agent 缓存已清除")
    return {
        "status": "success",
        "message": f"会话 {session_id} 的 Agent 缓存已清除",
    }
