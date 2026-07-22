"""File management routes"""

import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from ..db import Database, get_database
from ..models.api import FileListResponse, FileInfo
from ..middleware import get_current_username
from ..utils import parse_file_content
from ..config import Config

logger = logging.getLogger(__name__)

BASE_UPLOAD_DIR = Path("./data/uploads")
BASE_WORKSPACE_DIR = Path("./workspace")

HIDDEN_EXTENSIONS = frozenset({
    "py", "js", "ts", "vue", "html", "css", "json", "xml",
    "java", "go", "rs", "c", "cpp", "h", "sh", "bat",
    "log", "jsonl",
})

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
        file_list.append(
            {
                "id": f["id"],
                "filename": f["filename"],
                "file_path": f["file_path"],
                "file_type": f["file_type"],
                "size": f["size"],
                "created_at": f["created_at"],
            }
        )

    return file_list


@router.get("/list", summary="获取文件列表", response_model=FileListResponse)
async def list_files(
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
    session_id: Optional[str] = Query(default=None),
    file_type: Optional[str] = Query(default=None),
):
    upload_dir, _ = get_user_dirs(username)

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

    upload_files = []
    if upload_dir.exists():
        for item in upload_dir.rglob("*"):
            if item.is_file():
                ext = item.suffix.lstrip(".").lower()
                if ext in HIDDEN_EXTENSIONS:
                    continue
                try:
                    stat = item.stat()
                    upload_files.append(
                        FileInfo(
                            filename=item.name,
                            file_path=str(item.relative_to(upload_dir)),
                            file_type=ext or "unknown",
                            size=stat.st_size,
                            uploaded_at=datetime.fromtimestamp(
                                stat.st_mtime
                            ).isoformat(),
                        )
                    )
                except Exception as e:
                    logger.warning(f"读取上传文件信息失败: {item} | {e}")

    # 排除会话中生成的文件：生成文件物理上位于 workspace 目录，正常不会出现在 uploads，
    # 此处以文件名为黑名单做双保险，确保资产页只展示用户上传的文件。
    generated_names = db.get_generated_filenames(username)
    if generated_names:
        upload_files = [f for f in upload_files if f.filename not in generated_names]

    all_files = upload_files
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

    logger.info(
        f"文件上传成功 | 文件名: {safe_filename} | 大小: {len(content)} bytes | 用户: {username}"
    )

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


