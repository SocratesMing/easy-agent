"""Ragflow v0.26.3 本地服务标准 HTTP API 客户端（新版 knowledges 模块）。

设计约定:
- 接口路径、HTTP 方法、请求参数均按 Ragflow v0.26.3 官方 HTTP API 实现
  （标准 /api/v1/* 路径），客户端内不读任何环境变量；base_url / api_key
  由服务层从配置（RAGFLOW_BASE_URL / RAGFLOW_API_KEY）解析后经构造函数注入。
- 每个请求自动携带 ``Authorization: Bearer {api_key}``。
- 响应统一按 ``{"code": 0, "data": ...}`` 信封解码，成功时返回 ``data``
  字段原样内容（可能为 dict / list / bool / None）；非 0 code 与非 2xx
  HTTP 状态码转译为 errors.py 中的稳定异常。
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass
from typing import Any, BinaryIO
from urllib.parse import quote

import httpx

from .errors import (
    RagflowAuthenticationError,
    RagflowContractError,
    RagflowError,
    RagflowNotFoundError,
    RagflowRateLimitError,
    RagflowUnavailableError,
)

logger = logging.getLogger(__name__)

# 响应信封约定（Ragflow 官方 API 各接口响应示例）。
_SUCCESS_CODE = 0
_AUTH_ERROR_CODES = frozenset({109})
# 信封 message 中的"资源不存在/无权访问"特征，用于转译 NotFound 异常。
_NOT_FOUND_MARKERS = (
    "not exist",
    "doesn't exist",
    "can't find",
    "cannot find",
    "not found",
    "don't own",
    "doesn't own",
    "doesn't have",
    "does not have",
)
# 知识库名称规则（文档 1.1）：字母/数字/下划线，以字母或下划线开头，最多 65535 字符。
_DATASET_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,65534}")

# 重试退避参数（对传输错误、HTTP 429 与 5xx 生效）。
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 8.0


@dataclass(frozen=True, slots=True)
class RagflowBinary:
    """下载接口返回的二进制内容。"""

    content: bytes
    content_type: str
    content_disposition: str | None = None


class RagflowClient:
    """Ragflow v0.26.3 标准 HTTP API 客户端：32 个接口的类型化异步封装。

    用法::

        async with RagflowClient(base_url, api_key) as client:
            datasets = await client.list_datasets(page=1, page_size=30)
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 60.0,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        """
        Args:
            base_url: Ragflow 本地服务地址（RAGFLOW_BASE_URL）。
            api_key: Bearer 认证令牌（RAGFLOW_API_KEY）。
            timeout: 单次请求超时秒数。
            max_retries: 传输错误/限流/5xx 的最大重试次数（不含首次请求）。
            transport: 可注入的 httpx 传输层（测试用）。
        """
        if not base_url or not base_url.strip():
            raise ValueError("base_url 不能为空")
        if not api_key or not api_key.strip():
            raise ValueError("api_key 不能为空")
        if max_retries < 0:
            raise ValueError("max_retries 不能为负数")
        self._base_url = base_url.strip().rstrip("/")
        self._api_key = api_key.strip()
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(timeout),
        )

    async def __aenter__(self) -> "RagflowClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        """关闭底层 HTTP 连接池。"""
        await self._client.aclose()

    # ─────────────────────────── 请求基础设施 ───────────────────────────

    def _base_headers(self) -> dict[str, str]:
        """所有请求共用的 Bearer 认证头。"""
        return {
            "Authorization": f"Bearer {self._api_key}",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        files: list[tuple[str, tuple[str, bytes | BinaryIO, str]]] | None = None,
        expect_binary: bool = False,
    ) -> Any:
        """统一请求入口：注入头、重试、HTTP 状态与信封错误转译。"""
        url = self._base_url + path
        headers = self._base_headers()
        request_kwargs: dict[str, Any] = {"headers": headers}
        if query:
            request_kwargs["params"] = query
        if json_body is not None:
            request_kwargs["json"] = json_body
        if files is not None:
            request_kwargs["files"] = files

        logger.debug("RAGFlow 请求: %s %s", method, url)
        attempts = self._max_retries + 1
        response: httpx.Response | None = None
        for attempt in range(attempts):
            try:
                response = await self._client.request(method, url, **request_kwargs)
            except httpx.TransportError as exc:
                if attempt + 1 >= attempts:
                    raise RagflowUnavailableError(f"RAGFlow 请求失败: {exc}") from exc
                logger.warning(
                    "RAGFlow 传输错误，准备重试(%s/%s): %s",
                    attempt + 1,
                    self._max_retries,
                    exc,
                )
                await self._backoff(attempt)
                continue

            if response.status_code in (401, 403):
                raise RagflowAuthenticationError(
                    f"RAGFlow 认证失败(HTTP {response.status_code}): {self._error_detail(response)}"
                )
            if response.status_code == 404:
                raise RagflowNotFoundError(
                    f"RAGFlow 资源不存在(HTTP 404): {path}"
                )
            if response.status_code == 429:
                if attempt + 1 >= attempts:
                    raise RagflowRateLimitError(f"RAGFlow 限流(HTTP 429): {path}")
                await self._backoff(attempt)
                continue
            if response.status_code >= 500:
                if attempt + 1 >= attempts:
                    raise RagflowUnavailableError(
                        f"RAGFlow 服务错误(HTTP {response.status_code}): {path}"
                    )
                await self._backoff(attempt)
                continue
            if response.status_code >= 400:
                raise RagflowError(
                    f"RAGFlow 拒绝请求(HTTP {response.status_code}): "
                    f"{self._error_detail(response)}",
                    code="RAGFLOW_HTTP_ERROR",
                    http_status=response.status_code,
                )
            break

        assert response is not None
        if expect_binary:
            if self._is_json(response):
                # 错误信封会在解码时抛出对应异常。
                self._decode_envelope(response)
                raise RagflowContractError("RAGFlow 下载返回了 JSON 而非文件内容")
            return RagflowBinary(
                content=response.content,
                content_type=response.headers.get(
                    "content-type", "application/octet-stream"
                ),
                content_disposition=response.headers.get("content-disposition"),
            )
        return self._decode_envelope(response)

    async def _backoff(self, attempt: int) -> None:
        """指数退避 + 抖动。"""
        delay = min(_BACKOFF_MAX_SECONDS, _BACKOFF_BASE_SECONDS * (2**attempt))
        delay *= random.uniform(0.5, 1.5)
        await asyncio.sleep(delay)

    @staticmethod
    def _is_json(response: httpx.Response) -> bool:
        media_type = response.headers.get("content-type", "").split(";", 1)[0]
        media_type = media_type.strip().lower()
        return media_type == "application/json" or (
            media_type.startswith("application/") and media_type.endswith("+json")
        )

    def _decode_envelope(self, response: httpx.Response) -> Any:
        """解码 ``{"code": 0, "data": ...}`` 信封并转译业务错误。"""
        if not self._is_json(response):
            raise RagflowContractError("RAGFlow 返回了非 JSON 响应")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RagflowContractError("RAGFlow 返回了无法解析的 JSON") from exc
        if not isinstance(payload, dict):
            raise RagflowContractError("RAGFlow 响应信封必须是 JSON 对象")
        code = payload.get("code")
        if code is None:
            raise RagflowContractError("RAGFlow 响应缺少 code 字段")
        if code == _SUCCESS_CODE:
            return payload.get("data")

        message = str(payload.get("message") or "")
        lowered = message.lower()
        if code in _AUTH_ERROR_CODES or "authentication error" in lowered:
            raise RagflowAuthenticationError(
                message or "RAGFlow 认证失败：API Key 无效或无权限"
            )
        if any(marker in lowered for marker in _NOT_FOUND_MARKERS):
            raise RagflowNotFoundError(message or "RAGFlow 目标资源不存在")
        detail = f": {message}" if message else ""
        raise RagflowError(
            f"RAGFlow 业务失败(code={code}){detail}",
            http_status=response.status_code,
        )

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """提取 4xx 响应中的可读错误信息。"""
        try:
            payload = response.json()
        except ValueError:
            return (response.text or "")[:200]
        if isinstance(payload, dict) and payload.get("message"):
            return str(payload["message"])
        return (response.text or "")[:200]

    @staticmethod
    def _format_path(template: str, **params: str) -> str:
        """填充并 URL 编码路径参数。"""
        encoded = {key: quote(str(value), safe="") for key, value in params.items()}
        try:
            return template.format(**encoded)
        except KeyError as exc:
            raise ValueError(f"缺少路径参数: {exc.args[0]}") from exc

    @staticmethod
    def _clean_body(body: dict[str, Any]) -> dict[str, Any]:
        """剔除值为 None 的可选字段。"""
        return {key: value for key, value in body.items() if value is not None}

    @staticmethod
    def _clean_query(params: dict[str, Any]) -> dict[str, Any]:
        """剔除值为 None 的查询参数。"""
        return {key: value for key, value in params.items() if value is not None}

    @staticmethod
    def _require_ids(values: list[str], label: str) -> None:
        """ID 列表校验：必须非空且不含空串（防误删全部）。"""
        if not values or any(not value for value in values):
            raise ValueError(f"{label} 必须为非空 ID 列表")

    @staticmethod
    def _require_non_empty(value: str, label: str) -> None:
        if not value:
            raise ValueError(f"{label} 不能为空")

    # ─────────────────────────── 0 模型管理 ───────────────────────────

    async def add_model(
        self,
        *,
        model_factory: str,
        model_type: str,
        model_name: str,
        api_base: str,
        api_key: str | None = None,
        max_tokens: str | int | None = None,
        user_defined_model_name: str | None = None,
        x_custom_device: str | None = None,
    ) -> bool:
        """添加模型（文档 0.1：POST /api/v1/models）。

        Args:
            model_factory: 部署类型，'CangJie'（需 api_key）或 'Xinference'。
            model_type: 'chat' / 'embedding' / 'rerank'。
            model_name: 模型名称，如 "qwen2.5-72B-instruct-int4"。
            api_base: 模型访问路径。
            api_key: 仓颉服务模型密钥（Xinference 不需要）。
            max_tokens: chat 模型必传，控制最大 token 长度。
            user_defined_model_name: 用户自定义唯一名称，缺省同 model_name。
            x_custom_device: 仓颉特定显卡路由头，不涉及请忽略。
        """
        for field in (model_factory, model_type, model_name, api_base):
            if not field:
                raise ValueError(
                    "model_factory/model_type/model_name/api_base 均为必填"
                )
        if model_type == "chat" and max_tokens is None:
            raise ValueError("添加 chat 类型模型时必须传入 max_tokens")
        body = self._clean_body(
            {
                "model_factory": model_factory,
                "model_type": model_type,
                "model_name": model_name,
                "api_base": api_base,
                "api_key": api_key,
                "max_tokens": max_tokens,
                "user_defined_model_name": user_defined_model_name,
                "x_custom_device": x_custom_device,
            }
        )
        data = await self._request("POST", "/api/v1/models", json_body=body)
        return True if data is None else bool(data)

    async def delete_models(self, *, model_factory: str, model_name: str) -> bool:
        """删除模型（文档 0.2：DELETE /api/v1/models）。

        Args:
            model_factory: 部署类型，'CangJie' 或 'Xinference'。
            model_name: 需传 add_model 中 user_defined_model_name 对应的值。
        """
        self._require_non_empty(model_factory, "model_factory")
        self._require_non_empty(model_name, "model_name")
        data = await self._request(
            "DELETE",
            "/api/v1/models",
            json_body={"model_factory": model_factory, "model_name": model_name},
        )
        return True if data is None else bool(data)

    async def list_models(self) -> dict[str, Any]:
        """获取已添加的模型列表（文档 0.3：GET /api/v1/models）。"""
        data = await self._request("GET", "/api/v1/models")
        if not isinstance(data, dict):
            raise RagflowContractError("RAGFlow 模型列表格式不符")
        return data

    async def get_global_models(self) -> dict[str, Any]:
        """查看系统全局模型配置（文档 0.4：GET /api/v1/global_models）。"""
        data = await self._request("GET", "/api/v1/global_models")
        if not isinstance(data, dict):
            raise RagflowContractError("RAGFlow 全局模型配置格式不符")
        return data

    async def set_global_models(
        self,
        *,
        chat_model: str | None = None,
        embedding_model: str | None = None,
        rerank_model: str | None = None,
    ) -> bool:
        """配置系统全局模型（文档 0.5：PUT /api/v1/global_models）。

        模型名称格式为 "model_name"@"model_factory"，
        三个参数至少需要传入一个。
        """
        body = self._clean_body(
            {
                "chat_model": chat_model,
                "embedding_model": embedding_model,
                "rerank_model": rerank_model,
            }
        )
        if not body:
            raise ValueError(
                "chat_model/embedding_model/rerank_model 至少需要传入一个"
            )
        data = await self._request("PUT", "/api/v1/global_models", json_body=body)
        return True if data is None else bool(data)

    # ─────────────────────────── 1 知识库管理 ───────────────────────────

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
        query = self._clean_query(
            {
                "page": page,
                "page_size": page_size,
                "orderby": orderby,
                "desc": desc,
                "name": name,
                "id": dataset_id,
            }
        )
        data = await self._request("GET", "/api/v1/datasets", query=query)
        if isinstance(data, list):
            return {"datasets": data, "total": len(data)}
        if not isinstance(data, dict):
            raise RagflowContractError("RAGFlow 知识库列表格式不符")
        return data

    # ─────────────────────────── 2 知识库内的文档管理 ───────────────────────────

    async def upload_documents(
        self,
        dataset_id: str,
        files: list[tuple[str, bytes | BinaryIO, str]],
    ) -> list[dict[str, Any]]:
        """上传文档（文档 2.1：POST /api/v1/datasets/{dataset_id}/documents）。

        Args:
            dataset_id: 目标知识库 ID。
            files: (文件名, 文件内容, MIME 类型) 元组列表，支持多文件。

        Returns:
            文档对象列表（含 id/name/run 等字段）。
        """
        self._require_non_empty(dataset_id, "dataset_id")
        if not files:
            raise ValueError("files 不能为空")
        multipart = [
            ("file", (filename, content, content_type))
            for filename, content, content_type in files
        ]
        path = self._format_path(
            "/api/v1/datasets/{dataset_id}/documents", dataset_id=dataset_id
        )
        data = await self._request("POST", path, files=multipart)
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
        for field in (batch_id, file_part, object_name):
            if not field:
                raise ValueError("batch_id/file_part/object_name 为必填字段")
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
        """查询知识库文档列表（文档 2.5：GET /api/v1/datasets/{dataset_id}/documents）。

        Returns:
            ``{"docs": [...], "total": n}`` 形式的 data 字段，
            docs 内 run 字段标识解析状态（UNSTART/RUNNING/DONE/FAIL/CANCEL）。
        """
        self._require_non_empty(dataset_id, "dataset_id")
        query = self._clean_query(
            {
                "page": page,
                "page_size": page_size,
                "orderby": orderby,
                "desc": desc,
                "keywords": keywords,
                "id": document_id,
                "name": name,
            }
        )
        path = self._format_path(
            "/api/v1/datasets/{dataset_id}/documents", dataset_id=dataset_id
        )
        data = await self._request("GET", path, query=query)
        if isinstance(data, list):
            return {"docs": data, "total": len(data)}
        if not isinstance(data, dict):
            raise RagflowContractError("RAGFlow 文档列表格式不符")
        return data

    async def delete_documents(
        self, dataset_id: str, document_ids: list[str]
    ) -> None:
        """删除文档（文档 2.6：DELETE /api/v1/datasets/{dataset_id}/documents）。"""
        self._require_non_empty(dataset_id, "dataset_id")
        self._require_ids(document_ids, "document_ids")
        path = self._format_path(
            "/api/v1/datasets/{dataset_id}/documents", dataset_id=dataset_id
        )
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

    # ─────────────────────────── 3 chunk 管理与检索 ───────────────────────────

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
        if not isinstance(data, dict):
            raise RagflowContractError("RAGFlow 添加 chunk 未返回对象")
        return data

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
        if not isinstance(data, dict):
            raise RagflowContractError("RAGFlow chunk 列表格式不符")
        return data

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
        if not isinstance(data, dict):
            raise RagflowContractError("RAGFlow 检索结果格式不符")
        return data

    # ─────────────────────────── 4 聊天助手管理 ───────────────────────────

    async def create_chat_assistant(
        self,
        *,
        name: str,
        dataset_ids: list[str] | None = None,
        llm: dict[str, Any] | None = None,
        prompt: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """创建聊天助手（文档 4.1：POST /api/v1/chats）。

        Args:
            name: 助手名称（必填）。
            dataset_ids: 关联知识库 ID 列表。
            llm: LLM 设置（model_name/temperature/top_p 等），缺省用默认值。
            prompt: 提示词设置（similarity_threshold/top_n/variables 等）。
        """
        self._require_non_empty(name, "name")
        body = self._clean_body(
            {
                "name": name,
                "dataset_ids": dataset_ids,
                "llm": llm,
                "prompt": prompt,
            }
        )
        data = await self._request("POST", "/api/v1/chats", json_body=body)
        if not isinstance(data, dict) or not data.get("id"):
            raise RagflowContractError("RAGFlow 创建聊天助手未返回有效对象")
        return data

    async def update_chat_assistant(
        self,
        chat_id: str,
        *,
        name: str | None = None,
        dataset_ids: list[str] | None = None,
        llm: dict[str, Any] | None = None,
        prompt: dict[str, Any] | None = None,
    ) -> None:
        """更新聊天助手配置（文档 4.2：PUT /api/v1/chats/{chat_id}）。"""
        self._require_non_empty(chat_id, "chat_id")
        body = self._clean_body(
            {
                "name": name,
                "dataset_ids": dataset_ids,
                "llm": llm,
                "prompt": prompt,
            }
        )
        if not body:
            raise ValueError("至少需要传入一个更新字段")
        path = self._format_path("/api/v1/chats/{chat_id}", chat_id=chat_id)
        await self._request("PUT", path, json_body=body)

    async def delete_chat_assistants(self, chat_ids: list[str]) -> None:
        """删除聊天助手（文档 4.3：DELETE /api/v1/chats，body 传 ids）。"""
        self._require_ids(chat_ids, "chat_ids")
        await self._request("DELETE", "/api/v1/chats", json_body={"ids": chat_ids})

    async def list_chat_assistants(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
        orderby: str | None = None,
        desc: bool | None = None,
        name: str | None = None,
        chat_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """查询聊天助手列表（文档 4.4：GET /api/v1/chats）。"""
        query = self._clean_query(
            {
                "page": page,
                "page_size": page_size,
                "orderby": orderby,
                "desc": desc,
                "name": name,
                "id": chat_id,
            }
        )
        data = await self._request("GET", "/api/v1/chats", query=query)
        if not isinstance(data, list):
            raise RagflowContractError("RAGFlow 聊天助手列表格式不符")
        return data

    # ─────────────────────────── 5 会话管理 ───────────────────────────

    async def create_session(
        self,
        chat_id: str,
        *,
        name: str,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """创建聊天助手会话（文档 5.1：POST /api/v1/chats/{chat_id}/sessions）。"""
        self._require_non_empty(chat_id, "chat_id")
        self._require_non_empty(name, "name")
        body = self._clean_body({"name": name, "user_id": user_id})
        path = self._format_path(
            "/api/v1/chats/{chat_id}/sessions", chat_id=chat_id
        )
        data = await self._request("POST", path, json_body=body)
        if not isinstance(data, dict) or not data.get("id"):
            raise RagflowContractError("RAGFlow 创建会话未返回有效对象")
        return data

    async def update_session(
        self,
        chat_id: str,
        session_id: str,
        *,
        name: str,
        user_id: str | None = None,
    ) -> None:
        """更新会话名称（文档 5.2：PUT /api/v1/chats/{chat_id}/sessions/{session_id}）。"""
        self._require_non_empty(chat_id, "chat_id")
        self._require_non_empty(session_id, "session_id")
        self._require_non_empty(name, "name")
        body = self._clean_body({"name": name, "user_id": user_id})
        path = self._format_path(
            "/api/v1/chats/{chat_id}/sessions/{session_id}",
            chat_id=chat_id,
            session_id=session_id,
        )
        await self._request("PUT", path, json_body=body)

    async def list_sessions(
        self,
        chat_id: str,
        *,
        page: int | None = None,
        page_size: int | None = None,
        orderby: str | None = None,
        desc: bool | None = None,
        name: str | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """查询会话列表（文档 5.3：GET /api/v1/chats/{chat_id}/sessions）。"""
        self._require_non_empty(chat_id, "chat_id")
        query = self._clean_query(
            {
                "page": page,
                "page_size": page_size,
                "orderby": orderby,
                "desc": desc,
                "name": name,
                "id": session_id,
                "user_id": user_id,
            }
        )
        path = self._format_path(
            "/api/v1/chats/{chat_id}/sessions", chat_id=chat_id
        )
        data = await self._request("GET", path, query=query)
        if not isinstance(data, list):
            raise RagflowContractError("RAGFlow 会话列表格式不符")
        return data

    async def delete_sessions(
        self, chat_id: str, session_ids: list[str]
    ) -> None:
        """删除会话（文档 5.4：DELETE /api/v1/chats/{chat_id}/sessions，body 传 ids）。"""
        self._require_non_empty(chat_id, "chat_id")
        self._require_ids(session_ids, "session_ids")
        path = self._format_path(
            "/api/v1/chats/{chat_id}/sessions", chat_id=chat_id
        )
        await self._request("DELETE", path, json_body={"ids": session_ids})

    async def ask_chat(
        self,
        chat_id: str,
        *,
        question: str,
        stream: bool = False,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """与聊天助手对话（文档 5.5：POST /api/v1/chats/{chat_id}/completions）。

        仅支持非流式模式（stream=False）；未提供 session_id 时服务端会
        自动创建新会话。

        Returns:
            ``{"answer": ..., "reference": ..., "session_id": ...}``。
        """
        self._require_non_empty(chat_id, "chat_id")
        self._require_non_empty(question, "question")
        if stream:
            raise ValueError(
                "ask_chat 不支持流式响应(stream=True)，请保持非流式调用"
            )
        body = self._clean_body(
            {
                "question": question,
                "stream": False,
                "session_id": session_id,
                "user_id": user_id,
            }
        )
        path = self._format_path(
            "/api/v1/chats/{chat_id}/completions", chat_id=chat_id
        )
        data = await self._request("POST", path, json_body=body)
        if not isinstance(data, dict):
            raise RagflowContractError("RAGFlow 对话响应格式不符")
        return data

    # ─────────────────────────── 6 团队管理 ───────────────────────────

    async def join_team(self, *, email: str) -> dict[str, Any]:
        """邀请用户加入个人团队（文档 6.1：POST /api/v1/team）。

        被邀请用户可查看个人账号下权限为 team 的共享知识库。
        """
        self._require_non_empty(email, "email")
        data = await self._request(
            "POST", "/api/v1/team", json_body={"email": email}
        )
        if not isinstance(data, dict):
            raise RagflowContractError("RAGFlow 加入团队响应格式不符")
        return data

    # ─────────────────────────── 健康检查 ───────────────────────────

    async def health(self) -> bool:
        """以最小列表请求探测连通性与认证有效性。"""
        await self.list_datasets(page=1, page_size=1)
        return True


__all__ = ["RagflowBinary", "RagflowClient"]
