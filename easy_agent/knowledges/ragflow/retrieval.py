"""RAG 平台检索接口：Ragflow 原生 retrieval 与平台场景检索（RI003/RI004）。"""

from __future__ import annotations

from typing import Any


class RetrievalMixin:
    """检索方法；由 RagflowClient 组合，复用其请求与校验基础设施。"""

    def _build_retrieval_body(
        self,
        *,
        question: str,
        dataset_ids: list[str] | None,
        document_ids: list[str] | None,
        page: int | None,
        page_size: int | None,
        similarity_threshold: float | None,
        vector_similarity_weight: float | None,
        top_k: int | None,
        rerank_id: str | None,
        keyword: bool | None,
        highlight: bool | None,
        parent_retrieval: bool | None,
        mix_ocr: bool | None,
        meta_fields: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """构造检索请求体（文档 3.5 参数，两个检索接口共用）。"""
        self._require_non_empty(question, "question")
        if not dataset_ids and not document_ids:
            raise ValueError("dataset_ids 与 document_ids 至少需要传入一个")
        return self._clean_body(
            {
                "question": question,
                "dataset_ids": dataset_ids,
                "document_ids": document_ids,
                "page": page,
                "page_size": page_size,
                "similarity_threshold": similarity_threshold,
                "vector_similarity_weight": vector_similarity_weight,
                "top_k": top_k,
                "rerank_id": rerank_id,
                "keyword": keyword,
                "highlight": highlight,
                "parent_retrieval": parent_retrieval,
                "mix_ocr": mix_ocr,
                "meta_fields": meta_fields,
            }
        )

    async def retrieve(
        self,
        *,
        question: str,
        dataset_ids: list[str] | None = None,
        document_ids: list[str] | None = None,
        page: int | None = None,
        page_size: int | None = None,
        similarity_threshold: float | None = None,
        vector_similarity_weight: float | None = None,
        top_k: int | None = None,
        rerank_id: str | None = None,
        keyword: bool | None = None,
        highlight: bool | None = None,
        parent_retrieval: bool | None = None,
        mix_ocr: bool | None = None,
        meta_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """从知识库检索 chunk（POST /api/v1/retrieval，标准 Ragflow 接口）。

        Returns:
            ``{"chunks": [...], "doc_aggs": [...], "total": n}``。
        """
        body = self._build_retrieval_body(
            question=question,
            dataset_ids=dataset_ids,
            document_ids=document_ids,
            page=page,
            page_size=page_size,
            similarity_threshold=similarity_threshold,
            vector_similarity_weight=vector_similarity_weight,
            top_k=top_k,
            rerank_id=rerank_id,
            keyword=keyword,
            highlight=highlight,
            parent_retrieval=parent_retrieval,
            mix_ocr=mix_ocr,
            meta_fields=meta_fields,
        )
        data = await self._request("POST", "/api/v1/retrieval", json_body=body)
        return self._expect_dict(data, "RAGFlow 检索结果格式不符")

    async def search_by_scene(
        self,
        *,
        scene_codes: list[str],
        question: str,
        top_k: int,
        datasets: list[str] | None = None,
        meta_data_filter: dict[str, Any] | None = None,
        rerank: str | None = None,
    ) -> dict[str, Any]:
        """按应用场景语义检索知识（RI003：POST /api/v1/abac/search）。

        按场景编码检索已绑定知识库，返回命中切片，调用方无需自行维护
        知识库 id。

        Args:
            scene_codes: 场景编码列表（必填）。
            question: 检索问题（必填）。
            top_k: 返回条数（必填）。
            datasets: 知识库 id 列表（可选）。
            meta_data_filter: 元数据过滤条件，形如
                ``{"method": "manual", "logic": "and", "manual":
                [{"key": ..., "value": ..., "op": "="}]}``。
            rerank: 是否启用 rerank 模型精细打分，``"Y"`` / ``"N"``，
                服务端默认 ``"N"``。

        Returns:
            ``{"chunks": [...], "doc_aggs": [...], "labels": ..., "total": n}``
            形式的 data 字段。
        """
        self._require_non_empty(question, "question")
        if not scene_codes or any(not code for code in scene_codes):
            raise ValueError("scene_codes 必须为非空编码列表")
        if top_k is None or top_k <= 0:
            raise ValueError("top_k 必须为正整数")
        if rerank is not None and rerank not in ("Y", "N"):
            raise ValueError("rerank 仅支持 Y / N")
        body = self._clean_body(
            {
                "scene_code": scene_codes,
                "question": question,
                "top_k": top_k,
                "datasets": datasets,
                "meta_data_filter": meta_data_filter,
                "rerank": rerank,
            }
        )
        data = await self._request("POST", "/api/v1/abac/search", json_body=body)
        return self._expect_dict(data, "RAG 平台场景检索结果格式不符")

    async def list_scene_knowledge_bases(
        self,
        *,
        scene_code: str,
        page: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        """按应用场景查询可用知识库（RI004：GET /api/v1/abac/kb）。

        Args:
            scene_code: 场景编码（必填）。
            page: 页码，服务端默认 1。
            page_size: 每页条数，服务端默认 100。

        Returns:
            ``{"items": [...], "total": n, "page": p, "page_size": s}`` 形式
            的 data 字段，items 内含 id/name/description/tenant_id/app_name/
            doc_num/chunk_num/status/has_access 等字段。
        """
        self._require_non_empty(scene_code, "scene_code")
        query = self._clean_query(
            {"page": page, "page_size": page_size, "scene_code": scene_code}
        )
        data = await self._request("GET", "/api/v1/abac/kb", query=query)
        return self._expect_dict(data, "RAG 平台场景知识库列表格式不符")
