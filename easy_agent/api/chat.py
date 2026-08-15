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
from ..services.streaming import format_sse
from ..utils import parse_file_content, SessionLogger, get_owned_session
from .sessions import generate_workspace_name

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/chat",
    tags=["Chat"],
)


def generate_session_title(message: str, files: list | None) -> str:
    """根据首条消息或首个上传文件生成会话标题。"""
    if message and message.strip():
        title = message.strip()
        return title[:15] + "..." if len(title) > 15 else title
    elif files and len(files) > 0:
        filename = files[0].get("filename", "文件")
        return filename[:15] + "..." if len(filename) > 15 else filename
    return "未命名会话"


def create_new_session(
    db: Database,
    session_id: str,
    username: str,
    message: str,
    files: list | None,
) -> str:
    """创建新会话记录并返回其 workspace_name。"""
    now = datetime.now().isoformat()
    session_title = generate_session_title(message, files)
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
    return workspace_name


_detached_bg_tasks: "set[asyncio.Task]" = set()

# 会话级流式事件中枢：客户端刷新/重连后可通过 /stream/live 重新订阅，
# 先回放本流已产生的全部事件，再持续接收后续事件（页面刷新不中断流式展示）。
_session_stream_hubs: "dict[str, _StreamHub]" = {}
_STREAM_HUB_TTL_SECONDS = 120.0


class _StreamHub:
    """一个会话当前流式任务的事件广播器。

    后台任务把每个 SSE 分片广播给所有订阅者（原始客户端 + 刷新后重连的客户端），
    并保留完整事件历史供新订阅者回放。任务结束后保留 TTL 秒，让迟到重连的
    客户端仍能拿到 done/error 收尾事件。
    """

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.history: list[str] = []
        self.done = False
        self._subscribers: list[asyncio.Queue] = []
        self._cleanup_handle: asyncio.TimerHandle | None = None

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        for item in self.history:
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                break
        if self.done:
            q.put_nowait(None)
        else:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    def broadcast(self, item: str | None) -> None:
        if item is not None:
            self.history.append(item)
            if len(self.history) > 10000:
                del self.history[: len(self.history) - 10000]
        for q in list(self._subscribers):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                # 慢消费者：直接断开，避免阻塞后台流式任务
                self.unsubscribe(q)

    def close(self) -> None:
        if self.done:
            return
        self.done = True
        self.broadcast(None)
        self._subscribers.clear()
        self._cleanup_handle = asyncio.get_running_loop().call_later(
            _STREAM_HUB_TTL_SECONDS,
            lambda: (
                _session_stream_hubs.pop(self.session_id, None)
                if _session_stream_hubs.get(self.session_id) is self
                else None
            ),
        )


async def _detached_event_stream(gen, session_id: str, sid: str):
    """以「后台任务 + 事件中枢」驱动流式生成器，与客户端断开解耦。

    后台任务推进 astream 并把 SSE 分片入队；客户端断开（登出/超时/关页）时仅停止
    向前端推送，后台任务继续运行至完成（含 DB 持久化），长任务因此不会被中断。
    /cancel 仍生效：后台任务已 register_stream_task 注册，可被 task.cancel() 取消。
    刷新页面后，新客户端可通过 /stream/live 订阅同一中枢继续接收事件。
    """
    hub = _session_stream_hubs.get(session_id)
    if hub is None or hub.done:
        hub = _StreamHub(session_id)
        _session_stream_hubs[session_id] = hub
    queue: asyncio.Queue = hub.subscribe()
    state = {"disconnected": False}

    async def _drive():
        bg = asyncio.current_task()
        try:
            register_stream_task(session_id, bg)
            async for chunk in gen:
                # 广播给所有订阅者（原始客户端队列也是订阅者之一）
                hub.broadcast(chunk)
        except asyncio.CancelledError:
            logger.info(f"[{sid}] 后台流式任务被取消（/cancel）")
            raise
        except Exception as e:
            logger.error(f"[{sid}] 后台流式任务异常: {type(e).__name__}: {e}")
            hub.broadcast(
                format_sse({"type": "error", "content": f"处理失败: {e}"})
            )
        finally:
            unregister_stream_task(session_id, bg)
            hub.close()

    bg_task = asyncio.create_task(_drive())
    _detached_bg_tasks.add(bg_task)
    bg_task.add_done_callback(_detached_bg_tasks.discard)
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
    except asyncio.CancelledError:
        state["disconnected"] = True
        hub.unsubscribe(queue)
        logger.info(f"[{sid}] 客户端断开连接，后台流式任务继续执行（不取消）")


def _stream_hub_for(session_id: str) -> _StreamHub | None:
    hub = _session_stream_hubs.get(session_id)
    return hub if hub is not None and not hub.done else None


@router.get(
    "/stream/status",
    summary="查询会话是否正在流式输出",
    description="刷新页面后用于判断是否需要重新挂载到进行中的流式任务。",
)
async def chat_stream_status(
    session_id: str,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    # 校验会话归属（不存在的会话同样 404，避免泄露会话是否存在）
    get_owned_session(db, session_id, username)
    return {
        "session_id": session_id,
        "active": _stream_hub_for(session_id) is not None,
    }


@router.get(
    "/stream/live",
    summary="挂载到进行中的流式输出",
    description=(
        "回放当前流式任务已产生的事件（thinking/tool_call/content 等），"
        "然后持续接收后续事件直到 done/error。若任务已结束，回放完整历史后立即结束。"
    ),
)
async def chat_stream_live(
    session_id: str,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    # 校验会话归属
    get_owned_session(db, session_id, username)
    hub = _session_stream_hubs.get(session_id)

    async def event_source():
        if hub is None:
            yield format_sse(
                {
                    "type": "done",
                    "session_id": session_id,
                    "already_completed": True,
                }
            )
            return
        q = hub.subscribe()
        try:
            while True:
                item = await q.get()
                if item is None:
                    break
                yield item
        finally:
            hub.unsubscribe(q)

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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

    if session_id is None:
        session_id = str(uuid.uuid4())
        workspace_name = create_new_session(
            db, session_id, username, request.message, request.files
        )
    else:
        session = db.get_session(session_id)
        if not session:
            session_id = str(uuid.uuid4())
            workspace_name = create_new_session(
                db, session_id, username, request.message, request.files
            )
        elif (session.username or "") != (username or ""):
            # 会话属于其他用户 -> 拒绝访问，不泄露会话是否存在
            raise HTTPException(status_code=404, detail="会话不存在")
        else:
            workspace_name = session.workspace_name or ""
            if len(session.messages) == 0:
                session.title = generate_session_title(request.message, request.files)
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

    return StreamingResponse(
        _detached_event_stream(
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
            session_id,
            sid,
        ),
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

    # 获取会话的 workspace_name（校验归属，防止跨用户恢复他人会话）
    session = get_owned_session(db, session_id, username)
    workspace_name = session.workspace_name or ""

    # 复用缓存的 Agent 实例（保留 checkpointer 中的 interrupt 状态）
    agent = await get_or_create_agent_for_session(session_id, username, workspace_name)

    return StreamingResponse(
        _detached_event_stream(
            resume_stream_generator(
                db=db,
                agent=agent,
                session_id=session_id,
                thread_id=thread_id,
                decisions=request.decisions,
                username=username,
                message_id=request.message_id,
            ),
            session_id,
            sid,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