@router.get("/preview", summary="预览工作区文件")
async def preview_file(
    request: Request,
    file_path: str = Query(..., description="文件在工作区中的相对路径"),
    session_id: Optional[str] = Query(default=None, description="会话ID"),
    token: Optional[str] = Query(default=None, description="认证token（iframe等无法设置Header时使用）"),
    download: Optional[bool] = Query(default=None, description="是否以下载方式返回"),
    db: Annotated[Database, Depends(get_database)] = None,
):
    """返回工作区文件的原始内容，供前端预览组件使用。

    支持图片、PDF、Word、Excel、PPT、文本等常见格式的在线预览。
    认证方式：优先使用 Authorization Header，也支持 token 查询参数（用于 iframe 等场景）。
    """
    # 认证：优先 Header，其次查询参数 token
    username = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        from ..utils.auth import get_username_from_token
        username = get_username_from_token(auth_header[7:])
    if not username and token:
        from ..utils.auth import get_username_from_token
        username = get_username_from_token(token)
    if not username:
        default_user = db.get_or_create_default_user()
        username = default_user.username

    workspace_dir = Config.get_user_workspace_dir(username)

    if session_id:
        session = db.get_session(session_id) if db else None
        if session and session.username:
            workspace_dir = Config.get_user_workspace_dir(session.username)
        if session and session.workspace_name:
            workspace_dir = workspace_dir / session.workspace_name
        else:
            workspace_dir = workspace_dir / session_id

    full_path = workspace_dir / file_path

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="路径不是文件")

    ext = full_path.suffix.lower()

    # MIME 类型映射
    mime_map = {
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xls": "application/vnd.ms-excel",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".ppt": "application/vnd.ms-powerpoint",
        ".txt": "text/plain; charset=utf-8",
        ".md": "text/plain; charset=utf-8",
        ".json": "text/plain; charset=utf-8",
        ".csv": "text/plain; charset=utf-8",
        ".xml": "text/plain; charset=utf-8",
        ".html": "text/plain; charset=utf-8",
        ".css": "text/plain; charset=utf-8",
        ".js": "text/plain; charset=utf-8",
        ".ts": "text/plain; charset=utf-8",
        ".py": "text/plain; charset=utf-8",
        ".java": "text/plain; charset=utf-8",
        ".go": "text/plain; charset=utf-8",
        ".rs": "text/plain; charset=utf-8",
        ".c": "text/plain; charset=utf-8",
        ".cpp": "text/plain; charset=utf-8",
        ".h": "text/plain; charset=utf-8",
        ".sh": "text/plain; charset=utf-8",
        ".sql": "text/plain; charset=utf-8",
        ".yaml": "text/plain; charset=utf-8",
        ".yml": "text/plain; charset=utf-8",
        ".toml": "text/plain; charset=utf-8",
        ".ini": "text/plain; charset=utf-8",
        ".cfg": "text/plain; charset=utf-8",
        ".conf": "text/plain; charset=utf-8",
        ".log": "text/plain; charset=utf-8",
        ".vue": "text/plain; charset=utf-8",
    }

    media_type = mime_map.get(ext, "application/octet-stream")

    if download:
        return FileResponse(
            path=str(full_path),
            filename=full_path.name,
            media_type="application/octet-stream",
            content_disposition_type="attachment",
        )

    return FileResponse(
        path=str(full_path),
        filename=full_path.name,
        media_type=media_type,
        content_disposition_type="inline",
    )


@router.get("/workspace/tree", summary="获取工作区文件树")
async def get_workspace_tree(
    username: Annotated[str, Depends(get_current_username)],
    path: str = Query(default="", description="目录路径"),
    session_id: Optional[str] = Query(
        default=None, description="会话ID，指定后只返回该会话的工作区目录"
    ),
    db: Annotated[Database, Depends(get_database)] = None,
):
    workspace_dir = Config.get_user_workspace_dir(username)

    if session_id:
        session = db.get_session(session_id) if db else None
        # 使用会话所属用户的 workspace 目录，避免当前登录用户与会话所有者不一致时路径错误
        if session and session.username:
            workspace_dir = Config.get_user_workspace_dir(session.username)
        if session and session.workspace_name:
            workspace_dir = workspace_dir / session.workspace_name
        else:
            workspace_dir = workspace_dir / session_id
        logger.info(
            f"工作区文件树 | session_id={session_id} | workspace_name={session.workspace_name if session else 'N/A'} | dir={workspace_dir}"
        )
    else:
        logger.info(f"工作区文件树 | 无 session_id | dir={workspace_dir}")

    target_dir = workspace_dir / path if path else workspace_dir

    if not target_dir.exists():
        logger.warning(f"工作区目录不存在 | path={target_dir}")
        return {"items": []}

    if not target_dir.is_dir():
        raise HTTPException(status_code=400, detail="路径不是目录")

    _HIDDEN_DIRS = {"node_modules", ".venv", ".deps", "__pycache__"}

    items = []
    try:
        for entry in sorted(
            target_dir.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())
        ):
            if entry.is_dir() and entry.name in _HIDDEN_DIRS:
                continue
            item = {
                "name": entry.name,
                "path": str(entry.relative_to(workspace_dir)),
                "type": "directory" if entry.is_dir() else "file",
                "size": entry.stat().st_size if entry.is_file() else 0,
            }
            items.append(item)
    except PermissionError:
        pass

    return {"items": items}
