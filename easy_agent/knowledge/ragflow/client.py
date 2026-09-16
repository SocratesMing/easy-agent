"""Raw async client for the pinned/configured RAGFlow contract."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Any, BinaryIO
from urllib.parse import quote

import httpx

from ..config import (
    AuthMode,
    KnowledgeConfig,
    RequestEncoding,
    ResponseContract,
)
from .errors import (
    RagflowAuthenticationError,
    RagflowContractError,
    RagflowError,
    RagflowUnavailableError,
)
from .bank_contract import bank_request_headers
from .local_v017_bridge import build_local_v017_bridge


@dataclass(frozen=True, slots=True)
class RagflowBinary:
    content: bytes
    content_type: str
    content_disposition: str | None = None


class RagflowClient:
    """Execute configured operations without exposing supplier DTOs to the API."""

    def __init__(
        self,
        config: KnowledgeConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not config.enabled or config.endpoint is None:
            raise ValueError("RAGFlow client requires an enabled knowledge configuration")
        self.config = config
        self._root = (
            config.endpoint.base_url.rstrip("/") + config.endpoint.api_prefix
        )
        timeout = config.transport.timeout_seconds
        verify: bool | str = config.tls.ca_bundle or config.tls.verify
        cert: tuple[str, str] | None = None
        if config.tls.client_cert and config.tls.client_key:
            cert = (config.tls.client_cert, config.tls.client_key)
        limits = httpx.Limits(
            max_connections=config.transport.pool.max_connections,
            max_keepalive_connections=config.transport.pool.max_keepalive_connections,
            keepalive_expiry=config.transport.pool.keepalive_expiry_seconds,
        )
        self._client = httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(
                connect=timeout.connect,
                read=timeout.read,
                write=timeout.write,
                pool=timeout.pool,
            ),
            limits=limits,
            verify=verify,
            cert=cert,
            trust_env=config.transport.trust_env,
            follow_redirects=config.transport.follow_redirects,
            proxy=config.transport.proxy or None,
        )
        self._local_bridge = build_local_v017_bridge(
            config.adapter.local_v017_bridge
        )

    async def __aenter__(self) -> "RagflowClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    def _headers(
        self,
        *,
        operation_name: str,
        request_id: str | None,
        tenant_id: str | None,
        organization_id: str | None,
    ) -> dict[str, str]:
        auth = self.config.auth
        headers = dict(auth.fixed_headers)
        credential = auth.credential.get_secret_value()
        if auth.mode == AuthMode.BEARER:
            headers[auth.header_name] = f"{auth.scheme} {credential}".strip()
        elif auth.mode == AuthMode.API_KEY_HEADER:
            headers[auth.header_name] = credential

        principal_headers = auth.principal_headers
        if principal_headers.request_id and request_id:
            headers[principal_headers.request_id] = request_id
        if principal_headers.tenant_id and tenant_id:
            headers[principal_headers.tenant_id] = tenant_id
        if principal_headers.organization_id and organization_id:
            headers[principal_headers.organization_id] = organization_id
        headers.update(bank_request_headers(self.config, operation_name))
        if self._local_bridge is not None:
            headers = self._local_bridge.headers(headers)
        return headers

    @staticmethod
    def _format_path(template: str, values: dict[str, object]) -> str:
        encoded = {key: quote(str(value), safe="") for key, value in values.items()}
        try:
            return template.format_map(encoded)
        except KeyError as exc:
            raise ValueError(f"missing operation path parameter: {exc.args[0]}") from exc

    @staticmethod
    def _json_media_type(response: httpx.Response) -> bool:
        media_type = response.headers.get("content-type", "").split(";", 1)[0]
        media_type = media_type.strip().lower()
        return media_type == "application/json" or (
            media_type.startswith("application/") and media_type.endswith("+json")
        )

    def _validate_contract(
        self, contract: ResponseContract, data: object, *, has_data: bool
    ) -> None:
        if contract == ResponseContract.DATASET:
            valid = isinstance(data, dict) and bool(data.get("id"))
        elif contract == ResponseContract.DATASET_LIST:
            valid = isinstance(data, list) or (
                isinstance(data, dict) and isinstance(data.get("datasets"), list)
            )
        elif contract == ResponseContract.DOCUMENT_LIST:
            valid = isinstance(data, list) or (
                isinstance(data, dict) and isinstance(data.get("docs"), list)
            )
        elif contract == ResponseContract.EMPTY_OR_BOOLEAN:
            valid = not has_data or data is None or data is True or isinstance(data, dict)
        elif contract == ResponseContract.RETRIEVAL_RESULT:
            valid = isinstance(data, dict) and isinstance(data.get("chunks"), list)
        else:
            valid = True
        if not valid:
            raise RagflowContractError()

    def _decode_json(
        self, response: httpx.Response, contract: ResponseContract
    ) -> object:
        if not self._json_media_type(response):
            raise RagflowContractError("RAGFlow returned a non-JSON response")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RagflowContractError("RAGFlow returned malformed JSON") from exc
        if not isinstance(payload, dict):
            raise RagflowContractError("RAGFlow response envelope must be an object")

        envelope = self.config.response.envelope
        code = payload.get(envelope.code_field)
        message = str(payload.get(envelope.message_field) or "")
        has_data = envelope.data_field in payload
        data = payload.get(envelope.data_field)
        if code in envelope.authentication_codes:
            raise RagflowAuthenticationError()
        if code not in envelope.success_codes:
            mapped = self.config.response.error_code_map.get(str(code), "RAGFLOW_ERROR")
            raise RagflowError(
                "RAGFlow rejected the operation",
                code=mapped,
                retryable=False,
                http_status=response.status_code,
            )
        if (
            envelope.data_false_with_message_is_error
            and data is False
            and message
        ):
            if "auth" in message.lower() or "token" in message.lower():
                raise RagflowAuthenticationError()
            raise RagflowError("RAGFlow rejected the operation", code="RAGFLOW_ERROR")

        if self.config.response.require_operation_contract_match:
            self._validate_contract(contract, data, has_data=has_data)
        return data

    async def _request(
        self,
        operation_name: str,
        *,
        path_params: dict[str, object] | None = None,
        query: dict[str, object] | None = None,
        json_body: dict[str, object] | None = None,
        files: dict[str, tuple[str, BinaryIO | bytes, str]] | None = None,
        request_id: str | None = None,
        tenant_id: str | None = None,
        organization_id: str | None = None,
    ) -> object:
        endpoint = self.config.endpoint
        assert endpoint is not None
        operation = endpoint.operations[operation_name]
        path = self._format_path(operation.path, path_params or {})
        operation_root = operation.base_url.rstrip("/") if operation.base_url else self._root
        url = operation_root + path
        if self._local_bridge is not None:
            url = self._local_bridge.url(operation_name, path)

        request_query = dict(
            self.config.compatibility.fixed_query_by_operation.get(
                operation_name, {}
            )
        )
        request_query.update(query or {})
        request_body = dict(
            self.config.compatibility.fixed_body_by_operation.get(
                operation_name, {}
            )
        )
        request_body.update(json_body or {})
        if self._local_bridge is not None:
            request_body = self._local_bridge.json_body(
                operation_name, request_body
            )

        kwargs: dict[str, object] = {
            "headers": self._headers(
                operation_name=operation_name,
                request_id=request_id,
                tenant_id=tenant_id,
                organization_id=organization_id,
            )
        }
        if operation.request_encoding == RequestEncoding.QUERY:
            kwargs["params"] = request_query
        elif operation.request_encoding == RequestEncoding.JSON:
            kwargs["json"] = request_body
        elif operation.request_encoding == RequestEncoding.MULTIPART:
            kwargs["files"] = files or {}

        retry = self.config.transport.retry
        attempts = retry.attempts if operation_name in retry.operations else 1
        response: httpx.Response | None = None
        for attempt in range(attempts):
            try:
                response = await self._client.request(
                    operation.method.value, url, **kwargs
                )
            except httpx.TransportError as exc:
                if attempt + 1 >= attempts:
                    raise RagflowUnavailableError() from exc
                await self._backoff(attempt)
                continue

            if response.status_code in {401, 403}:
                raise RagflowAuthenticationError()
            if (
                response.status_code in retry.retry_statuses
                and attempt + 1 < attempts
            ):
                await self._backoff(attempt)
                continue
            if response.status_code >= 500:
                raise RagflowUnavailableError()
            if response.status_code >= 400:
                raise RagflowError(
                    "RAGFlow rejected the operation",
                    code="RAGFLOW_HTTP_ERROR",
                    http_status=response.status_code,
                )
            break

        assert response is not None
        if operation.response_contract == ResponseContract.BINARY:
            if self._json_media_type(response):
                self._decode_json(response, ResponseContract.EMPTY_OR_BOOLEAN)
                raise RagflowContractError(
                    "RAGFlow download returned JSON instead of file content"
                )
            return RagflowBinary(
                content=response.content,
                content_type=response.headers.get(
                    "content-type", "application/octet-stream"
                ),
                content_disposition=response.headers.get("content-disposition"),
            )
        return self._decode_json(response, operation.response_contract)

    async def _backoff(self, attempt: int) -> None:
        retry = self.config.transport.retry
        delay = min(
            retry.max_backoff_seconds,
            retry.base_backoff_seconds * (2**attempt),
        )
        if retry.jitter:
            delay *= random.uniform(0.5, 1.5)
        await asyncio.sleep(delay)

    async def health(self, *, request_id: str | None = None) -> bool:
        await self.list_datasets(page=1, page_size=1, request_id=request_id)
        return True

    async def create_dataset(
        self,
        *,
        name: str,
        description: str = "",
        request_id: str | None = None,
    ) -> dict[str, Any]:
        defaults = self.config.defaults
        profile = defaults.parsing.profiles.get(defaults.parsing.default_profile)
        body: dict[str, object] = {
            "name": name,
            "description": description,
            "permission": defaults.dataset.permission.value,
            "embedding_model": defaults.dataset.embedding_model,
        }
        if profile is not None:
            body.update(
                {
                    "chunk_method": profile.chunk_method,
                    "parser_config": profile.parser_config,
                }
            )
        data = await self._request(
            "create_dataset", json_body=body, request_id=request_id
        )
        assert isinstance(data, dict)
        return data

    async def list_datasets(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        request_id: str | None = None,
    ) -> list[dict[str, Any]]:
        data = await self._request(
            "list_datasets",
            query={"page": page, "page_size": page_size},
            request_id=request_id,
        )
        if isinstance(data, dict):
            datasets = data.get("datasets")
            assert isinstance(datasets, list)
            return datasets
        assert isinstance(data, list)
        return data

    async def update_dataset(
        self, dataset_id: str, values: dict[str, object], *, request_id: str | None = None
    ) -> None:
        await self._request(
            "update_dataset",
            path_params={"dataset_id": dataset_id},
            json_body=values,
            request_id=request_id,
        )

    async def delete_datasets(
        self, dataset_ids: list[str], *, request_id: str | None = None
    ) -> None:
        if not dataset_ids or any(not value for value in dataset_ids):
            raise ValueError("dataset_ids must be non-empty")
        await self._request(
            "delete_datasets",
            json_body={"ids": dataset_ids},
            request_id=request_id,
        )

    async def upload_document(
        self,
        *,
        dataset_id: str,
        filename: str,
        content: BinaryIO | bytes,
        content_type: str,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        data = await self._request(
            "upload_documents",
            path_params={"dataset_id": dataset_id},
            files={"file": (filename, content, content_type)},
            request_id=request_id,
        )
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise RagflowContractError("RAGFlow upload did not return a document")
        return data[0]

    async def list_documents(
        self,
        *,
        dataset_id: str,
        page: int = 1,
        page_size: int = 50,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        data = await self._request(
            "list_documents",
            path_params={"dataset_id": dataset_id},
            query={"page": page, "page_size": page_size},
            request_id=request_id,
        )
        if isinstance(data, list):
            return {"docs": data, "total": len(data)}
        assert isinstance(data, dict)
        return data

    async def configure_document(
        self,
        *,
        dataset_id: str,
        document_id: str,
        filename: str,
        request_id: str | None = None,
    ) -> None:
        if (
            self._local_bridge is not None
            and self._local_bridge.should_skip_document_configuration(filename)
        ):
            return
        extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        parsing = self.config.defaults.parsing
        profile_name = parsing.default_profile
        for rule in parsing.routing_rules:
            if extension in {item.lower() for item in rule.extensions}:
                profile_name = rule.profile
                break
        profile = parsing.profiles[profile_name]
        await self._request(
            "update_document",
            path_params={"dataset_id": dataset_id, "document_id": document_id},
            json_body={
                "chunk_method": profile.chunk_method,
                "parser_config": profile.parser_config,
            },
            request_id=request_id,
        )

    async def download_document(
        self,
        *,
        dataset_id: str,
        document_id: str,
        request_id: str | None = None,
    ) -> RagflowBinary:
        data = await self._request(
            "download_document",
            path_params={"dataset_id": dataset_id, "document_id": document_id},
            request_id=request_id,
        )
        assert isinstance(data, RagflowBinary)
        return data

    async def delete_documents(
        self,
        *,
        dataset_id: str,
        document_ids: list[str],
        request_id: str | None = None,
    ) -> None:
        if not document_ids or any(not value for value in document_ids):
            raise ValueError("document_ids must be non-empty")
        await self._request(
            "delete_documents",
            path_params={"dataset_id": dataset_id},
            json_body={"ids": document_ids},
            request_id=request_id,
        )

    async def start_parsing(
        self,
        *,
        dataset_id: str,
        document_ids: list[str],
        request_id: str | None = None,
    ) -> None:
        if not document_ids or any(not value for value in document_ids):
            raise ValueError("document_ids must be non-empty")
        field = self.config.compatibility.request_field_aliases.parse_document_ids
        await self._request(
            "start_parsing",
            path_params={"dataset_id": dataset_id},
            json_body={field: document_ids},
            request_id=request_id,
        )

    async def retrieve(
        self,
        *,
        question: str,
        dataset_ids: list[str],
        document_ids: list[str],
        top_n: int,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if not dataset_ids or any(not value for value in dataset_ids):
            raise ValueError("dataset_ids must be non-empty")
        if not document_ids or any(not value for value in document_ids):
            raise ValueError("document_ids must be non-empty")
        aliases = self.config.compatibility.request_field_aliases
        retrieval = self.config.defaults.retrieval
        body: dict[str, object] = {
            "question": question,
            aliases.retrieval_dataset_ids: dataset_ids,
            aliases.retrieval_document_ids: document_ids,
            "page": 1,
            "page_size": top_n,
            "similarity_threshold": retrieval.similarity_threshold,
            "vector_similarity_weight": retrieval.vector_similarity_weight,
            "top_k": retrieval.top_k,
        }
        if retrieval.rerank_id:
            body["rerank_id"] = retrieval.rerank_id
        data = await self._request(
            "retrieve", json_body=body, request_id=request_id
        )
        assert isinstance(data, dict)
        return data


__all__ = ["RagflowBinary", "RagflowClient"]
