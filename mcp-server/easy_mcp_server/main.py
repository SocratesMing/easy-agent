"""服务入口：`uv run uvicorn easy_mcp_server.main:app`"""

from __future__ import annotations

import os

from .app import create_app
from .env import load_env
from .keys import create_mysql_verifier

load_env()

_ttl_raw = os.environ.get("MCP_KEY_CACHE_TTL")
_verifier = create_mysql_verifier(float(_ttl_raw) if _ttl_raw else 60.0)

app = create_app(_verifier)
