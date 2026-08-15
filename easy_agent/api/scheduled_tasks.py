"""Scheduled tasks API routes - CRUD + execution records."""

import logging
from datetime import datetime
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ..db import Database, get_database
from ..middleware import get_current_username
from ..models.api import (
    ScheduledTaskInfo,
    ScheduledTaskRunInfo,
    DeleteScheduledTaskResponse,
)
from ..services import (
    register_scheduled_task,
    unregister_scheduled_task,
    get_scheduler,
)
from ..services.scheduler import get_task_workspace_name
from ..api.files import build_workspace_tree, resolve_workspace_file, serve_workspace_file
from ..config import Config
from ..utils.task_logger import log_task_event

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/scheduled-tasks",
    tags=["Scheduled Tasks"],
)


def _task_to_info(task) -> dict:
    return {
        "task_id": task.task_id,
        "username": task.username,
        "session_id": task.session_id,
        "workspace_name": getattr(task, "workspace_name", ""),
        "name": task.name,
        "description": task.description,
        "schedule_cron": task.schedule_cron,
        "task_prompt": task.task_prompt,
        "enabled": bool(task.enabled),
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "last_run_at": task.last_run_at,
        "next_run_at": task.next_run_at,
    }


def _run_to_info(run) -> dict:
    return {
        "run_id": run.run_id,
        "task_id": run.task_id,
        "session_id": run.session_id,
        "status": run.status,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "result_summary": run.result_summary,
        "error_message": run.error_message,
    }


