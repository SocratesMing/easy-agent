"""知识库文档路由：列表、上传、移动、原文下载、删除与解析控制。"""

from __future__ import annotations

import logging
import re
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse

from easy_agent.knowledges.core.models import (
    DocumentListResponse,
    DocumentMoveRequest,
    DocumentStatus,
    DocumentSummary,
    DocumentUploadAccepted,
    KnowledgePrincipal,
    OperationListResponse,
)
from easy_agent.knowledges.service import KnowledgeService

from .deps import _call, _request_id, _service, get_knowledge_principal


logger = logging.getLogger("easy_agent.knowledge.upload")

router = APIRouter()


@router.get("/bases/{base_id}/documents", response_model=DocumentListResponse)
async def list_documents(
    base_id: str,
    request: Request,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    folder_id: str | None = Query(default=None),
    document_status: DocumentStatus | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, max_length=200),
    direct_only: bool = Query(default=False),
) -> DocumentListResponse:
    """分页列出知识库文档，支持状态过滤与关键字搜索（搜索覆盖整个知识库）。"""

    return await _call(
        service.list_documents,
        base_id=base_id,
        principal=principal,
        page=page,
        page_size=page_size,
        folder_id=folder_id,
        direct_only=direct_only,
        status=document_status.value if document_status else None,
        query=q,
        request_id=_request_id(request),
    )


def _upload_size(file: UploadFile) -> int:
    """获取上传文件的字节大小，读取后恢复原文件指针位置。"""

    position = file.file.tell()
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(position)
    return size


_DOWNLOAD_CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _download_content_type(filename: str, fallback: str) -> str:
    """按文件后缀推断下载响应的 Content-Type，未知后缀回退给定值。"""

    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return _DOWNLOAD_CONTENT_TYPES.get(suffix, fallback or "application/octet-stream")


def _parse_single_range(value: str, size: int) -> tuple[int, int]:
    """解析单区间 Range 头为闭区间 [start, end]，非法值抛 ValueError。"""

    match = re.fullmatch(r"bytes=(\d*)-(\d*)", value.strip())
    if not match or "," in value or size <= 0:
        raise ValueError("invalid range")
    start_text, end_text = match.groups()
    if not start_text and not end_text:
        raise ValueError("empty range")
    if not start_text:
        suffix = int(end_text)
        if suffix <= 0:
            raise ValueError("invalid suffix")
        return max(0, size - suffix), size - 1
    start = int(start_text)
    if start >= size:
        raise ValueError("range starts after content")
    end = int(end_text) if end_text else size - 1
    if end < start:
        raise ValueError("range end before start")
    return start, min(end, size - 1)


