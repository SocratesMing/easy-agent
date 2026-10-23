"""File management routes"""

import asyncio
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse

from easy_agent.db.repositories.file_repository import FileRepository
from easy_agent.db.repositories.user_repository import UserRepository
from easy_agent.models.api import (
    DeleteFileResponse,
    FileInfo,
    FileListResponse,
    ParseFileResponse,
    SessionGeneratedFileInfo,
    UploadFileResponse,
)
from easy_agent.middleware import get_current_username
from easy_agent.middleware.auth import get_user_repository
from easy_agent.api.deps import (  # noqa: F401 - 旧模块级名字 re-export
    as_file_repository as _as_file_repository,
    get_file_repository,
)
from easy_agent.utils import parse_file_content
from easy_agent.bootstrap.config import Config
from easy_agent.services.session_uploads import (
    UploadTooLargeError,
    save_upload_streaming,
)
from easy_agent.services.upload_storage import (
    UploadStorageError,
    UploadStoreAccessDenied,
    UploadStoreNotFound,
    UploadStoreUnavailable,
    delete_stored_upload,
    is_store_uri,
    materialize_upload,
    save_upload_to_store,
    store_mode_enabled,
)

logger = logging.getLogger(__name__)

BASE_UPLOAD_DIR = Path("./data/uploads")
BASE_WORKSPACE_DIR = Path("./workspace")

HIDDEN_EXTENSIONS = frozenset({
    "py", "js", "ts", "vue", "html", "css", "json", "xml",
    "java", "go", "rs", "c", "cpp", "h", "sh", "bat",
    "log", "jsonl",
})

router = APIRouter(
    prefix="/agent/files",
    tags=["Files"],
)


# 批次 4：repository provider 统一在 ``api.deps``；旧名字在此 re-export。


def _remove_file_quietly(path: Path) -> None:
    """删除本地临时文件；失败只告警（不能掩盖真正的上传结果）。"""
    try:
        Path(path).unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"删除上传临时文件失败: {path} | {exc}")


def _store_http_error(exc: Exception) -> HTTPException:
    """对象存储异常 → HTTP 错误。

    存储不可用/读写失败一律 503：绝不"降级写本地"或返回成功但没存上。
    """
    if isinstance(exc, UploadStoreAccessDenied):
        return HTTPException(
            status_code=403,
            detail={"code": "UPLOAD_STORE_ACCESS_DENIED", "message": str(exc)},
        )
    if isinstance(exc, UploadStoreNotFound):
        return HTTPException(status_code=404, detail="文件不存在")
    if isinstance(exc, UploadStoreUnavailable):
        return HTTPException(
            status_code=503,
            detail={"code": "UPLOAD_STORE_UNAVAILABLE", "message": str(exc)},
        )
    return HTTPException(
        status_code=500,
        detail={"code": "UPLOAD_STORE_ERROR", "message": str(exc)},
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

    # .agent：各会话的框架产物（大工具结果 / 对话历史，DA-04），不给用户看
    _HIDDEN_DIRS = {"node_modules", ".venv", ".deps", "__pycache__", ".agent"}
    # 框架内部文件不列给用户：点开头（如记忆锁 ``.memory.jsonl.lock``）与锁文件。
    # 前者是运行时产物，后者是 ``memory_lock()`` 的跨进程锁，删不得也看不到才有意义。
    _HIDDEN_SUFFIXES = (".lock",)
    items = []
    try:
        for entry in sorted(
            target_dir.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower())
        ):
            if entry.name.startswith("."):
                continue
            if entry.is_dir() and entry.name in _HIDDEN_DIRS:
                continue
            if entry.is_file() and entry.name.endswith(_HIDDEN_SUFFIXES):
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


def _resolve_download_file(upload_dir: Path, workspace_dir: Path, file_path: str) -> Path:
    """下载只在本用户的上传目录、工作区内解析（依次查找，SEC-05）。

    ``upload_dir / "/etc/passwd"`` 会丢掉前缀，``../bob/...`` 能读别的用户的文件；
    每个目录都按 :func:`resolve_workspace_file` 校验：越界 400，都不存在 404。
    """
    for base in (upload_dir, workspace_dir):
        try:
            return resolve_workspace_file(base, file_path)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
    # 上传接口返回的落库路径相对服务进程目录（如 ``data/uploads/<user>/x``），不是相对上传目录；
    # 同样只认落在本用户目录内的真实文件。
    stored = Path(file_path).resolve()
    for base in (upload_dir, workspace_dir):
        root = base.resolve()
        if root in stored.parents and stored.is_file():
            return stored
    raise HTTPException(status_code=404, detail="文件不存在")


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


