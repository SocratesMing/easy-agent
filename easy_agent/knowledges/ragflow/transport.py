"""RAG 平台客户端传输层：连接生命周期、重试、信封解码与通用辅助。"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any, BinaryIO, Self
from urllib.parse import quote

import httpx

from easy_agent.knowledges.ragflow.errors import (
    RagflowAuthenticationError,
    RagflowContractError,
    RagflowError,
    RagflowNotFoundError,
    RagflowRateLimitError,
    RagflowUnavailableError,
)

logger = logging.getLogger(__name__)

# 响应信封约定：RAG 平台统一信封成功码为 200（《接入文档 v1.1.1》统一响应
# 编码），Ragflow 原生接口成功码为 0；两者均按成功解码。
_SUCCESS_CODES = frozenset({0, 200})
_AUTH_ERROR_CODES = frozenset({109})
# 平台统一响应编码（文档"统一响应编码"章节）→ 稳定异常类型。
_PLATFORM_ERROR_EXC: dict[int, type[RagflowError]] = {
    401: RagflowAuthenticationError,
    404: RagflowNotFoundError,
    429: RagflowRateLimitError,
    500: RagflowUnavailableError,
    503: RagflowUnavailableError,
}
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
    "do not belong",
    "does not belong",
    "not belong",
)

# 重试退避参数（对传输错误、HTTP 429 与 5xx 生效）。
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 8.0


@dataclass(frozen=True, slots=True)
class RagflowBinary:
    """下载接口返回的二进制内容。"""

    content: bytes
    content_type: str
    content_disposition: str | None = None


class RagflowTransportMixin:
    """HTTP 传输基础设施：持有连接池并封装请求、重试、解码与参数校验。"""

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

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        """关闭底层 HTTP 连接池。"""
        await self._client.aclose()

    # ─────────────────────────── 请求基础设施 ───────────────────────────

    def _trace_serial(self) -> str:
        """生成 stdsysserialnum 全局链路追踪流水号。

        规则（《接入文档 v1.1.1》RI001 请求说明）：
        ``<令牌>+yyyyMMddHHmmss+六位随机数``，令牌即 Bearer 认证凭据。
        """
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        suffix = f"{random.randint(0, 999999):06d}"
        return f"{self._api_key}{stamp}{suffix}"

    def _base_headers(self) -> dict[str, str]:
        """所有请求共用头：Bearer 认证 + stdsysserialnum 链路追踪流水号。"""
        return {
            "Authorization": f"Bearer {self._api_key}",
            "stdsysserialnum": self._trace_serial(),
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        form: dict[str, Any] | None = None,
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
        if form is not None:
            # 与 files 同时提供时，httpx 自动合并为 multipart/form-data 字段。
            request_kwargs["data"] = form
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
        """按 Content-Type 判断响应是否 JSON（含 application/*+json）。"""

        media_type = response.headers.get("content-type", "").split(";", 1)[0]
        media_type = media_type.strip().lower()
        return media_type == "application/json" or (
            media_type.startswith("application/") and media_type.endswith("+json")
        )

    def _decode_envelope(self, response: httpx.Response) -> Any:
        """解码 ``{"code": <int>, "data": ...}`` 信封并转译业务错误。

        成功码兼容平台统一信封 ``code=200`` 与 Ragflow 原生 ``code=0``；
        失败时按文档"统一响应编码"映射稳定异常（message 格式 ``{说明}:{msg}``）。
        """
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
        if code in _SUCCESS_CODES:
            return payload.get("data")

        message = str(payload.get("message") or "")
        lowered = message.lower()
        if code in _AUTH_ERROR_CODES or "authentication error" in lowered:
            raise RagflowAuthenticationError(
                message or "RAGFlow 认证失败：API Key 无效或无权限"
            )
        if any(marker in lowered for marker in _NOT_FOUND_MARKERS):
            raise RagflowNotFoundError(message or "RAGFlow 目标资源不存在")
        platform_exc = _PLATFORM_ERROR_EXC.get(code)
        if platform_exc is not None:
            raise platform_exc(message or f"RAG 平台业务失败(code={code})")
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

    # 查询参数与 body 的清理语义完全相同，保留独立名称以兼容既有调用点。
    _clean_query = _clean_body

    @staticmethod
    def _documents_path(dataset_id: str) -> str:
        """知识库文档集合路径。"""
        return RagflowTransportMixin._format_path(
            "/api/v1/datasets/{dataset_id}/documents", dataset_id=dataset_id
        )

    @staticmethod
    def _page_query(
        page: int | None,
        page_size: int | None,
        *,
        orderby: str | None = None,
        desc: bool | None = None,
        name: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """构造分页查询参数（剔除值为 None 的项）。"""
        query: dict[str, Any] = {
            "page": page,
            "page_size": page_size,
            "orderby": orderby,
            "desc": desc,
            "name": name,
        }
        if extra:
            query.update(extra)
        return RagflowTransportMixin._clean_body(query)

    @staticmethod
    def _require_all(values: tuple[str, ...], message: str) -> None:
        """必填字段校验：任一为空即抛出 ValueError。"""
        if any(not value for value in values):
            raise ValueError(message)

    @staticmethod
    def _expect_dict(data: Any, message: str) -> dict[str, Any]:
        """断言响应 data 为对象，否则抛出契约异常。"""
        if not isinstance(data, dict):
            raise RagflowContractError(message)
        return data

    @staticmethod
    def _expect_list(data: Any, message: str) -> list[dict[str, Any]]:
        """断言响应 data 为数组，否则抛出契约异常。"""
        if not isinstance(data, list):
            raise RagflowContractError(message)
        return data

    @staticmethod
    def _require_ids(values: list[str], label: str) -> None:
        """ID 列表校验：必须非空且不含空串（防误删全部）。"""
        if not values or any(not value for value in values):
            raise ValueError(f"{label} 必须为非空 ID 列表")

    @staticmethod
    def _require_non_empty(value: str, label: str) -> None:
        if not value:
            raise ValueError(f"{label} 不能为空")
