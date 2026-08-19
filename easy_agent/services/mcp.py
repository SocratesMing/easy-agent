"""MCP (Model Context Protocol) tool integration service.

Uses langchain-mcp-adapters to connect to MCP servers and convert
their tools into LangChain BaseTool instances for the agent.
"""

import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from easy_agent.config import Config, _expand_env_recursive

logger = logging.getLogger("easy-agent")


def _unpack_error(e: BaseException) -> str:
    """把 ExceptionGroup/TaskGroup 的错误展开为最底层可读信息。"""
    if isinstance(e, BaseExceptionGroup):
        parts = []
        seen = set()
        for sub in e.exceptions:
            text = _unpack_error(sub)
            if text and text not in seen:
                seen.add(text)
                parts.append(text)
        return "; ".join(parts) if parts else str(e) or type(e).__name__
    return str(e) or type(e).__name__


def _stdio_precheck(cfg: dict[str, Any]) -> str:
    """stdio 服务启动前的快速检查，返回明确的中文提示（空字符串表示通过）。"""
    if str(cfg.get("transport", "")).lower() != "stdio":
        return ""
    command = cfg.get("command", "")
    if not command:
        return "stdio 服务缺少 command 字段"
    if shutil.which(command) is None:
        return f"命令不存在: {command}（请确认该命令在后端进程的 PATH 中，或填写绝对路径）"
    args = cfg.get("args") or []
    for i, a in enumerate(args):
        if a in ("--directory", "--cwd", "-C") and i + 1 < len(args):
            workdir = args[i + 1]
            if not os.path.isdir(workdir):
                return (
                    f"stdio 命令的工作目录不存在: {workdir}（请填写服务器实际所在目录的绝对路径）"
                )
    return ""


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
        data = _expand_env_recursive(data)

        servers = data.get("servers")
        if servers is None:
            # 兼容标准 Claude Desktop 格式 {"mcpServers": {"name": {...}}}
            servers = data.get("mcpServers", {})
        if not servers:
            logger.info(
                f"mcp.json ({config_path}) has no server entries, MCP tools disabled"
            )
            return {}

        result: dict[str, dict[str, Any]] = {}

        if isinstance(servers, dict):
            # Object format: {"mysql": {"type": "stdio", "command": ...}}
            result.update(normalize_servers_mapping(servers))
        else:
            # Array format: [{"name": "example", "transport": "stdio", ...}]
            items = {
                s.get("name", ""): {k: v for k, v in s.items() if k != "name"}
                for s in servers
                if s.get("name")
            }
            result.update(normalize_servers_mapping(items))

        logger.info(
            f"MCP config loaded from {config_path}: "
            f"{len(result)} server(s) — {', '.join(result.keys())}"
        )
        return result

    except Exception as e:
        logger.warning(f"Failed to load mcp.json ({config_path}): {e}")
        return {}


def normalize_servers_mapping(
    servers: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """归一化 servers 映射：type→transport，并展开误存的嵌套外壳。

    旧版解析把标准 Claude Desktop 格式 {"mcpServers": {...}} 误存成名为
    "mcpServers"/"servers" 的 server（值为 {name: cfg} 映射），这里递归展开，
    避免出现缺少 transport 的伪 server 导致校验失败。
    """
    result: dict[str, dict[str, Any]] = {}
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        # 值整体是 {name: cfg} 映射（嵌套外壳）时递归展开
        if name in ("mcpServers", "servers") and cfg and all(
            isinstance(v, dict) for v in cfg.values()
        ):
            result.update(normalize_servers_mapping(cfg))
            continue
        normalized = dict(cfg)
        if "type" in normalized and "transport" not in normalized:
            normalized["transport"] = normalized.pop("type")
        elif "transport" not in normalized:
            # 标准 Claude Desktop 格式不写 type：
            # 有 command/args 的是 stdio 服务，只有 url 的按 sse 处理
            if "command" in normalized:
                normalized["transport"] = "stdio"
            elif "url" in normalized:
                normalized["transport"] = "sse"
        result[name] = normalized
    return result


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
            precheck = _stdio_precheck(cfg)
            if precheck:
                results.append({
                    "name": name,
                    "status": "error",
                    "tools_count": 0,
                    "error": precheck,
                })
                logger.warning(f"MCP 校验 | {name} ❌ 配置预检失败: {precheck}")
                continue
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
            err_text = _unpack_error(e)
            results.append({
                "name": name,
                "status": "error",
                "tools_count": 0,
                "error": err_text,
            })
            logger.warning(f"MCP 校验 | {name} ❌ 失败: {err_text}")
    return results