def serve_pdf_preview(full_path: Path, file_path: str) -> FileResponse:
    """把 Office 文档（PPT/XLS/DOC 等）转成 PDF 后内联返回，供前端内置查看器预览。

    转换会启动 LibreOffice 子进程并等它结束，是阻塞调用：async 路由要放进线程里调用。
    不支持的文件类型返回 400，转换失败返回 500。
    """
    try:
        from easy_agent.services.document_convert import convert_to_pdf

        pdf_path = convert_to_pdf(full_path, "pdf")
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


@router.get("/session/{session_id}", summary="获取会话生成的文件列表", response_model=list[SessionGeneratedFileInfo])
async def get_session_generated_files(
    session_id: str,
    db: Annotated[FileRepository, Depends(get_file_repository)],
    username: Annotated[str, Depends(get_current_username)],
):
    # 校验会话归属，防止跨用户读取其他会话生成的文件
    repo = _as_file_repository(db)
    repo.get_owned_session_meta(session_id, username)
    generated_files = repo.get_generated_files(session_id)

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
    db: Annotated[FileRepository, Depends(get_file_repository)],
    username: Annotated[str, Depends(get_current_username)],
    session_id: Optional[str] = Query(default=None),
    file_type: Optional[str] = Query(default=None),
):
    repo = _as_file_repository(db)
    upload_dir, _ = get_user_dirs(username)

    if session_id:
        # 校验会话归属，防止跨用户读取其他会话的文件列表
        repo.get_owned_session_meta(session_id, username)
        files = repo.get_session_files(session_id)
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
    if store_mode_enabled():
        # store 模式：本地 uploads 已不保留副本，资产页从 LangGraph Store 列举。
        from easy_agent.services.upload_storage import (
            UploadStorageError,
            list_stored_uploads,
        )

        try:
            stored = list_stored_uploads(username)
        except UploadStorageError as exc:
            raise _store_http_error(exc) from exc
        for item in stored:
            name = item["saved_name"]
            ext = Path(name).suffix.lstrip(".").lower()
            if ext in HIDDEN_EXTENSIONS:
                continue
            upload_files.append(
                FileInfo(
                    filename=name,
                    file_path=item["file_path"],
                    file_type=ext or "unknown",
                    size=int(item["size"]),
                    uploaded_at=str(item["created_at"]),
                )
            )
    elif upload_dir.exists():
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
    generated_names = repo.get_generated_filenames(username)
    if generated_names:
        upload_files = [f for f in upload_files if f.filename not in generated_names]

    all_files = upload_files
    if file_type:
        all_files = [f for f in all_files if f.file_type == file_type]

    return FileListResponse(files=all_files, total=len(all_files))


