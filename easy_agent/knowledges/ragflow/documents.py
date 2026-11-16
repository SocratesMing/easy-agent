"""RAG 平台文档接口：上传、更新、下载、列表、删除与解析控制。"""

from __future__ import annotations

import json
from typing import Any, BinaryIO

from easy_agent.knowledges.ragflow.errors import RagflowContractError
from easy_agent.knowledges.ragflow.transport import RagflowBinary


class DocumentMixin:
    """文档管理方法；由 RagflowClient 组合，复用其请求与校验基础设施。"""

    async def upload_documents(
        self,
        dataset_id: str,
        files: list[tuple[str, bytes | BinaryIO, str]],
        *,
        metadata: dict[str, Any] | str | None = None,
    ) -> list[dict[str, Any]]:
        """上传文档（RI006：POST /api/v1/datasets/{dataset_id}/documents）。

        Args:
            dataset_id: 目标知识库 ID。
            files: (文件名, 文件内容, MIME 类型) 元组列表，``file`` 字段
                多文件时重复提交。
            metadata: 可选 key/value 自定义元数据（文档示例字段），dict 时
                序列化为 JSON 字符串随表单提交。

        Returns:
            文档对象列表（含 id/name/dataset_id/run 等字段）。
        """
        self._require_non_empty(dataset_id, "dataset_id")
        if not files:
            raise ValueError("files 不能为空")
        multipart = [
            ("file", (filename, content, content_type))
            for filename, content, content_type in files
        ]
        form: dict[str, Any] | None = None
        if metadata is not None:
            form = {
                "metadata": (
                    json.dumps(metadata, ensure_ascii=False)
                    if isinstance(metadata, dict)
                    else metadata
                )
            }
        path = self._documents_path(dataset_id)
        data = await self._request("POST", path, files=multipart, form=form)
        if not isinstance(data, list) or not data:
            raise RagflowContractError("RAGFlow 上传文档未返回文档列表")
        return data

    async def upload_cbcm_documents(
        self,
        dataset_id: str,
        *,
        batch_id: str,
        file_part: str,
        object_name: str,
        service_id: str | None = None,
        file_list: list[str] | None = None,
        client_ip: str | None = None,
        client_port: int | None = None,
    ) -> list[dict[str, Any]]:
        """上传 CBCM 内容管理平台文件为虚拟文档（文档 2.2：
        POST /api/v1/datasets/{dataset_id}/cbcm_documents）。

        正式接入需开通 aigcqa 到指定内容管理平台文件夹的访问权限。

        Args:
            batch_id: 批次 ID（必填）。
            file_part: 文件部分标识（必填），如 "ERIM_PART"。
            object_name: 对象名称（必填），如 "ERIM_INFO"。
            service_id: 服务 ID。
            file_list: 文件编号列表。
            client_ip / client_port: 客户端 IP/端口，不传使用服务端默认值。
        """
        self._require_non_empty(dataset_id, "dataset_id")
        self._require_all(
            (batch_id, file_part, object_name),
            "batch_id/file_part/object_name 为必填字段",
        )
        body = self._clean_body(
            {
                "serviceId": service_id,
                "batchId": batch_id,
                "filePart": file_part,
                "objectName": object_name,
                "FileList": file_list,
                "clientIp": client_ip,
                "clientPort": client_port,
            }
        )
        path = self._format_path(
            "/api/v1/datasets/{dataset_id}/cbcm_documents", dataset_id=dataset_id
        )
        data = await self._request("POST", path, json_body=body)
        if not isinstance(data, list) or not data:
            raise RagflowContractError("RAGFlow CBCM 上传未返回文档列表")
        return data

    async def update_document(
        self,
        dataset_id: str,
        document_id: str,
        *,
        name: str | None = None,
        status: str | None = None,
        meta_fields: dict[str, Any] | None = None,
        chunk_method: str | None = None,
        parser_config: dict[str, Any] | None = None,
    ) -> None:
        """更新文档配置（文档 2.3：PUT /api/v1/datasets/{dataset_id}/documents/{document_id}）。

        Args:
            name: 文档名称。
            status: 可选 '0' / '1'。
            meta_fields: 文档元字段，可用于检索接口的条件查询。
            chunk_method: 解析方法。
            parser_config: 解析器配置。
        """
        self._require_non_empty(dataset_id, "dataset_id")
        self._require_non_empty(document_id, "document_id")
        body = self._clean_body(
            {
                "name": name,
                "status": status,
                "meta_fields": meta_fields,
                "chunk_method": chunk_method,
                "parser_config": parser_config,
            }
        )
        if not body:
            raise ValueError("至少需要传入一个更新字段")
        path = self._format_path(
            "/api/v1/datasets/{dataset_id}/documents/{document_id}",
            dataset_id=dataset_id,
            document_id=document_id,
        )
        await self._request("PUT", path, json_body=body)

    async def download_document(
        self,
        dataset_id: str,
        document_id: str,
    ) -> RagflowBinary:
        """下载文档（文档 2.4：GET /api/v1/datasets/{dataset_id}/documents/{document_id}）。"""
        self._require_non_empty(dataset_id, "dataset_id")
        self._require_non_empty(document_id, "document_id")
        path = self._format_path(
            "/api/v1/datasets/{dataset_id}/documents/{document_id}",
            dataset_id=dataset_id,
            document_id=document_id,
        )
        data = await self._request("GET", path, expect_binary=True)
        if not isinstance(data, RagflowBinary):
            raise RagflowContractError("RAGFlow 下载未返回文件内容")
        return data

    async def list_documents(
        self,
        dataset_id: str,
        *,
        page: int | None = None,
        page_size: int | None = None,
        orderby: str | None = None,
        desc: bool | None = None,
        keywords: str | None = None,
        document_id: str | None = None,
        name: str | None = None,
    ) -> dict[str, Any]:
        """查询知识库文档列表（RI007：GET /api/v1/datasets/{dataset_id}/documents）。

        实际 RAGFlow 版本按 ``id`` 精确过滤文档；``document_id`` 会被上游忽略，
        返回整页其它文档，导致调用方取 docs[0] 时把同批文档都显示成同一篇
        （通常是进度最慢那篇）的状态。这里统一把参数名映射为 ``id``。
        page / page_size（默认 30）/ keywords 为文档规定查询参数；orderby /
        desc / name 为文档未提及的可选参数，保持原状。

        Returns:
            ``{"docs": [...], "total": n}`` 形式的 data 字段，
            docs 内 run 字段标识解析状态（UNSTART/RUNNING/DONE/FAIL/CANCEL）。
        """
        self._require_non_empty(dataset_id, "dataset_id")
        # Ragflow 契约：page_size 上限 100，超出直接 code=100 拒绝
        if page_size is not None and page_size > 100:
            page_size = 100
        query = self._page_query(
            page,
            page_size,
            orderby=orderby,
            desc=desc,
            name=name,
            extra={"keywords": keywords, "id": document_id},
        )
        path = self._documents_path(dataset_id)
        data = await self._request("GET", path, query=query)
        if isinstance(data, list):
            return {"docs": data, "total": len(data)}
        return self._expect_dict(data, "RAGFlow 文档列表格式不符")

    async def delete_documents(
        self, dataset_id: str, document_ids: list[str]
    ) -> None:
        """删除文档（文档 2.6：DELETE /api/v1/datasets/{dataset_id}/documents）。"""
        self._require_non_empty(dataset_id, "dataset_id")
        self._require_ids(document_ids, "document_ids")
        path = self._documents_path(dataset_id)
        await self._request("DELETE", path, json_body={"ids": document_ids})

    async def parse_documents(
        self, dataset_id: str, document_ids: list[str]
    ) -> None:
        """解析文档（文档 2.7：POST /api/v1/datasets/{dataset_id}/chunks）。

        文档解析失败后需先调用 stop_parsing 才能重新提交解析。
        """
        self._require_non_empty(dataset_id, "dataset_id")
        self._require_ids(document_ids, "document_ids")
        path = self._format_path(
            "/api/v1/datasets/{dataset_id}/chunks", dataset_id=dataset_id
        )
        await self._request(
            "POST", path, json_body={"document_ids": document_ids}
        )

    async def stop_parsing(
        self, dataset_id: str, document_ids: list[str]
    ) -> None:
        """停止文档解析（文档 2.8：DELETE /api/v1/datasets/{dataset_id}/chunks）。"""
        self._require_non_empty(dataset_id, "dataset_id")
        self._require_ids(document_ids, "document_ids")
        path = self._format_path(
            "/api/v1/datasets/{dataset_id}/chunks", dataset_id=dataset_id
        )
        await self._request(
            "DELETE", path, json_body={"document_ids": document_ids}
        )
