"""Chat routes - streaming and non-streaming"""

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..db import Database, get_database
from ..models.db import SessionModel
from ..models.api import ChatRequest
from ..middleware import get_current_username
from ..services import (
    chat_stream_generator,
    get_or_create_agent_for_session,
    remove_session_agent,
    register_stream_task,
    unregister_stream_task,
    cancel_stream_task,
)
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

    if not request.message:
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    session_id = request.session_id
    message_id = request.message_id or str(uuid.uuid4())

    sid = session_id[-5:] if session_id else "new"

    logger.info(
        f"[{sid}] 聊天请求 | message: {request.message[:50]}{'...' if len(request.message) > 50 else ''} | deep_think: {request.enable_deep_think}"
    )

    def generate_session_title(message, files):
        if message and message.strip():
            title = message.strip()
            return title[:15] + "..." if len(title) > 15 else title
        elif files and len(files) > 0:
            filename = files[0].get("filename", "文件")
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
        else:
            workspace_name = session.workspace_name or ""
            if len(session.messages) == 0:
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
                    file_contents.append(
                        f"[文件: {file_info.get('filename', '')}]\n{content}"
                    )
        if file_contents:
            parsed_content = "\n\n".join(file_contents) + "\n\n" + request.message

    # RAG 知识库检索：当用户启用知识库按钮，或上传文件时自动触发
    # 将检索结果与用户原始输入整合后发送给 Agent
    should_rag = request.use_knowledge_base or (request.files and len(request.files) > 0)
    if should_rag:
        try:
            from ..services.rag_service import search_knowledge_base, format_rag_context

            rag_query = request.message or (request.files[0].get("filename", "") if request.files else "")
            logger.info(
                f"[{sid}] [RAG] 触发检索 | use_knowledge_base={request.use_knowledge_base} | "
                f"files={len(request.files or [])} | query={rag_query[:50]}"
            )
            rag_result = search_knowledge_base(
                query=rag_query,
                username=username,
                session_id=session_id,
                files=[f.get("file_path") for f in (request.files or []) if f.get("file_path")],
                n_results=5,
                http_request=http_request,
            )
            logger.info(
                f"[{sid}] [RAG] 检索完成 | success={rag_result.get('success')} | "
                f"count={rag_result.get('count', 0)} | source={rag_result.get('source')}"
            )
            if rag_result.get("success") and rag_result.get("results"):
                parsed_content = format_rag_context(rag_result, parsed_content)
                logger.info(f"[{sid}] [RAG] 已将检索结果整合至消息内容")
        except Exception as e:
            logger.error(f"[{sid}] [RAG] 检索异常: {e}", exc_info=True)

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

    agent = await get_or_create_agent_for_session(session_id, username, workspace_name)

    async def event_generator():
        # 注册当前请求任务，使 /cancel 端点能通过 task.cancel() 中断 astream
        cur_task = asyncio.current_task()
        logger.info(
            f"[{session_id[-5:]}] 📡 流式响应启动 | 已注册 task={cur_task.get_name() if cur_task else '?'}"
        )
        register_stream_task(session_id, cur_task)
        try:
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
        except asyncio.CancelledError:
            logger.info(
                f"[{session_id[-5:]}] 🛑 event_generator 收到 CancelledError | "
                f"astream 已被中断，停止向客户端推送"
            )
            raise
        except Exception as e:
            logger.error(
                f"[{session_id[-5:]}] ❌ event_generator 异常: {type(e).__name__}: {e}"
            )
            raise
        finally:
            unregister_stream_task(session_id)
            logger.info(f"[{session_id[-5:]}] 🏁 流式响应结束 | task 已注销")

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
    description="取消当前正在进行的流式聊天请求，中断后端 astream 执行。",
)
async def cancel_chat(
    session_id: str,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    sid = session_id[-5:] if session_id else "unknown"
    logger.info(
        f"[{sid}] 🛑 收到终止请求 | 用户: {username} | "
        f"完整 session_id: {session_id} | 时间: {datetime.now().isoformat()}"
    )

    # 1. 取消正在运行的流式任务（向 astream 注入 CancelledError，
    #    streaming.py 的 except 分支会保存已生成的部分回复）
    cancelled = await cancel_stream_task(session_id)
    if cancelled:
        logger.info(f"[{sid}] 🛑 流式任务已中断")
    else:
        logger.info(f"[{sid}] 🛑 无正在运行的流式任务（可能已结束或前端 abort）")

    # 2. 清除 Agent 缓存，使下次请求重新创建 Agent
    remove_session_agent(session_id)

    logger.info(f"[{sid}] 🛑 终止处理完成")
    return {"status": "cancelled", "session_id": session_id}