@router.get("", summary="列出当前用户的定时任务")
async def list_scheduled_tasks(
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    tasks = db.list_scheduled_tasks(username)
    return [_task_to_info(t) for t in tasks]


@router.get("/{task_id}/runs", summary="列出定时任务执行记录")
async def list_task_runs(
    task_id: str,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    task = db.get_scheduled_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.username != username:
        raise HTTPException(status_code=403, detail="无权访问此任务")
    runs = db.list_scheduled_task_runs(task_id)
    return [_run_to_info(r) for r in runs]


@router.delete("/{task_id}", summary="删除定时任务", response_model=DeleteScheduledTaskResponse)
async def delete_scheduled_task(
    task_id: str,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    task = db.get_scheduled_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.username != username:
        raise HTTPException(status_code=403, detail="无权删除此任务")
    try:
        unregister_scheduled_task(task_id, task=task)
    except Exception as e:
        logger.warning(f"移除 scheduler job 失败: {e}")
    db.delete_scheduled_task(task_id)
    logger.info(f"[{username}] 删除定时任务 | task_id={task_id}")
    log_task_event(
        username=username,
        task_id=task_id,
        operation="delete",
        detail=f"任务已删除: name={task.name}",
    )
    return DeleteScheduledTaskResponse(task_id=task_id)


@router.patch("/{task_id}/toggle", summary="启用/禁用定时任务")
async def toggle_scheduled_task(
    task_id: str,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    task = db.get_scheduled_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.username != username:
        raise HTTPException(status_code=403, detail="无权操作此任务")
    new_enabled = not bool(task.enabled)
    db.update_scheduled_task_status(task_id, new_enabled)
    if new_enabled:
        try:
            next_run = register_scheduled_task(task)
            if next_run:
                db.update_scheduled_task_run_times(task_id, task.last_run_at, next_run)
        except Exception as e:
            logger.warning(f"注册 scheduler job 失败: {e}")
    else:
        try:
            unregister_scheduled_task(task_id, task=task)
        except Exception as e:
            logger.warning(f"移除 scheduler job 失败: {e}")
    logger.info(f"[{username}] 切换定时任务状态 | task_id={task_id} | enabled={new_enabled}")
    log_task_event(
        username=username,
        task_id=task_id,
        operation="toggle",
        detail=f"任务状态切换: name={task.name}, enabled={new_enabled}",
        enabled=new_enabled,
    )
    return {"task_id": task_id, "enabled": new_enabled}


@router.post("/{task_id}/run", summary="手动触发定时任务")
async def run_scheduled_task_now(
    task_id: str,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    task = db.get_scheduled_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.username != username:
        raise HTTPException(status_code=403, detail="无权操作此任务")
    try:
        from ..services.scheduler import _execute_task
        scheduler = get_scheduler()
        scheduler.add_job(
            _execute_task,
            trigger="date",
            args=[task_id],
            id=f"manual-{task_id}-{datetime.now().strftime('%H%M%S')}",
            replace_existing=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"触发失败: {e}")
    log_task_event(
        username=username,
        task_id=task_id,
        operation="manual_run",
        detail=f"手动触发执行: name={task.name}",
    )
    return {"task_id": task_id, "status": "triggered"}


def _resolve_task_workspace_dir(db, task_id: str, username: str):
    """校验任务归属并返回其工作目录的绝对路径。"""
    task = db.get_scheduled_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.username != username:
        raise HTTPException(status_code=403, detail="无权访问此任务")
    workspace_name = get_task_workspace_name(db, task)
    workspace_dir = Config.get_user_workspace_dir(task.username)
    if workspace_name:
        workspace_dir = workspace_dir / "session" / workspace_name
    return task, workspace_dir


@router.get("/{task_id}/workspace", summary="获取定时任务工作目录文件树")
async def get_task_workspace(
    task_id: str,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
    path: str = "",
):
    """返回定时任务工作目录的文件树，便于查看任务产生的文件。

    工作目录定位规则见 ``get_task_workspace_name``：若任务由会话生成则复用该会话目录，
    否则使用任务独立目录 ``scheduled_{task_id}``。
    """
    _, workspace_dir = _resolve_task_workspace_dir(db, task_id, username)
    items = build_workspace_tree(workspace_dir, path)
    return {"path": path, "workspace_dir": str(workspace_dir), "items": items}


@router.get("/{task_id}/workspace/file", summary="预览/下载定时任务工作目录文件")
async def get_task_workspace_file(
    request: Request,
    task_id: str,
    file_path: str = "",
    download: bool = False,
    target: Optional[str] = Query(
        default=None,
        description="转换为指定格式后返回，如 pdf（用 LibreOffice 转换，用于 PPT/XLS/DOC 预览）",
    ),
    token: Optional[str] = Query(
        default=None,
        description="认证token（iframe/img 等无法设置 Header 时使用）",
    ),
    db: Annotated[Database, Depends(get_database)] = None,
):
    # 认证：优先 Authorization Header，其次查询参数 token（iframe/img 预览场景），
    # 均走单点登录 token_version 校验
    username = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        from ..middleware.auth import verify_token_sso

        username = verify_token_sso(auth_header[7:], db)
    if not username and token:
        from ..middleware.auth import verify_token_sso

        username = verify_token_sso(token, db)
    if not username:
        raise HTTPException(status_code=401, detail="登录已过期或未登录，请重新登录")

    _, workspace_dir = _resolve_task_workspace_dir(db, task_id, username)
    if not file_path:
        raise HTTPException(status_code=400, detail="缺少 file_path 参数")

    full_path = resolve_workspace_file(workspace_dir, file_path)
    if not full_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    # 目标格式转换（PPT/XLS/DOC → PDF），与普通会话文件预览一致
    if target and target.lower() == "pdf":
        try:
            from ..services.document_convert import convert_to_pdf

            pdf_path = convert_to_pdf(full_path, target="pdf")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"文档转换失败 | {file_path} | {e}")
            raise HTTPException(status_code=500, detail=f"文档转换失败: {e}")
        return FileResponse(
            path=str(pdf_path),
            filename=full_path.stem + ".pdf",
            media_type="application/pdf",
            content_disposition_type="inline",
        )

    return serve_workspace_file(workspace_dir, file_path, download=download)