@router.post(
    "/bases/{base_id}/documents",
    response_model=DocumentUploadAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    base_id: str,
    request: Request,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
    file: UploadFile = File(..., alias="file"),
    folder_id: str | None = Form(default=None),
    relative_path: Annotated[str | None, Form(max_length=1024)] = None,
) -> DocumentUploadAccepted:
    """上传单个文档：先落盘原文再提交上游解析，返回 202 受理。"""

    filename = file.filename or "<未命名>"
    declared_type = file.content_type or "application/octet-stream"
    try:
        size_bytes = await run_in_threadpool(_upload_size, file)
        if size_bytes > service.config.limits.max_request_size_mb * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail={
                    "code": "KNOWLEDGE_REQUEST_TOO_LARGE",
                    "message": "上传请求超过限制",
                    "retryable": False,
                },
            )
        await file.seek(0)
        accepted = await _call(
            service.upload_document,
            base_id=base_id,
            folder_id=folder_id,
            filename=file.filename or "",
            content_type=file.content_type or "application/octet-stream",
            size_bytes=size_bytes,
            content=file.file,
            principal=principal,
            request_id=_request_id(request),
            relative_path=relative_path,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        logger.warning(
            "[知识库] 文档上传失败 | 文件=%s | 类型=%s | 知识库=%s | 用户=%s | %s | %s",
            filename,
            declared_type,
            base_id,
            principal.username,
            detail.get("code") or f"HTTP_{exc.status_code}",
            detail.get("message") or (exc.detail if isinstance(exc.detail, str) else ""),
        )
        raise
    logger.info(
        "[知识库] 文档上传成功 | 文件=%s | 类型=%s | 知识库=%s | 用户=%s | 文档ID=%s | 大小=%dKB",
        filename,
        declared_type,
        base_id,
        principal.username,
        accepted.document.id,
        max(size_bytes, 0) // 1024,
    )
    return accepted


@router.patch("/documents/{document_id}", response_model=DocumentSummary)
async def move_document(
    document_id: str,
    payload: DocumentMoveRequest,
    request: Request,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> DocumentSummary:
    """移动文档到指定文件夹。"""

    return await _call(
        service.move_document,
        document_id=document_id,
        folder_id=payload.folder_id,
        principal=principal,
        request_id=_request_id(request),
    )


@router.get("/documents/{document_id}/content")
async def get_document_content(
    document_id: str,
    request: Request,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
    disposition: Literal["inline", "attachment"] = Query(default="inline"),
) -> Response:
    """下载或在线预览文档原文，支持单区间 Range 请求。"""

    document, binary = await _call(
        service.download_document,
        document_id=document_id,
        principal=principal,
        request_id=_request_id(request),
    )
    filename = quote(str(document["name"]), safe="")
    size = binary.size_bytes
    headers = {
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
        "X-Content-Type-Options": "nosniff",
        "Accept-Ranges": "bytes",
        "Content-Length": str(size),
    }
    if binary.sha256:
        headers["ETag"] = f'"sha256-{binary.sha256}"'
    range_header = request.headers.get("Range")
    status_code = status.HTTP_200_OK
    start = 0
    end = size - 1
    if range_header:
        try:
            start, end = _parse_single_range(range_header, size)
        except (TypeError, ValueError):
            return Response(
                status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
                # 416 响应无实体主体；沿用原 Content-Length 会让 HTTP 客户端
                # 一直等待永远不会到达的字节（curl 报 error 18）。
                headers={
                    **headers,
                    "Content-Range": f"bytes */{size}",
                    "Content-Length": "0",
                },
            )
        status_code = status.HTTP_206_PARTIAL_CONTENT
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(
        binary.iter_bytes(start, end),
        status_code=status_code,
        media_type=_download_content_type(str(document["name"]), binary.content_type),
        headers=headers,
    )


@router.delete("/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str,
    request: Request,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> Response:
    """删除文档：立即同步删除 RAGFlow 中的副本，本地保留软删除记录与原文。"""

    await _call(
        service.delete_document,
        document_id=document_id,
        principal=principal,
        request_id=_request_id(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/documents/{document_id}/retry",
    response_model=DocumentUploadAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_document(
    document_id: str,
    request: Request,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> DocumentUploadAccepted:
    """重试失败的文档解析，返回 202 受理。"""

    return await _call(
        service.retry_document,
        document_id=document_id,
        principal=principal,
        request_id=_request_id(request),
    )


@router.post(
    "/documents/{document_id}/pause",
    status_code=status.HTTP_200_OK,
)
async def pause_document(
    document_id: str,
    request: Request,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> dict:
    """暂停未就绪文档的解析任务（等待解析/解析中可暂停，已暂停可恢复）。"""

    return await _call(
        service.pause_document,
        document_id=document_id,
        principal=principal,
        request_id=_request_id(request),
    )


@router.post(
    "/documents/{document_id}/resume",
    status_code=status.HTTP_200_OK,
)
async def resume_document(
    document_id: str,
    request: Request,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
) -> dict:
    """恢复已暂停文档的解析任务，重新提交解析队列。"""

    return await _call(
        service.resume_document,
        document_id=document_id,
        principal=principal,
        request_id=_request_id(request),
    )


# ---------------------------------------------------------------------------
# 操作记录（仅列表）
# ---------------------------------------------------------------------------
@router.get("/operations", response_model=OperationListResponse)
async def list_operations(
    request: Request,
    service: Annotated[KnowledgeService, Depends(_service)],
    principal: Annotated[KnowledgePrincipal, Depends(get_knowledge_principal)],
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> OperationListResponse:
    """分页查询当前用户发起的操作记录。"""

    return await _call(
        service.list_operations,
        principal=principal,
        page=page,
        page_size=page_size,
        request_id=_request_id(request),
    )
