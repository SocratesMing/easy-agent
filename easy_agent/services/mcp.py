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
# Config loading
# ---------------------------------------------------------------------------


def _find_mcp_config() -> Path | None:
    """Find mcp.json in the same search path as config.yaml."""
    return Config.find_config_file("mcp.json")


def load_mcp_config() -> dict[str, dict[str, Any]]:
    """Load MCP server configuration from mcp.json.

    Returns a dict compatible with MultiServerMCPClient, where keys are
    server names and values are connection parameter dicts.

    Supports both object and array formats for the "servers" field,
    and normalizes "type" -> "transport".
    """
    config_path = _find_mcp_config()
    if not config_path:
        logger.info("No mcp.json found, MCP tools disabled")
        return {}

    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)

        servers = data.get("servers", {})
        if not servers:
            logger.info("mcp.json has no server entries, MCP tools disabled")
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
            f"MCP config loaded: {len(result)} server(s) — {', '.join(result.keys())}"
        )
        return result

    except Exception as e:
        logger.warning(f"Failed to load mcp.json: {e}")
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_mcp_tools() -> list:
    """Get all MCP tools as LangChain tools via langchain-mcp-adapters.

    Creates a MultiServerMCPClient from mcp.json config and loads all
    available tools. Returns a list of LangChain BaseTool instances that
    can be passed directly to create_agent / create_deep_agent.

    Note: MultiServerMCPClient is stateless by default — each tool
    invocation creates a fresh MCP session and cleans up afterwards.
    """
    config = load_mcp_config()
    if not config:
        return []

    try:
        client = MultiServerMCPClient(config)
        tools = await client.get_tools()
        logger.info(f"MCP tools loaded: {len(tools)} tool(s)")
        for t in tools:
            logger.info(f"  └─ {t.name}")
        return tools
    except Exception as e:
        logger.warning(f"Failed to load MCP tools: {e}")
        return []
