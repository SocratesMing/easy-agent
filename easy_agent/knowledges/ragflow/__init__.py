"""Ragflow v0.26.3 本地服务标准 HTTP API 客户端（新版 knowledges 模块）。

所有请求携带 ``Authorization: Bearer {api_key}``，走标准 /api/v1/* 路径；
base_url 与 api_key 由服务层从配置注入。
"""

from .client import RagflowBinary, RagflowClient
from .errors import (
    RagflowAuthenticationError,
    RagflowContractError,
    RagflowError,
    RagflowNotFoundError,
    RagflowRateLimitError,
    RagflowUnavailableError,
)

__all__ = [
    "RagflowAuthenticationError",
    "RagflowBinary",
    "RagflowClient",
    "RagflowContractError",
    "RagflowError",
    "RagflowNotFoundError",
    "RagflowRateLimitError",
    "RagflowUnavailableError",
]
