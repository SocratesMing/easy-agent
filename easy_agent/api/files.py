"""File management routes"""

import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response

from ..db import Database, get_database
from ..models.api import FileListResponse, FileInfo
from ..middleware import get_current_username
from ..utils import parse_file_content
from ..config import Config

logger = logging.getLogger(__name__)

BASE_UPLOAD_DIR = Path("./data/uploads")
BASE_WORKSPACE_DIR = Path("./workspace")

router = APIRouter(
    prefix="/api/files",
    tags=["Files"],
)


def get_user_dirs(username: str):
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
                    logger.warning(f"读取文件信息失败: {item} | {e}")

    upload_files = []
    if upload_dir.exists():
        for item in upload_dir.rglob("*"):
            if item.is_file():
                try:
                    stat = item.stat()
                    upload_files.append(FileInfo(
                        filename=item.name,
                        file_path=str(item.relative_to(upload_dir)),
                        file_type=item.suffix.lstrip(".") or "unknown",
                        size=stat.st_size,
                        uploaded_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    ))
                except Exception as e:
                    logger.warning(f"读取上传文件信息失败: {item} | {e}")

    all_files = upload_files + workspace_files
    if file_type:
        all_files = [f for f in all_files if f.file_type == file_type]

    return FileListResponse(files=all_files, total=len(all_files))


@router.post("/upload", summary="上传文件")
async def upload_file(
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
    file: UploadFile = File(...),
    session_id: Optional[str] = Query(default=None),
):
    upload_dir, workspace_dir = get_user_dirs(username)

    safe_filename = os.path.basename(file.filename or "unknown")
    file_id = str(uuid.uuid4())[:8]
    saved_name = f"{file_id}_{safe_filename}"
    file_path = upload_dir / saved_name

    content = await file.read()
    file_path.write_bytes(content)

    ext = os.path.splitext(safe_filename)[1].lstrip(".") or "unknown"

    if session_id:
        db.add_session_file(
            session_id=session_id,
            filename=safe_filename,
            file_path=str(file_path),
            file_type=ext,
            size=len(content),
            username=username,
        )

    logger.info(f"文件上传成功 | 文件名: {safe_filename} | 大小: {len(content)} bytes | 用户: {username}")

    return {
        "filename": safe_filename,
        "file_path": str(file_path),
        "file_type": ext,
        "size": len(content),
        "uploaded_at": datetime.now().isoformat(),
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

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    return FileResponse(
        path=str(full_path),
        filename=os.path.basename(file_path),
        media_type="application/octet-stream",
    )


@router.delete("/{file_id}", summary="删除文件")
async def delete_file(
    file_id: int,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    success = db.delete_file(file_id)
    if not success:
        raise HTTPException(status_code=404, detail="文件不存在")

    return {"status": "deleted", "file_id": file_id}


@router.post("/parse", summary="解析文件内容")
async def parse_file(
    username: Annotated[str, Depends(get_current_username)],
    file_path: str = Query(..., description="文件路径"),
):
    content = parse_file_content(file_path)
    return {"file_path": file_path, "content": content}
