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
from ..models.api import ChatRequest, ResumeRequest
from ..middleware import get_current_username
from ..services import (
    chat_stream_generator,
    resume_stream_generator,
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
        f"[{sid}] 聊天请求 | message: {request.message[:50]}{'...' if len(request.message) > 50 else ''} | "
        f"deep_think: {request.enable_deep_think} | model: {request.model or '(active)'}"
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
    # 判断是否为新建会话（首次消息）：新建会话的上传文件内容注入系统提示词，
    # 非首次会话则拼接到用户消息（保持原有行为）。
    _existing = db.get_session(session_id)
    is_new_session = (not _existing) or (len(_existing.messages) == 0)

    # 依次解析上传文件内容（支持 docx/excel/pdf/txt/md 等）
    file_context_parts = []
    if request.files:
        for file_info in request.files:
            file_path = file_info.get("file_path", "")
            if not file_path:
                continue
            filename = file_info.get("filename", "")
            content = parse_file_content(file_path)
            if content:
                file_context_parts.append((filename, content))
                logger.info(
                    f"[{session_id[-5:]}] 📎 文件已解析 | {filename} | "
                    f"内容长度: {len(content)} 字符"
                )
            else:
                logger.warning(f"[{session_id[-5:]}] 文件解析为空: {filename}")

    system_prompt_extra = ""
    if file_context_parts:
        if is_new_session:
            sections = [
                f"### 文件: {fn}\n{c}" for fn, c in file_context_parts
            ]
            system_prompt_extra = (
                "## 用户上传的文件内容（已为你解析，请基于这些内容回应用户）\n\n"
                + "\n\n".join(sections)
            )
        else:
            file_contents = [
                f"[文件: {fn}]\n{c}" for fn, c in file_context_parts
            ]
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

    agent = await get_or_create_agent_for_session(
        session_id, username, workspace_name, model_name=request.model,
        system_prompt_extra=system_prompt_extra,
    )

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


@router.post(
    "/resume",
    summary="恢复 HITL 审批",
    description="用户审批文件删除操作后，恢复 Agent 执行流。",
)
async def chat_resume(
    request: ResumeRequest,
    db: Annotated[Database, Depends(get_database)],
    http_request: Request,
    username: Annotated[str, Depends(get_current_username)],
):
    session_id = request.session_id
    thread_id = request.thread_id
    sid = session_id[-5:] if session_id else "resume"

    logger.info(
        f"[{sid}] 🔔 HITL 恢复请求 | thread_id={thread_id} | "
        f"decisions={request.decisions}"
    )

    # 获取会话的 workspace_name
    workspace_name = ""
    try:
        session = db.get_session(session_id)
        if session:
            workspace_name = session.workspace_name or ""
    except Exception:
        pass

    # 复用缓存的 Agent 实例（保留 checkpointer 中的 interrupt 状态）
    agent = await get_or_create_agent_for_session(session_id, username, workspace_name)

    async def event_generator():
        cur_task = asyncio.current_task()
        register_stream_task(session_id, cur_task)
        try:
            async for chunk in resume_stream_generator(
                db=db,
                agent=agent,
                session_id=session_id,
                thread_id=thread_id,
                decisions=request.decisions,
                username=username,
            ):
                yield chunk
        except asyncio.CancelledError:
            logger.info(f"[{sid}] 🔔 HITL 恢复被取消")
            raise
        except Exception as e:
            logger.error(f"[{sid}] 🔔 HITL 恢复异常: {type(e).__name__}: {e}")
            raise
        finally:
            unregister_stream_task(session_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
