"""RAG 平台知识库（dataset）接口：创建、更新、删除、列表、检索与团队共享。"""

from __future__ import annotations

import re
from typing import Any

from easy_agent.knowledges.ragflow.errors import RagflowContractError

# 知识库名称规则（文档 1.1）：字母/数字/下划线，以字母或下划线开头，最多 65535 字符。
_DATASET_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,65534}")


class DatasetMixin:
    """知识库管理方法；由 RagflowClient 组合，复用其请求与校验基础设施。"""

    async def create_dataset(
        self,
        *,
        name: str,
        description: str | None = None,
        embedding_model: str | None = None,
        permission: str | None = None,
        chunk_method: str | None = None,
        parser_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """创建知识库（文档 1.1：POST /api/v1/datasets）。

        Args:
            name: 唯一名称，仅允许字母/数字/下划线，以字母或下划线开头。
            description: 简要描述。
            embedding_model: embedding 模型，格式 "model_name"@"model_factory"。
            permission: 'me'（默认）/ 'team' / 'personal'。
            chunk_method: 'naive'（默认）/ 'qa' / 'table' / 'picture' /
                'knowledge_graph'。
            parser_config: 解析器配置，属性随 chunk_method 变化。
        """
        if not name or not _DATASET_NAME_RE.fullmatch(name):
            raise ValueError(
                "知识库名称仅允许英文字母/数字/下划线，且以字母或下划线开头"
            )
        body = self._clean_body(
            {
                "name": name,
                "description": description,
                "embedding_model": embedding_model,
                "permission": permission,
                "chunk_method": chunk_method,
                "parser_config": parser_config,
            }
        )
        data = await self._request("POST", "/api/v1/datasets", json_body=body)
        if not isinstance(data, dict) or not data.get("id"):
            raise RagflowContractError("RAGFlow 创建知识库未返回有效对象")
        return data

    async def delete_datasets(self, dataset_ids: list[str]) -> None:
        """删除知识库（文档 1.2：DELETE /api/v1/datasets，body 传 ids）。"""
        self._require_ids(dataset_ids, "dataset_ids")
        await self._request(
            "DELETE", "/api/v1/datasets", json_body={"ids": dataset_ids}
        )

    async def update_dataset(
        self,
        dataset_id: str,
        *,
        name: str | None = None,
        embedding_model: str | None = None,
        chunk_method: str | None = None,
        permission: str | None = None,
    ) -> None:
        """更新知识库配置（文档 1.3：PUT /api/v1/datasets/{dataset_id}）。

        更新 embedding_model 前需确保该库 chunk_count 为 0。
        """
        body = self._clean_body(
            {
                "name": name,
                "embedding_model": embedding_model,
                "chunk_method": chunk_method,
                "permission": permission,
            }
        )
        if not body:
            raise ValueError("至少需要传入一个更新字段")
        self._require_non_empty(dataset_id, "dataset_id")
        path = self._format_path("/api/v1/datasets/{dataset_id}", dataset_id=dataset_id)
        await self._request("PUT", path, json_body=body)

    async def list_datasets(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
        orderby: str | None = None,
        desc: bool | None = None,
        name: str | None = None,
        dataset_id: str | None = None,
    ) -> dict[str, Any]:
        """查询知识库列表（文档 1.4：GET /api/v1/datasets）。

        Returns:
            ``{"datasets": [...], "total": n}`` 形式的 data 字段。
        """
        query = self._page_query(
            page,
            page_size,
            orderby=orderby,
            desc=desc,
            name=name,
            extra={"id": dataset_id},
        )
        data = await self._request("GET", "/api/v1/datasets", query=query)
        if isinstance(data, list):
            return {"datasets": data, "total": len(data)}
        return self._expect_dict(data, "RAGFlow 知识库列表格式不符")

    async def search_datasets(
        self,
        *,
        question: str,
        dataset_ids: list[str],
        top_k: int | None = None,
        page: int | None = None,
        size: int | None = None,
        similarity_threshold: float | None = None,
        vector_similarity_weight: float | None = None,
        highlight: bool | None = None,
        use_kg: bool | None = None,
    ) -> dict[str, Any]:
        """知识库语义检索（RI009：POST /api/v1/datasets/search）。

        与 ``retrieve``（Ragflow 原生 ``/api/v1/retrieval``，文档未提及、
        保持原状）并列的平台检索接口；参数名以《接入文档 v1.1.1》为准：
        每页条数为 ``size``（服务端默认 10），召回条数为 ``top_k``（默认 5）。

        Args:
            question: 检索问题（必填）。
            dataset_ids: 知识库 id 列表（必填）。
            top_k: 召回条数，服务端默认 5。
            page: 页码，服务端默认 1。
            size: 每页条数，服务端默认 10。
            similarity_threshold: 相似度阈值，服务端默认 0.2。
            vector_similarity_weight: 向量相似度权重，服务端默认 0.3。
            highlight: 是否高亮命中文本，默认 false。
            use_kg: 是否启用知识图谱，默认 false。

        Returns:
            ``{"chunks": [...], "doc_aggs": [...], "total": n}`` 形式的
            data 字段，chunks 内含 chunk_id/content_with_weight/doc_id/
            kb_id/highlight/similarity 等字段。
        """
        self._require_non_empty(question, "question")
        self._require_ids(dataset_ids, "dataset_ids")
        body = self._clean_body(
            {
                "question": question,
                "dataset_ids": dataset_ids,
                "top_k": top_k,
                "page": page,
                "size": size,
                "similarity_threshold": similarity_threshold,
                "vector_similarity_weight": vector_similarity_weight,
                "highlight": highlight,
                "use_kg": use_kg,
            }
        )
        data = await self._request("POST", "/api/v1/datasets/search", json_body=body)
        return self._expect_dict(data, "RAG 平台知识库检索结果格式不符")

    async def join_team(self, *, email: str) -> dict[str, Any]:
        """邀请用户加入个人团队（文档 6.1：POST /api/v1/team）。

        被邀请用户可查看个人账号下权限为 team 的共享知识库。
        """
        self._require_non_empty(email, "email")
        data = await self._request(
            "POST", "/api/v1/team", json_body={"email": email}
        )
        return self._expect_dict(data, "RAGFlow 加入团队响应格式不符")