@router.post("/upload", summary="上传文件", response_model=UploadFileResponse)
async def upload_file(
    db: Annotated[FileRepository, Depends(get_file_repository)],
    username: Annotated[str, Depends(get_current_username)],
    file: UploadFile = File(...),
    session_id: Optional[str] = Query(default=None),
):
    repo = _as_file_repository(db)
    upload_dir, workspace_dir = get_user_dirs(username)

    safe_filename = os.path.basename(file.filename or "unknown")
    file_id = str(uuid.uuid4())[:8]
    saved_name = f"{file_id}_{safe_filename}"
    file_path = upload_dir / saved_name

    # 归属校验前置：被拒绝的上传不应留下本地文件或 Store 对象（孤儿副本）。
    if session_id:
        repo.get_owned_session_meta(session_id, username)

    try:
        store_mode = store_mode_enabled()
    except UploadStorageError as exc:
        raise _store_http_error(exc) from exc

    try:
        size = await save_upload_streaming(file, file_path)
    except UploadTooLargeError as exc:
        raise HTTPException(
            status_code=413,
            detail={
                "code": "UPLOAD_FILE_TOO_LARGE",
                "message": f"单个文件不能超过 {exc.limit // (1024 * 1024)}MB",
                "limit_mb": exc.limit // (1024 * 1024),
                "actual_mb": round(exc.size / 1024 / 1024, 2),
            },
        ) from exc

    ext = os.path.splitext(safe_filename)[1].lstrip(".") or "unknown"

    # 落库口径：local = 本地绝对路径（行为不变）；store = store:// URI。
    stored_path = str(file_path)
    if store_mode:
        try:
            stored_path = await asyncio.to_thread(
                save_upload_to_store, file_path, username, saved_name
            )
        except UploadStorageError as exc:
            _remove_file_quietly(file_path)
            logger.error(
                f"上传文件写入对象存储失败（本次上传已回滚）| {safe_filename} | {exc}"
            )
            raise _store_http_error(exc) from exc
        # 本地文件只用于接收流式上传；Store 已是权威副本，删除避免同一份文件存两处。
        _remove_file_quietly(file_path)

    file_id = None
    if session_id:
        file_id = repo.add_session_file(
            session_id=session_id,
            filename=safe_filename,
            file_path=stored_path,
            file_type=ext,
            size=size,
            username=username,
        )

    logger.info(
        f"文件上传成功 | 文件名: {safe_filename} | 大小: {size} bytes | 用户: {username}"
        f" | 存储: {'store' if store_mode else 'local'}"
    )

    return {
        "id": file_id,
        "filename": safe_filename,
        "file_path": stored_path,
        "file_type": ext,
        "size": size,
        "uploaded_at": datetime.now().isoformat(),
    }


@router.get("/download/{file_path:path}", summary="下载文件")
async def download_file(
    file_path: str,
    username: Annotated[str, Depends(get_current_username)],
):
    if is_store_uri(file_path):
        # 对象存储模式：materialize 到本地缓存后响应（跨用户由 URI 校验拒绝）。
        try:
            cached = await asyncio.to_thread(materialize_upload, file_path, username)
        except UploadStorageError as exc:
            raise _store_http_error(exc) from exc
        return FileResponse(
            path=str(cached),
            filename=cached.name,
            media_type="application/octet-stream",
        )

    upload_dir, workspace_dir = get_user_dirs(username)
    full_path = _resolve_download_file(upload_dir, workspace_dir, file_path)

    return FileResponse(
        path=str(full_path),
        filename=os.path.basename(file_path),
        media_type="application/octet-stream",
    )


@router.delete("/{file_id}", summary="删除文件", response_model=DeleteFileResponse)
async def delete_file(
    file_id: int,
    db: Annotated[FileRepository, Depends(get_file_repository)],
    username: Annotated[str, Depends(get_current_username)],
):
    repo = _as_file_repository(db)
    # 先取记录：DB 行删除后还要凭 file_path 判断是否需要清理 Store 对象。
    record = repo.get_file_record(file_id, username=username)
    success = repo.delete_file(file_id, username=username)
    if not success:
        raise HTTPException(status_code=404, detail="文件不存在")

    stored_path = (record or {}).get("file_path") or ""
    if is_store_uri(stored_path):
        try:
            await asyncio.to_thread(delete_stored_upload, stored_path, username)
        except Exception as exc:  # noqa: BLE001 - 清理失败不能回滚已完成的 DB 删除
            logger.warning(
                "删除对象存储文件失败（DB 记录已删除，对象可能残留）"
                f" | {stored_path} | {exc}"
            )

    return {"status": "deleted", "file_id": file_id}


@router.post("/parse", summary="解析文件内容", response_model=ParseFileResponse)
async def parse_file(
    username: Annotated[str, Depends(get_current_username)],
    file_path: str = Query(..., description="文件路径"),
):
    local_path = file_path
    if is_store_uri(file_path):
        # 对象存储里的原始文件不在本地：先 materialize 再解析。
        try:
            local_path = str(
                await asyncio.to_thread(materialize_upload, file_path, username)
            )
        except UploadStorageError as exc:
            raise _store_http_error(exc) from exc
    content = await asyncio.to_thread(parse_file_content, local_path)
    return {"file_path": file_path, "content": content}


