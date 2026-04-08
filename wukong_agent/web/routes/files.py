"""File management routes"""

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from ..database import Database, get_database
from ..dependencies import get_current_username
from ..models import FileListResponse, FileInfo
from ...config import Config

logger = logging.getLogger(__name__)

BASE_UPLOAD_DIR = Path("./data/uploads")
BASE_WORKSPACE_DIR = Path("./workspace")

router = APIRouter(
    prefix="/api/files",
    tags=["Files"],
)


def get_user_dirs(username: str):
    """获取用户隔离的上传目录和workspace目录"""
    safe_name = Config.sanitize_username(username)
    upload_dir = BASE_UPLOAD_DIR / safe_name
    workspace_dir = BASE_WORKSPACE_DIR / safe_name
    upload_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir, workspace_dir


@router.get("/session/{session_id}", summary="获取会话生成的文件列表")
async def get_session_generated_files(
    session_id: str,
    db: Annotated[Database, Depends(get_database)],
):
    """获取指定会话生成的文件列表"""
    generated_files = db.get_generated_files(session_id)
    
    file_list = []
    for f in generated_files:
        file_list.append({
            "id": f["id"],
            "filename": f["filename"],
            "file_path": f["file_path"],
            "file_type": f["file_type"],
            "size": f["size"],
            "created_at": f["created_at"],
        })
    
    return file_list


@router.get("/list", summary="获取文件列表", response_model=FileListResponse)
async def list_files(
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
    session_id: Optional[str] = Query(default=None),
    file_type: Optional[str] = Query(default=None),
):
    upload_dir, workspace_dir = get_user_dirs(username)

    if session_id:
        files = db.get_session_files(session_id)
        file_list = []
        for f in files:
            fi = FileInfo(
                filename=f["filename"],
                file_path=f["file_path"],
                file_type=f["file_type"],
                size=f["size"],
                uploaded_at=f["uploaded_at"],
            )
            if not file_type or fi.file_type == file_type:
                file_list.append(fi)
        return FileListResponse(files=file_list, total=len(file_list))

    workspace_files = []
    if workspace_dir.exists():
        for item in workspace_dir.rglob("*"):
            if item.is_file():
                try:
                    stat = item.stat()
                    workspace_files.append(FileInfo(
                        filename=item.name,
                        file_path=str(item.relative_to(workspace_dir)),
                        file_type=item.suffix.lstrip(".") or "unknown",
                        size=stat.st_size,
                        uploaded_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    ))
                except Exception as e:
                    logger.warning(f"无法读取文件 {item}: {e}")

    generated_files = db.get_generated_files(session_id) if session_id else []

    all_files = workspace_files + [
        FileInfo(
            filename=f["filename"],
            file_path=f["file_path"],
            file_type=f["file_type"],
            size=f["size"],
            created_at=f["created_at"],
        )
        for f in generated_files
    ]

    return FileListResponse(files=all_files, total=len(all_files))


@router.post("/upload", summary="上传文件")
async def upload_file(
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
    session_id: Optional[str] = Query(default=None),
    file: UploadFile = File(...),
):
    upload_dir, workspace_dir = get_user_dirs(username)

    file_ext = os.path.splitext(file.filename)[1] if file.filename else ""
    unique_name = f"{uuid.uuid4().hex}{file_ext}"
    file_path = upload_dir / unique_name

    content = await file.read()
    with open(file_path, "wb") as f:
        f.write(content)

    file_type = file.content_type or "application/octet-stream"
    if file_ext:
        file_type = file_ext.lstrip(".")

    if session_id:
        db.add_session_file(
            session_id=session_id,
            filename=file.filename,
            file_path=str(file_path),
            file_type=file_type,
            size=len(content),
            username=username,
        )

    logger.info(f"上传文件 | 文件名: {file.filename} | 大小: {len(content)} bytes")

    return {
        "status": "success",
        "filename": file.filename,
        "file_path": str(file_path),
        "file_type": file_type,
        "size": len(content),
    }


@router.get("/download/{file_path:path}", summary="下载文件")
async def download_file(
    file_path: str,
    username: Annotated[str, Depends(get_current_username)],
):
    upload_dir, workspace_dir = get_user_dirs(username)

    full_path = upload_dir / file_path
    if not full_path.exists():
        full_path = workspace_dir / file_path

    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(
        path=str(full_path),
        filename=full_path.name,
        media_type="application/octet-stream",
    )


@router.delete("/{file_id}", summary="删除文件")
async def delete_file(
    file_id: int,
    db: Annotated[Database, Depends(get_database)],
):
    success = db.delete_file(file_id)
    if not success:
        raise HTTPException(status_code=404, detail="文件记录不存在")

    return {"status": "success", "message": "文件已删除"}


@router.get("/workspace/tree", summary="获取工作区目录结构")
async def get_workspace_tree(
    username: Annotated[str, Depends(get_current_username)],
    path: str = "",
):
    _, workspace_dir = get_user_dirs(username)
    target_dir = workspace_dir / path if path else workspace_dir

    if not target_dir.exists():
        raise HTTPException(status_code=404, detail="路径不存在")

    if target_dir.is_file():
        return {
            "name": target_dir.name,
            "path": str(target_dir.relative_to(workspace_dir)),
            "type": "file",
            "size": target_dir.stat().st_size,
        }

    items = []
    for item in sorted(target_dir.iterdir()):
        try:
            items.append({
                "name": item.name,
                "path": str(item.relative_to(workspace_dir)),
                "type": "directory" if item.is_dir() else "file",
                "size": item.stat().st_size if item.is_file() else 0,
            })
        except Exception:
            pass

    return {"path": path, "items": items}
