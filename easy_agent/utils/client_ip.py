"""Extract the client IP without blindly trusting forwarded headers.

Ingress / kube-proxy 场景下 ``X-Forwarded-For`` 可被客户端伪造。
仅当显式设置 ``EASY_TRUST_PROXY=1``（并由 Ingress 覆盖该头）时才读取转发头。
"""

from __future__ import annotations

import os
from ..env import env_bool

from fastapi import Request


def trust_proxy_headers() -> bool:
    return env_bool("EASY_TRUST_PROXY")


def get_client_ip(request: Request) -> str:
    if trust_proxy_headers():
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
    return request.client.host if request.client else "unknown"
