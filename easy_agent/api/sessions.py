"""Session management routes"""

import asyncio
import logging
import os
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile

from easy_agent.agent import release_session_checkpointer
from easy_agent.db.session_repository import SessionRepository
from easy_agent.api.deps import (  # noqa: F401 - 旧模块级名字 re-export
    as_session_repository as _as_session_repository,
    get_session_repository,
)
from easy_agent.models.db import SessionModel
from easy_agent.models.api import (
    SessionPinUpdateResponse,
    SessionTitleUpdateResponse,
    SessionUploadResponse,
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
from easy_agent.middleware import get_current_username
from easy_agent.utils.reasoning_safety import redact_messages_reasoning
from easy_agent.bootstrap.config import Config
from easy_agent.services import clear_session_connectors, get_agent_config, remove_session_agent
from easy_agent.services.session_uploads import (
    UploadTooLargeError,
    append_manifest,
    save_upload_streaming,
    store_parsed_content,
)

logger = logging.getLogger(__name__)


def generate_workspace_name(session_id: str) -> str:
    now = datetime.now()
    return f"{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}_{session_id[:5]}"


def compute_session_usage(messages: list[dict]) -> dict | None:
    """从消息列表中计算会话级别的 token 用量。

    Returns:
        包含 input_tokens/output_tokens/reasoning_tokens/context_tokens/
        elapsed_time/step_count 的字典，如果没有任何用量数据则返回 None。
    """
    total_input = 0
    total_output = 0
    total_reasoning = 0
    context_tokens = 0
    total_elapsed = 0.0
    total_steps = 0

    for msg in messages:
        msg_usage = msg.get("usage") or {}
        total_input += msg_usage.get("input_tokens", 0) or 0
        total_output += msg_usage.get("output_tokens", 0) or 0
        # 思考 token（output 的子集），仅用于拆分展示
        total_reasoning += msg_usage.get("reasoning_tokens", 0) or 0
        total_elapsed += msg_usage.get("elapsed_time", 0) or 0
        total_steps += msg_usage.get("step_count", 0) or 0

    # 从最后一条 assistant 消息获取上下文窗口占用
    # 优先使用 context_tokens（最后一次 API 调用的 input_tokens），
    # 旧消息无此字段时回退到 input_tokens
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            last_usage = msg.get("usage") or {}
            if last_usage.get("context_tokens"):
                context_tokens = last_usage["context_tokens"]
                break
            if last_usage.get("input_tokens"):
                context_tokens = last_usage["input_tokens"]
                break

    if total_input > 0 or total_output > 0 or total_elapsed > 0 or total_steps > 0:
        return {
            "input_tokens": total_input,
            "output_tokens": total_output,
            "reasoning_tokens": total_reasoning,
            "context_tokens": context_tokens,
            "elapsed_time": round(total_elapsed, 2),
            "step_count": total_steps,
        }
    return None


def get_context_length() -> int | None:
    """获取当前配置的 context_length。

    优先复用启动时已初始化的全局配置（get_agent_config），避免每次请求重新读盘
    并依赖 Config.load 的默认搜索路径（生产环境可能读不到按 AGENT_ENV 选择的配置文件，
    导致返回 None，进而使前端切换会话后 token 用量百分比显示为 0）。
    兜底再从磁盘加载配置。
    """
    try:
        cfg = get_agent_config()
        if cfg and cfg.get("config"):
            return cfg["config"].llm.context_length
    except Exception:
        pass
    try:
        cfg = Config.load()
        return cfg.llm.context_length if cfg and cfg.llm else None
    except Exception:
        return None


def _get_max_file_size_mb() -> int | None:
    """读取配置中的单文件上传大小上限（MB）。

    返回 None 表示不做限制（``upload_limits.enabled=false`` 或读取配置失败）；
    读取失败按"不拦"处理，避免配置异常时把上传功能整体打死。
    与 get_context_length 一致：优先复用启动时初始化的全局配置，兜底读盘。
    """
    try:
        cfg = get_agent_config()
        if cfg and cfg.get("config"):
            limits = getattr(cfg["config"], "upload_limits", None)
            if limits is None:
                return None
            return int(limits.max_file_size_mb) if limits.enabled else None
    except Exception as e:
        logger.warning(f"读取上传限额配置失败（本次不做单文件大小限制）: {e}")
        return None
    return None


router = APIRouter(
    prefix="/agent/sessions",
    tags=["Sessions"],
)


# 批次 4：repository provider 统一在 ``api.deps``；旧名字在此 re-export。


@router.post("", summary="创建新会话", response_model=CreateSessionResponse)
async def create_session(
    request: CreateSessionRequest,
    db: Annotated[SessionRepository, Depends(get_session_repository)],
    username: Annotated[str, Depends(get_current_username)],
):
    repo = _as_session_repository(db)

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

    repo.create_session(session_data)
    repo.update_session_workspace_name(session_id, workspace_name)

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
    db: Annotated[SessionRepository, Depends(get_session_repository)],
    username: Annotated[str, Depends(get_current_username)],
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    repo = _as_session_repository(db)

    sessions = repo.list_sessions(limit=limit, offset=offset, username=username)

    return [
        SessionInfo(
            session_id=s.session_id,
            title=s.title,
            created_at=s.created_at,
            updated_at=s.updated_at,
            message_count=len(s.messages),
            pinned=s.pinned,
        )
        for s in sessions
    ]


@router.get("/count", summary="获取会话总数", response_model=SessionCountResponse)
async def count_sessions(
    db: Annotated[SessionRepository, Depends(get_session_repository)],
    username: Annotated[str, Depends(get_current_username)],
):
    repo = _as_session_repository(db)

    total = repo.count_sessions(username=username)
    return SessionCountResponse(total_sessions=total)


@router.get("/{session_id}", summary="获取会话详情", response_model=SessionDetail)
async def get_session(
    session_id: str,
    db: Annotated[SessionRepository, Depends(get_session_repository)],
    username: Annotated[str, Depends(get_current_username)],
):
    repo = _as_session_repository(db)

    session = repo.get_owned_session(session_id, username)

    usage = compute_session_usage(session.messages or [])
    context_length = get_context_length()

    return SessionDetail(
        session_id=session.session_id,
        title=session.title,
        created_at=session.created_at,
        updated_at=session.updated_at,
        messages=redact_messages_reasoning(session.messages),
        todos=session.todos,
        usage=usage,
        context_length=context_length,
    )


@router.put("/{session_id}/title", summary="更新会话标题", response_model=SessionTitleUpdateResponse)
async def update_title(
    session_id: str,
    request: UpdateTitleRequest,
    db: Annotated[SessionRepository, Depends(get_session_repository)],
    username: Annotated[str, Depends(get_current_username)],
):
    repo = _as_session_repository(db)

    session = repo.get_owned_session(session_id, username)

    title = request.title or "未命名会话"
    repo.update_session_title(session_id, title)

    logger.info(f"更新会话标题 | ID: {session_id} | 新标题: {title}")

    return {"status": "updated", "session_id": session_id, "title": title}


@router.put("/{session_id}/pin", summary="切换会话置顶", response_model=SessionPinUpdateResponse)
async def toggle_pin(
    session_id: str,
    db: Annotated[SessionRepository, Depends(get_session_repository)],
    username: Annotated[str, Depends(get_current_username)],
):
    repo = _as_session_repository(db)

    session = repo.get_owned_session(session_id, username)

    new_val = repo.toggle_session_pin(session_id)
    action = "置顶" if new_val else "取消置顶"
    logger.info(f"会话{action} | ID: {session_id}")

    return {"status": "updated", "session_id": session_id, "pinned": new_val}


@router.delete(
    "/{session_id}", summary="删除会话", response_model=DeleteSessionResponse
)
async def delete_session(
    session_id: str,
    db: Annotated[SessionRepository, Depends(get_session_repository)],
    username: Annotated[str, Depends(get_current_username)],
):
    repo = _as_session_repository(db)

    session = repo.get_owned_session(session_id, username)

    session_files = repo.get_session_files(session_id)
    for f in session_files:
        file_path = Path(f["file_path"])
        if file_path.exists():
            try:
                file_path.unlink()
                logger.info(f"删除会话文件 | 路径: {file_path}")
            except Exception as e:
                logger.warning(f"删除会话文件失败: {file_path} | {e}")

    # 先移除缓存 Agent 实例，避免后台记忆更新线程在删除后用 mkdir 重建工作区目录
    try:
        remove_session_agent(session_id)
    except Exception as e:
        logger.warning(f"移除缓存 Agent 失败: {e}")

    # 会话级连接器开关记录随会话一起清掉（进程内缓存，不清理会残留到进程结束）
    try:
        clear_session_connectors(session_id)
    except Exception as e:
        logger.warning(f"清除会话连接器开关失败: {e}")

    # 释放该会话的 HITL checkpointer（未调用时由 agent.py 的 FIFO 上限兜底）
    try:
        release_session_checkpointer(session_id)
    except Exception as e:
        logger.warning(f"释放会话 checkpointer 失败: {e}")

    user_workspace_dir = Config.get_user_workspace_dir(username)
    # 候选工作区目录名：workspace_name（首轮/重命名后）与 session_id（旧会话回退）
    # agent.py 在 workspace_name 为空时会用 session_id 作目录名，故两者都尝试。
    candidate_names = []
    if session.workspace_name:
        candidate_names.append(session.workspace_name)
    candidate_names.append(session_id)
    deleted_any = False
    for name in candidate_names:
        if not name:
            continue
        ws_dir = user_workspace_dir / "session" / name
        # 安全护栏：绝不删除用户级工作区根目录（name 为空或仅指向 user 目录时跳过）
        if ws_dir.resolve() == user_workspace_dir.resolve():
            logger.warning(f"跳过删除：候选目录等于用户工作区根目录 | name={name}")
            continue
        if ws_dir.exists():
            try:
                shutil.rmtree(ws_dir)
                logger.info(f"删除工作区目录 | 路径: {ws_dir} | name={name}")
                deleted_any = True
            except Exception as e:
                logger.warning(f"删除工作区目录失败: {ws_dir} | {e}")
        else:
            logger.info(f"工作区目录不存在，跳过 | 路径: {ws_dir} | name={name}")
    if not deleted_any:
        logger.warning(
            f"未删除任何工作区目录 | session_id={session_id} | "
            f"workspace_name={session.workspace_name} | user_dir={user_workspace_dir}"
        )

    repo.delete_session(session_id)

    logger.info(f"删除会话 | ID: {session_id}")

    return DeleteSessionResponse(session_id=session_id)


@router.get(
    "/{session_id}/history",
    summary="获取聊天历史",
    response_model=GetChatHistoryResponse,
)
async def get_chat_history(
    session_id: str,
    db: Annotated[SessionRepository, Depends(get_session_repository)],
    username: Annotated[str, Depends(get_current_username)],
):
    repo = _as_session_repository(db)

    session = repo.get_owned_session(session_id, username)

    usage = compute_session_usage(session.messages or [])
    context_length = get_context_length()

    return GetChatHistoryResponse(
        session_id=session.session_id,
        title=session.title,
        messages=redact_messages_reasoning(session.messages),
        created_at=session.created_at,
        updated_at=session.updated_at,
        usage=usage,
        context_length=context_length,
    )


@router.post(
    "/{session_id}/messages", summary="添加消息", response_model=AddMessageResponse
)
async def add_message(
    session_id: str,
    request: AddMessageRequest,
    db: Annotated[SessionRepository, Depends(get_session_repository)],
    username: Annotated[str, Depends(get_current_username)],
):
    repo = _as_session_repository(db)

    session = repo.get_owned_session(session_id, username)

    message = {
        "role": request.role,
        "content": request.content,
        "timestamp": datetime.now().isoformat(),
    }
    repo.add_message(session_id, message)

    return AddMessageResponse(
        session_id=session_id,
        message_count=len(session.messages) + 1,
    )


@router.post("/{session_id}/upload", summary="上传文件到会话", response_model=SessionUploadResponse)
async def upload_session_file(
    session_id: str,
    db: Annotated[SessionRepository, Depends(get_session_repository)],
    username: Annotated[str, Depends(get_current_username)],
    file: UploadFile = File(...),
):
    repo = _as_session_repository(db)

    session = repo.get_owned_session(session_id, username)

    Config.sanitize_username(username)
    upload_dir = Config.get_user_workspace_dir(username) / "uploadfiles"
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_filename = os.path.basename(file.filename or "unknown")
    file_id = str(uuid.uuid4())[:8]
    saved_name = f"{file_id}_{safe_filename}"
    file_path = upload_dir / saved_name

    # 流式落盘 + 单文件上限（权威校验）：前端已按 upload_limits 拦截，这里防止
    # 直接调接口绕过；分块读取避免把整个请求体读进内存。
    max_file_size_mb = _get_max_file_size_mb()
    max_bytes = (
        max_file_size_mb * 1024 * 1024 if max_file_size_mb else None
    )
    try:
        size = await save_upload_streaming(file, file_path, max_bytes=max_bytes)
    except UploadTooLargeError as exc:
        actual_mb = round(exc.size / 1024 / 1024, 2)
        limit_mb = exc.limit // (1024 * 1024)
        logger.warning(
            f"文件上传被拒（超过单文件上限）| 文件名: {safe_filename} | "
            f"大小: {actual_mb}MB | 上限: {limit_mb}MB | 用户: {username}"
        )
        raise HTTPException(
            status_code=413,
            detail={
                "code": "UPLOAD_FILE_TOO_LARGE",
                "message": f"单个文件不能超过 {limit_mb}MB，当前 {actual_mb}MB",
                "limit_mb": limit_mb,
                "actual_mb": actual_mb,
            },
        ) from exc

    ext = os.path.splitext(safe_filename)[1].lstrip(".") or "unknown"

    repo.add_session_file(
        session_id=session_id,
        filename=safe_filename,
        file_path=str(file_path),
        file_type=ext,
        size=size,
        username=username,
    )

    logger.info(
        f"文件上传成功 | 会话: {session_id} | 文件名: {safe_filename} | 大小: {size} bytes | 用户: {username}"
    )

    # 解析落盘 + 上传清单（记忆）：解析正文写入会话工作区 uploads/{文件名}.txt，
    # 文件清单写入 uploads/index.md —— 后者由 agent.py 作为记忆文件注入 system
    # prompt，因此后续每一轮（不必再上传）模型都知道本会话传过哪些文件、正文在哪，
    # 并按需用 grep/read_file 检索。file_context.enabled=false 时整体跳过（回退旧行为）。
    parsed_entry = None
    try:
        parsed_entry = await asyncio.to_thread(
            store_parsed_content,
            session_id=session_id,
            username=username,
            file_path=str(file_path),
            filename=safe_filename,
            size=size,
            file_type=ext,
            db=repo.database,
            source_name=saved_name,
        )
        if parsed_entry:
            await asyncio.to_thread(
                append_manifest, session_id, username, [parsed_entry], repo.database
            )
    except Exception as e:
        # 解析/清单失败不能影响上传本身：文件已落盘、记录已入库，用户仍可继续对话
        logger.warning(f"上传文件解析落盘失败（不影响上传）| {safe_filename} | {e}")

    # 上传后驱逐本会话缓存 Agent：记忆文件只在创建 Agent 时加载，不驱逐的话
    # 本轮新上传的清单要等到 Agent 因其他原因重建时才生效（下一轮就看不到新文件）。
    if parsed_entry:
        try:
            remove_session_agent(session_id)
        except Exception as e:
            logger.warning(f"上传后驱逐缓存 Agent 失败: {e}")

    return {
        "id": file_id,
        "filename": safe_filename,
        "file_path": str(file_path),
        "file_type": ext,
        "size": size,
        "uploaded_at": datetime.now().isoformat(),
        "parsed": bool(parsed_entry),
        "parsed_path": parsed_entry.get("stored_path") if parsed_entry else None,
        "parse_status": parsed_entry.get("status") if parsed_entry else None,
    }
