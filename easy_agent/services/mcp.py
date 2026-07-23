"""MCP (Model Context Protocol) tool integration service.

Uses langchain-mcp-adapters to connect to MCP servers and convert
their tools into LangChain BaseTool instances for the agent.
"""

import json
import logging
from pathlib import Path
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from easy_agent.config import Config

logger = logging.getLogger("easy-agent")


# ---------------------------------------------------------------------------
# Content parsing
# ---------------------------------------------------------------------------


def _parse_mcp_content(content) -> str:
    """Parse MCP-style ToolMessage content and extract plain text.

    MCP tools return content as a list of blocks like:
        [{"type": "text", "text": "..."}]
    or Python repr:
        [{'type': 'text', 'text': '...'}]
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif hasattr(item, "text"):
                parts.append(str(item.text))
        if parts:
            return "\n\n".join(parts)
    return str(content)


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def _find_mcp_config() -> Path | None:
    """Find the global mcp.json in the same search path as config.yaml."""
    return Config.find_config_file("mcp.json")


def _resolve_mcp_config_path(username: str | None = None) -> Path | None:
    """Resolve which mcp.json to load.

    Per-user override takes precedence: when ``username`` is given and a user
    mcp.json exists at ``{workspace_dir}/{username}/mcp.json``, it is used.
    Otherwise fall back to the global mcp.json (may be None if not found).
    """
    if username:
        user_path = Config.get_user_mcp_path(username)
        if user_path.exists():
            logger.info(f"MCP config source: user file ({user_path})")
            return user_path
        logger.info(
            f"MCP config source: global (no user file at {user_path})"
        )
    return _find_mcp_config()


def load_mcp_config(username: str | None = None) -> dict[str, dict[str, Any]]:
    """Load MCP server configuration from mcp.json.

    Args:
        username: When provided, a per-user mcp.json at
            ``{workspace_dir}/{username}/mcp.json`` takes precedence over the
            global file. When the user file is absent, the global file is used.

    Returns a dict compatible with MultiServerMCPClient, where keys are
    server names and values are connection parameter dicts.

    Supports both object and array formats for the "servers" field,
    and normalizes "type" -> "transport".
    """
    config_path = _resolve_mcp_config_path(username)
    if not config_path:
        logger.info(
            f"No mcp.json found (username={username!r}), MCP tools disabled"
        )
        return {}

    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)

        servers = data.get("servers", {})
        if not servers:
            logger.info(
                f"mcp.json ({config_path}) has no server entries, MCP tools disabled"
            )
            return {}

        result: dict[str, dict[str, Any]] = {}

        if isinstance(servers, dict):
            # Object format: {"mysql": {"type": "stdio", "command": ...}}
            for name, cfg in servers.items():
                normalized = dict(cfg)
                if "type" in normalized and "transport" not in normalized:
                    normalized["transport"] = normalized.pop("type")
                result[name] = normalized
        else:
            # Array format: [{"name": "example", "transport": "stdio", ...}]
            for s in servers:
                name = s.get("name", "")
                if not name:
                    continue
                normalized = {k: v for k, v in s.items() if k != "name"}
                if "type" in normalized and "transport" not in normalized:
                    normalized["transport"] = normalized.pop("type")
                result[name] = normalized

        logger.info(
            f"MCP config loaded from {config_path}: "
            f"{len(result)} server(s) — {', '.join(result.keys())}"
        )
        return result

    except Exception as e:
        logger.warning(f"Failed to load mcp.json ({config_path}): {e}")
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Per-path MCP tool cache: {resolved_path_str: (mtime, tools)}
_mcp_tools_cache: dict[str, tuple[float, list]] = {}


async def get_mcp_tools(username: str | None = None) -> list:
    """Get all MCP tools as LangChain tools via langchain-mcp-adapters.

    Args:
        username: When provided, loads the per-user mcp.json if it exists,
            otherwise the global file.

    Creates a MultiServerMCPClient from mcp.json config and loads all
    available tools. Returns a list of LangChain BaseTool instances that
    can be passed directly to create_agent / create_deep_agent.

    Tools are cached per resolved config file path + mtime, so editing the
    mcp.json and requesting tools again automatically picks up the change
    (dynamic reload). MultiServerMCPClient is stateless by default — each
    tool invocation creates a fresh MCP session and cleans up afterwards.
    """
    config_path = _resolve_mcp_config_path(username)
    if not config_path:
        return []

    path_key = str(config_path)
    try:
        mtime = config_path.stat().st_mtime
    except OSError:
        mtime = 0.0

    cached = _mcp_tools_cache.get(path_key)
    if cached and cached[0] == mtime:
        logger.info(
            f"MCP tools cache hit ({path_key}, mtime={mtime}): {len(cached[1])} tool(s)"
        )
        return cached[1]

    config = load_mcp_config(username)
    if not config:
        _mcp_tools_cache[path_key] = (mtime, [])
        return []

    try:
        client = MultiServerMCPClient(config)
        tools = await client.get_tools()
        logger.info(
            f"MCP tools loaded ({path_key}, mtime={mtime}): {len(tools)} tool(s)"
        )
        for t in tools:
            logger.info(f"  └─ {t.name}")
        _mcp_tools_cache[path_key] = (mtime, tools)
        return tools
    except Exception as e:
        logger.warning(f"Failed to load MCP tools ({path_key}): {e}")
        return []


def invalidate_mcp_cache(username: str | None = None) -> None:
    """Invalidate cached MCP tools.

    With ``username``, drops the cache entry for that user's mcp.json (and the
    global cache when falling back). Without ``username``, clears the entire
    cache. Call after writing a new mcp.json to force a reload on next use.
    """
    if username is None:
        _mcp_tools_cache.clear()
        logger.info("MCP tools cache cleared (all entries)")
        return

    user_path = Config.get_user_mcp_path(username)
    key = str(user_path)
    if key in _mcp_tools_cache:
        _mcp_tools_cache.pop(key, None)
        logger.info(f"MCP tools cache invalidated for user {username} ({key})")
    # Also drop global cache so a fallback reload is fresh.
    global_path = _find_mcp_config()
    if global_path:
        gkey = str(global_path)
        if gkey in _mcp_tools_cache:
            _mcp_tools_cache.pop(gkey, None)
            logger.info(f"MCP tools cache invalidated for global ({gkey})")


async def validate_mcp_servers(
    username: str | None = None,
) -> list[dict[str, Any]]:
    """逐个加载 MCP server 并返回每个 server 的状态。

    用于前端保存 MCP 配置后检测哪些 server 可用、哪些异常，
    异常的 server 前端会自动关闭开关并给出提示。

    Returns:
        [{"name": "xx", "status": "ok"|"error", "tools_count": N, "error": "..."}]
    """
    config = load_mcp_config(username)
    if not config:
        return []

    results: list[dict[str, Any]] = []
    for name, cfg in config.items():
        try:
            single_config = {name: cfg}
            client = MultiServerMCPClient(single_config)
            tools = await client.get_tools()
            results.append({
                "name": name,
                "status": "ok",
                "tools_count": len(tools),
                "error": "",
            })
            logger.info(f"MCP 校验 | {name} ✅ 成功 ({len(tools)} 工具)")
        except Exception as e:
            results.append({
                "name": name,
                "status": "error",
                "tools_count": 0,
                "error": str(e),
            })
            logger.warning(f"MCP 校验 | {name} ❌ 失败: {e}")
    return results
