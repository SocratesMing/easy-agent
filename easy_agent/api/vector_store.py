"""Vector Store API Routes"""

import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/vectors", tags=["向量数据库"])


class AddDocumentsRequest(BaseModel):
    documents: list[str]
    metadatas: Optional[list[dict]] = None
    ids: Optional[list[str]] = None


class SearchRequest(BaseModel):
    query: str
    n_results: int = 5
    where: Optional[dict] = None


class DeleteRequest(BaseModel):
    ids: Optional[list[str]] = None
    where: Optional[dict] = None


class UpdateDocumentRequest(BaseModel):
    document: str
    metadata: Optional[dict] = None


def get_vector_store(request: Request):
    vs = getattr(request.app.state, 'vector_store', None)
    if not vs or not vs.is_ready:
        raise HTTPException(status_code=503, detail="向量数据库未启用或初始化失败")
    return vs


@router.get("/status")
async def get_vector_store_status(request: Request):
    vs = getattr(request.app.state, 'vector_store', None)
    if not vs:
        return {
            "enabled": False,
            "ready": False,
            "message": "向量数据库未启用",
        }
    return {
        "enabled": True,
        "ready": vs.is_ready,
        "collection_name": vs.collection_name,
        "embedding_provider": vs.embedding_provider,
        "document_count": vs.count(),
        "db_path": vs.db_path,
    }


@router.post("/documents")
async def add_documents(req: AddDocumentsRequest, request: Request):
    vs = get_vector_store(request)

    if not req.documents:
        raise HTTPException(status_code=400, detail="文档列表不能为空")

    if req.ids and len(req.ids) != len(req.documents):
        raise HTTPException(status_code=400, detail="ID列表长度必须与文档列表一致")

    if req.metadatas and len(req.metadatas) != len(req.documents):
        raise HTTPException(status_code=400, detail="元数据列表长度必须与文档列表一致")

    if not req.ids:
        req.ids = [str(uuid.uuid4()) for _ in req.documents]

    added_count = vs.add_documents(
        documents=req.documents,
        metadatas=req.metadatas,
        ids=req.ids,
    )

    return {
        "success": True,
        "added_count": added_count,
        "ids": req.ids,
    }


@router.post("/search")
async def search_documents(req: SearchRequest, request: Request):
    vs = get_vector_store(request)

    if not req.query:
        raise HTTPException(status_code=400, detail="查询内容不能为空")

    results = vs.search(
        query=req.query,
        n_results=req.n_results,
        where=req.where,
    )

    return {
        "success": True,
        "query": req.query,
        "results": results,
        "count": len(results),
    }


@router.post("/delete")
async def delete_documents(req: DeleteRequest, request: Request):
    vs = get_vector_store(request)

    if not req.ids and not req.where:
        raise HTTPException(status_code=400, detail="必须提供 ids 或 where 条件")

    deleted_count = vs.delete(ids=req.ids, where=req.where)

    return {
        "success": True,
        "deleted_count": deleted_count,
    }


@router.put("/documents/{doc_id}")
async def update_document(doc_id: str, req: UpdateDocumentRequest, request: Request):
    vs = get_vector_store(request)

    vs.update_document(
        doc_id=doc_id,
        document=req.document,
        metadata=req.metadata,
    )

    return {
        "success": True,
        "doc_id": doc_id,
    }


@router.get("/documents")
async def list_documents(
    request: Request,
    limit: int = 100,
    offset: int = 0,
):
    vs = get_vector_store(request)

    documents = vs.get_all(limit=limit, offset=offset)

    return {
        "success": True,
        "documents": documents,
        "count": len(documents),
    }