@router.get("/preview", summary="预览工作区文件")
async def preview_file(
    request: Request,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    file_path: str = Query(..., description="文件在工作区中的相对路径"),
    session_id: Optional[str] = Query(default=None, description="会话ID"),
    token: Optional[str] = Query(default=None, description="认证token（iframe等无法设置Header时使用）"),
    download: Optional[bool] = Query(default=None, description="是否以下载方式返回"),
    target: Optional[str] = Query(default=None, description="转换为指定格式后返回，如 pdf（用 LibreOffice 转换，用于 PPT/XLS/DOC 预览）"),
    db: Annotated[FileRepository, Depends(get_file_repository)] = None,
):
    """返回工作区文件的原始内容，供前端预览组件使用。

    支持图片、PDF、Word、Excel、PPT、文本等常见格式的在线预览。
    认证方式：优先使用 Authorization Header，也支持 token 查询参数（用于 iframe 等场景）。

    当指定 target=pdf 且文件为可转换的 Office 类型时，使用 LibreOffice 将文件转换为
    PDF 后返回，由前端内置查看器渲染（解决 @vue-office/pptx 等组件渲染不稳定的问题）。
    """
    repo = _as_file_repository(db)
    # 认证：优先 Header，其次查询参数 token（均走单点登录 version 校验）
    username = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        from easy_agent.middleware.auth import verify_token_sso

        username = verify_token_sso(auth_header[7:], user_repo)
    if not username and token:
        from easy_agent.middleware.auth import verify_token_sso

        username = verify_token_sso(token, user_repo)
    if not username:
        default_user = repo.get_or_create_default_user()
        username = default_user.username

    workspace_dir = Config.get_user_workspace_dir(username)

    if session_id and repo:
        # 校验会话归属，防止通过他人 session_id 访问其工作区文件
        session_meta = repo.get_owned_session_meta(session_id, username)
        if session_meta["workspace_name"]:
            workspace_dir = workspace_dir / "session" / session_meta["workspace_name"]
        else:
            workspace_dir = workspace_dir / "session" / session_id

    store_backed = is_store_uri(file_path)
    if store_backed:
        # 上传原始文件的预览：materialize 后按上传文件响应，不经过工作区解析。
        try:
            full_path = await asyncio.to_thread(
                materialize_upload, file_path, username
            )
        except UploadStorageError as exc:
            raise _store_http_error(exc) from exc
    else:
        full_path = workspace_dir / file_path

        if not full_path.exists():
            raise HTTPException(status_code=404, detail="文件不存在")

        if not full_path.is_file():
            raise HTTPException(status_code=400, detail="路径不是文件")

    # 目标格式转换（如 PPT/XLS/DOC → PDF）
    if target and target.lower() == "pdf":
        return await asyncio.to_thread(serve_pdf_preview, full_path, file_path)

    if store_backed:
        media_type = _MIME_MAP.get(
            full_path.suffix.lower(), "application/octet-stream"
        )
        return FileResponse(
            path=str(full_path),
            filename=full_path.name,
            media_type="application/octet-stream" if download else media_type,
            content_disposition_type="attachment" if download else "inline",
        )

    return serve_workspace_file(workspace_dir, file_path, download=bool(download))


@router.get("/workspace/tree", summary="获取工作区文件树")
async def get_workspace_tree(
    username: Annotated[str, Depends(get_current_username)],
    path: str = Query(default="", description="目录路径"),
    session_id: Optional[str] = Query(
        default=None, description="会话ID，指定后只返回该会话的工作区目录"
    ),
    db: Annotated[FileRepository, Depends(get_file_repository)] = None,
):
    repo = _as_file_repository(db)
    workspace_dir = Config.get_user_workspace_dir(username)

    if session_id and repo:
        # 校验会话归属，防止通过他人 session_id 浏览其工作区文件树
        session_meta = repo.get_owned_session_meta(session_id, username)
        if session_meta["workspace_name"]:
            workspace_dir = workspace_dir / "session" / session_meta["workspace_name"]
        else:
            workspace_dir = workspace_dir / "session" / session_id
        logger.info(
            f"工作区文件树 | session_id={session_id} | workspace_name={session_meta['workspace_name']} | dir={workspace_dir}"
        )
    else:
        logger.info(f"工作区文件树 | 无 session_id | dir={workspace_dir}")

    target_dir = workspace_dir / path if path else workspace_dir

    if not target_dir.exists():
        logger.warning(f"工作区目录不存在 | path={target_dir}")
        return {"items": []}

    items = build_workspace_tree(workspace_dir, path)
    return {"items": items}
