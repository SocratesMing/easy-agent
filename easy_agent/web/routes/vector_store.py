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

    if not results:
        return {"results": [], "total": 0}

    formatted_results = []
    for i in range(len(results.get("ids", [[]])[0])):
        formatted_results.append({
            "id": results["ids"][0][i],
            "document": results["documents"][0][i],
            "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
            "distance": results["distances"][0][i] if results.get("distances") else 0,
        })

    return {
        "results": formatted_results,
        "total": len(formatted_results),
    }


@router.delete("/documents")
async def delete_documents(req: DeleteRequest, request: Request):
    vs = get_vector_store(request)

    if not req.ids and not req.where:
        raise HTTPException(status_code=400, detail="必须提供 ID 列表或过滤条件")

    before_count = vs.count()
    vs.delete(ids=req.ids, where=req.where)
    after_count = vs.count()

    return {
        "success": True,
        "deleted_count": before_count - after_count,
    }


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str, request: Request):
    vs = get_vector_store(request)
    doc = vs.get_by_id(doc_id)

    if not doc:
        raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")

    return doc


@router.put("/documents/{doc_id}")
async def update_document(doc_id: str, req: UpdateDocumentRequest, request: Request):
    vs = get_vector_store(request)
    vs.update_document(doc_id=doc_id, document=req.document, metadata=req.metadata)

    return {"success": True}


@router.get("/documents")
async def list_documents(request: Request, limit: int = 100, offset: int = 0):
    vs = get_vector_store(request)
    all_docs = vs.get_all(limit=limit, offset=offset)

    if not all_docs or not all_docs.get("ids"):
        return {"documents": [], "total": 0}

    documents = []
    for i in range(len(all_docs["ids"])):
        documents.append({
            "id": all_docs["ids"][i],
            "document": all_docs["documents"][i],
            "metadata": all_docs["metadatas"][i] if all_docs.get("metadatas") else {},
        })

    return {
        "documents": documents,
        "total": len(documents),
    }


@router.post("/clear")
async def clear_collection(request: Request):
    vs = get_vector_store(request)
    vs.clear()

    return {"success": True, "message": "向量数据库已清空"}
