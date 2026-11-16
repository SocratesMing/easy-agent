"""RAG 平台 chunk 接口：添加、列表、删除与更新。"""

from __future__ import annotations

from typing import Any


class ChunkMixin:
    """chunk 管理方法；由 RagflowClient 组合，复用其请求与校验基础设施。"""

    async def add_chunk(
        self,
        dataset_id: str,
        document_id: str,
        *,
        content: str,
        important_keywords: list[str] | None = None,
        questions: list[str] | None = None,
        meta_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """添加 chunk（文档 3.1：POST /api/v1/datasets/{dataset_id}/documents/{document_id}/chunks）。"""
        self._require_non_empty(dataset_id, "dataset_id")
        self._require_non_empty(document_id, "document_id")
        self._require_non_empty(content, "content")
        body = self._clean_body(
            {
                "content": content,
                "important_keywords": important_keywords,
                "questions": questions,
                "meta_fields": meta_fields,
            }
        )
        path = self._format_path(
            "/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks",
            dataset_id=dataset_id,
            document_id=document_id,
        )
        data = await self._request("POST", path, json_body=body)
        return self._expect_dict(data, "RAGFlow 添加 chunk 未返回对象")

    async def list_chunks(
        self,
        dataset_id: str,
        document_id: str,
        *,
        keywords: str | None = None,
        page: int | None = None,
        page_size: int | None = None,
        chunk_id: str | None = None,
    ) -> dict[str, Any]:
        """查询 chunk 列表（文档 3.2：GET /api/v1/datasets/{dataset_id}/documents/{document_id}/chunks）。

        Returns:
            ``{"chunks": [...], "doc": {...}, "total": n}`` 形式的 data 字段。
        """
        self._require_non_empty(dataset_id, "dataset_id")
        self._require_non_empty(document_id, "document_id")
        query = self._clean_query(
            {
                "keywords": keywords,
                "page": page,
                "page_size": page_size,
                "id": chunk_id,
            }
        )
        path = self._format_path(
            "/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks",
            dataset_id=dataset_id,
            document_id=document_id,
        )
        data = await self._request("GET", path, query=query)
        return self._expect_dict(data, "RAGFlow chunk 列表格式不符")

    async def delete_chunks(
        self, dataset_id: str, document_id: str, chunk_ids: list[str]
    ) -> None:
        """删除 chunk（文档 3.3：DELETE /api/v1/datasets/{dataset_id}/documents/{document_id}/chunks）。"""
        self._require_non_empty(dataset_id, "dataset_id")
        self._require_non_empty(document_id, "document_id")
        self._require_ids(chunk_ids, "chunk_ids")
        path = self._format_path(
            "/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks",
            dataset_id=dataset_id,
            document_id=document_id,
        )
        await self._request("DELETE", path, json_body={"chunk_ids": chunk_ids})

    async def update_chunk(
        self,
        dataset_id: str,
        document_id: str,
        chunk_id: str,
        *,
        content: str | None = None,
        important_keywords: list[str] | None = None,
        available: bool | None = None,
    ) -> None:
        """更新 chunk（文档 3.4：PUT /api/v1/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}）。"""
        self._require_non_empty(dataset_id, "dataset_id")
        self._require_non_empty(document_id, "document_id")
        self._require_non_empty(chunk_id, "chunk_id")
        body = self._clean_body(
            {
                "content": content,
                "important_keywords": important_keywords,
                "available": available,
            }
        )
        if not body:
            raise ValueError("至少需要传入一个更新字段")
        path = self._format_path(
            "/api/v1/datasets/{dataset_id}/documents/{document_id}/chunks/{chunk_id}",
            dataset_id=dataset_id,
            document_id=document_id,
            chunk_id=chunk_id,
        )
        await self._request("PUT", path, json_body=body)
