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
from ..utils import parse_file_content, get_owned_session
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

_MIME_MAP = {
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


def get_user_dirs(username: str):
    safe_name = Config.sanitize_username(username)
    upload_dir = BASE_UPLOAD_DIR / safe_name
    workspace_dir = BASE_WORKSPACE_DIR / safe_name
    upload_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir, workspace_dir


def build_workspace_tree(workspace_dir: Path, path: str = "") -> list:
    """列出工作区某个目录下的文件树（与具体会话/任务解耦，便于复用）。"""
    target_dir = workspace_dir / path if path else workspace_dir
    if not target_dir.exists():
        return []
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
            items.append(
                {
                    "name": entry.name,
                    "path": str(entry.relative_to(workspace_dir)),
                    "type": "directory" if entry.is_dir() else "file",
                    "size": entry.stat().st_size if entry.is_file() else 0,
                }
            )
    except PermissionError:
        pass
    return items


def resolve_workspace_file(workspace_dir: Path, file_path: str) -> Path:
    """解析工作区文件绝对路径，并防止路径穿越攻击。"""
    full_path = (workspace_dir / file_path).resolve()
    base = workspace_dir.resolve()
    if full_path != base and base not in full_path.parents:
        raise HTTPException(status_code=400, detail="非法的文件路径")
    if not full_path.exists() or not full_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return full_path


def serve_workspace_file(
    workspace_dir: Path, file_path: str, download: bool = False
) -> FileResponse:
    """返回工作区文件的预览/下载响应。"""
    full_path = resolve_workspace_file(workspace_dir, file_path)
    ext = full_path.suffix.lower()
    media_type = _MIME_MAP.get(ext, "application/octet-stream")
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


@router.get("/session/{session_id}", summary="获取会话生成的文件列表")
async def get_session_generated_files(
    session_id: str,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    # 校验会话归属，防止跨用户读取其他会话生成的文件
    get_owned_session(db, session_id, username)
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
        # 校验会话归属，防止跨用户读取其他会话的文件列表
        get_owned_session(db, session_id, username)
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
        # 校验会话归属，防止向他人会话关联文件
        get_owned_session(db, session_id, username)
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
    success = db.delete_file(file_id, username=username)
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
    target: Optional[str] = Query(default=None, description="转换为指定格式后返回，如 pdf（用 LibreOffice 转换，用于 PPT/XLS/DOC 预览）"),
    db: Annotated[Database, Depends(get_database)] = None,
):
    """返回工作区文件的原始内容，供前端预览组件使用。

    支持图片、PDF、Word、Excel、PPT、文本等常见格式的在线预览。
    认证方式：优先使用 Authorization Header，也支持 token 查询参数（用于 iframe 等场景）。

    当指定 target=pdf 且文件为可转换的 Office 类型时，使用 LibreOffice 将文件转换为
    PDF 后返回，由前端内置查看器渲染（解决 @vue-office/pptx 等组件渲染不稳定的问题）。
    """
    # 认证：优先 Header，其次查询参数 token（均走单点登录 version 校验）
    username = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        from ..middleware.auth import verify_token_sso
        username = verify_token_sso(auth_header[7:], db)
    if not username and token:
        from ..middleware.auth import verify_token_sso
        username = verify_token_sso(token, db)
    if not username:
        default_user = db.get_or_create_default_user()
        username = default_user.username

    workspace_dir = Config.get_user_workspace_dir(username)

    if session_id and db:
        # 校验会话归属，防止通过他人 session_id 访问其工作区文件
        session = get_owned_session(db, session_id, username)
        if session.workspace_name:
            workspace_dir = workspace_dir / "session" / session.workspace_name
        else:
            workspace_dir = workspace_dir / "session" / session_id

    full_path = workspace_dir / file_path

    if not full_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    if not full_path.is_file():
        raise HTTPException(status_code=400, detail="路径不是文件")

    # 目标格式转换（如 PPT/XLS/DOC → PDF）
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

    return serve_workspace_file(workspace_dir, file_path, download=bool(download))


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

    if session_id and db:
        # 校验会话归属，防止通过他人 session_id 浏览其工作区文件树
        session = get_owned_session(db, session_id, username)
        if session.workspace_name:
            workspace_dir = workspace_dir / "session" / session.workspace_name
        else:
            workspace_dir = workspace_dir / "session" / session_id
        logger.info(
            f"工作区文件树 | session_id={session_id} | workspace_name={session.workspace_name} | dir={workspace_dir}"
        )
    else:
        logger.info(f"工作区文件树 | 无 session_id | dir={workspace_dir}")

    target_dir = workspace_dir / path if path else workspace_dir

    if not target_dir.exists():
        logger.warning(f"工作区目录不存在 | path={target_dir}")
        return {"items": []}

    items = build_workspace_tree(workspace_dir, path)
    return {"items": items}
