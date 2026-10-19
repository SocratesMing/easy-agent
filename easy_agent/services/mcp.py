"""MCP (Model Context Protocol) tool integration service.

Uses langchain-mcp-adapters to connect to MCP servers and convert
their tools into LangChain BaseTool instances for the agent.
"""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime
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

    # Agent 工具加载：过滤停用（enabled: false）的 server
    return _load_servers_from_file(config_path, active_only=True)


def load_user_mcp_config(
    username: str,
    config: Config | None = None,
) -> dict[str, dict[str, Any]]:
    """只读当前用户自己的 mcp.json，**不做全局回退**。

    连接器页用它判断「已安装 / 自定义」，避免把全局 mcp.json（config 目录下那份，
    现在是公共连接器目录）里的内容误判成用户已安装。

    Args:
        username: 当前用户
        config: 运行期 Config（用于解析 workspace 路径，可为 None）
    """
    user_path = Config.get_user_mcp_path(username, config)
    if not user_path.exists():
        return {}
    logger.info(f"MCP config source: user file only ({user_path})")
    return _load_servers_from_file(user_path)


def _load_servers_from_file(
    config_path: Path, active_only: bool = False
) -> dict[str, dict[str, Any]]:
    """解析单个 mcp.json，返回归一化后的 servers 映射；任何异常都降级为空映射。"""
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

        # Agent 工具加载只取用户启用的 server（enabled: false 的跳过），
        # 并剥离 enabled 标记，避免 MultiServerMCPClient 收到未知字段。
        # 连接器页读取（load_user_mcp_config）不走这里——停用的也要显示「已安装」。
        if active_only:
            result = filter_active_servers(result)

        logger.info(
            f"MCP config loaded from {config_path}: "
            f"{len(result)} server(s) — {', '.join(result.keys())}"
        )
        return result

    except Exception as e:
        logger.warning(f"Failed to load mcp.json ({config_path}): {e}")
        return {}


def filter_active_servers(
    servers: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """过滤掉用户停用（``enabled: false``）的 server，并剥离 enabled 标记。

    用户 mcp.json 里每个 server 配置可带 ``enabled`` 布尔字段（连接器页的开关，
    默认开启）。Agent 加载工具时只取启用项，且要把 enabled 键从配置里去掉——
    MultiServerMCPClient 不认识该字段。其余读取/编辑场景不应使用本函数。
    """
    active: dict[str, dict[str, Any]] = {}
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("enabled") is False:
            continue
        cleaned = {k: v for k, v in cfg.items() if k != "enabled"}
        active[name] = cleaned
    return active


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

# Per-path MCP tool cache: {resolved_path_str: (mtime, {server_name: [tools]})}
# 按 server 分组缓存：既服务「注入全部启用连接器」，也服务「使用连接器[X]」只注入
# 选中连接器（见 get_mcp_tools 的 server_names），两种取法复用同一份缓存、只补差量。
_mcp_tools_cache: dict[str, tuple[float, dict[str, list]]] = {}


# ---------------------------------------------------------------------------
# Data provenance (防幻觉)
#
# MCP 工具返回什么数据，模型就只能用这些数据；回答还必须标注数据来源。
# 这靠三层保证：
#   1. 工具描述带上连接器归属，模型选工具时就知道数据来自哪里；
#   2. 工具返回内容前后注入「数据来源 + 使用规则」，让模型看到来源与约束；
#   3. 来源元数据随 SSE 的 tool_call / tool_result 下发给前端展示。
# ---------------------------------------------------------------------------

_MCP_SOURCE_MARKER = "_easy_mcp_source_wrapped"
_MCP_SOURCE_RESULT_HEADER = "【数据来源】连接器：{display_name}｜工具：{tool}｜调用时间：{called_at}"
_MCP_SOURCE_USAGE_RULE = (
    "【使用规则】以下为连接器返回的原始数据，只能据此回答："
    "字段缺失、为空或调用失败时必须如实说明「连接器未返回该项数据」，"
    "禁止推测、补全、换算或编造任何数值、行与字段；引用时保持原值。"
)


def _connector_display_names() -> dict[str, str]:
    """server 名 → 连接器展示名（公共目录 config/mcp.json 的 name）。

    自定义连接器（不在公共目录里）回退为 server 名本身。
    """
    try:
        from .connectors import load_catalog

        return {
            str(item.get("id")): str(item.get("name") or item.get("id"))
            for item in load_catalog()
            if item.get("id")
        }
    except Exception as e:  # 目录缺失/配置异常不影响工具加载
        logger.warning(f"读取连接器目录失败，来源标注将使用 server 名: {e}")
        return {}


def _build_source(server: str, display_name: str, tool: str) -> dict[str, str]:
    return {
        "server": server,
        "display_name": display_name or server,
        "tool": tool,
        "called_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _decorate_mcp_result(result: Any, source: dict[str, str]) -> Any:
    """在工具返回内容首尾注入来源与使用规则。

    仅改写文本块：多模态块（图片等）原样保留，避免破坏结构化返回。
    """
    header = _MCP_SOURCE_RESULT_HEADER.format(**source)
    prefix = f"{header}\n{_MCP_SOURCE_USAGE_RULE}\n【原始数据开始】"
    suffix = "\n【原始数据结束】"

    if isinstance(result, str):
        return f"{prefix}\n{result}{suffix}"
    if isinstance(result, list):
        decorated = [dict(item) if isinstance(item, dict) else item for item in result]
        # 文本块既可能是 {"type": "text", "text": ...}，也可能是纯 str
        # （langchain-mcp-adapters 的 content block 两种都可能出现）
        text_idx = [
            idx
            for idx, item in enumerate(decorated)
            if isinstance(item, str)
            or (isinstance(item, dict) and item.get("type") == "text")
        ]
        if not text_idx:
            decorated.insert(0, {"type": "text", "text": f"{prefix}{suffix}"})
            return decorated
        first, last = text_idx[0], text_idx[-1]
        for idx in text_idx:
            item = decorated[idx]
            is_str = isinstance(item, str)
            text = item if is_str else str(item.get("text", ""))
            if idx == first:
                text = f"{prefix}\n{text}"
            if idx == last:
                text = f"{text}{suffix}"
            decorated[idx] = text if is_str else {**item, "text": text}
        return decorated
    return f"{prefix}\n{result}{suffix}"


def _decorate_tool_output(
    result: Any, response_format: str, source: dict[str, str]
) -> Any:
    """装饰工具返回值，同时保留 StructuredTool 的输出协议。

    langchain-mcp-adapters 生成的 MCP 工具是
    ``StructuredTool(response_format="content_and_artifact")``，coroutine 必须返回
    ``(content, artifact)`` 二元组；这里只装饰 content，artifact 原样透传。
    否则 BaseTool._format_output 会因返回值不是二元组而抛
    「Tool must return a tuple of (content, artifact)」，整次工具调用失败。
    """
    if (
        response_format == "content_and_artifact"
        and isinstance(result, tuple)
        and len(result) == 2
    ):
        content, artifact = result
        return _decorate_mcp_result(content, source), artifact
    return _decorate_mcp_result(result, source)


def _prepare_mcp_tool(tool: Any, server: str, display_name: str) -> dict[str, str]:
    """给 MCP 工具打上来源元数据并包装返回值（幂等）。"""
    tool_name = getattr(tool, "name", "") or "tool"
    metadata = dict(getattr(tool, "metadata", None) or {})
    metadata["mcp_server"] = server
    metadata["mcp_display_name"] = display_name or server
    try:
        tool.metadata = metadata
    except Exception:
        pass

    if getattr(tool, _MCP_SOURCE_MARKER, False):
        return metadata

    description = str(getattr(tool, "description", "") or "")
    prefix = f"[连接器：{display_name or server}]"
    if description and not description.startswith(prefix):
        try:
            tool.description = f"{prefix} {description}"
        except Exception:
            pass

    def _source() -> dict[str, str]:
        return _build_source(server, display_name or server, tool_name)

    # MCP 工具是 content_and_artifact 协议（返回 (content, artifact) 二元组），
    # 包装时必须保留，否则工具调用会在 _format_output 阶段报错。
    response_format = str(getattr(tool, "response_format", "content") or "content")

    coroutine = getattr(tool, "coroutine", None)
    if coroutine is not None:
        async def _wrapped_coroutine(*args, **kwargs):
            result = await coroutine(*args, **kwargs)
            return _decorate_tool_output(result, response_format, _source())

        try:
            tool.coroutine = _wrapped_coroutine
            setattr(tool, _MCP_SOURCE_MARKER, True)
        except Exception as e:
            logger.warning(f"MCP 工具结果来源包装失败（{tool_name}）: {e}")
    else:
        func = getattr(tool, "func", None)
        if func is not None:
            def _wrapped_func(*args, **kwargs):
                return _decorate_tool_output(func(*args, **kwargs), response_format, _source())

            try:
                tool.func = _wrapped_func
                setattr(tool, _MCP_SOURCE_MARKER, True)
            except Exception as e:
                logger.warning(f"MCP 工具结果来源包装失败（{tool_name}）: {e}")

    return metadata


def collect_mcp_tool_sources(tools: list | None) -> dict[str, dict[str, str]]:
    """从已加载的工具列表提取「工具名 → 来源」，供流式事件标注使用。"""
    sources: dict[str, dict[str, str]] = {}
    for tool in tools or []:
        metadata = getattr(tool, "metadata", None) or {}
        server = metadata.get("mcp_server")
        if not server:
            continue
        name = getattr(tool, "name", "") or ""
        if not name:
            continue
        if name in sources:
            logger.warning(
                f"MCP 工具重名（{name}）：{sources[name].get('server')} 与 {server}，"
                f"来源标注以最后一个为准"
            )
        sources[name] = {
            "server": str(server),
            "display_name": str(metadata.get("mcp_display_name") or server),
            "tool": name,
        }
    return sources


async def _load_server_tools(
    client: MultiServerMCPClient, server_names: list[str]
) -> dict[str, list]:
    """并发加载若干 server 的工具。

    单个 server 起不来只记 warning 并返回空列表，不拖累其他 server
    （与连接器页的逐个探测状态一致）。
    """

    async def _load_one(name: str) -> tuple[str, list]:
        try:
            return name, await client.get_tools(server_name=name)
        except Exception as e:
            logger.warning(
                f"Failed to load MCP tools from server {name}: {_unpack_error(e)}"
            )
            return name, []

    pairs = await asyncio.gather(*(_load_one(name) for name in server_names))
    return dict(pairs)


async def get_mcp_tools(
    username: str | None = None,
    server_names: list[str] | None = None,
) -> list:
    """Get MCP tools as LangChain tools via langchain-mcp-adapters.

    Args:
        username: When provided, loads the per-user mcp.json if it exists,
            otherwise the global file.
        server_names: 只加载这些连接器（MCP server）的工具；``None`` 表示加载全部
            启用项。名字不在启用配置里的（未安装 / 已停用 / 名字不符）会被跳过并记
            warning。用户消息里的「使用连接器[X]」解析结果由此传入，实现
            「本轮只给模型挂上选中的连接器工具」。

    Creates a MultiServerMCPClient from mcp.json config and loads the
    available tools. Returns a list of LangChain BaseTool instances that
    can be passed directly to create_agent / create_deep_agent.

    Tools are cached per resolved config file path + mtime（按 server 分组），
    so editing the mcp.json and requesting tools again automatically picks up
    the change (dynamic reload). 各 server 独立加载、独立容错：某个连接器不可用时
    其余连接器的工具照常可用。MultiServerMCPClient is stateless by default —
    each tool invocation creates a fresh MCP session and cleans up afterwards.
    """
    config_path = _resolve_mcp_config_path(username)
    if not config_path:
        return []

    path_key = str(config_path)
    try:
        mtime = config_path.stat().st_mtime
    except OSError:
        mtime = 0.0

    config = load_mcp_config(username)
    if not config:
        _mcp_tools_cache[path_key] = (mtime, {})
        return []

    if server_names is None:
        wanted = list(config)
    else:
        selected = [str(n).strip() for n in server_names if str(n).strip()]
        wanted = [name for name in config if name in set(selected)]
        unavailable = [n for n in dict.fromkeys(selected) if n not in config]
        if unavailable:
            logger.warning(
                f"连接器指令指定的 server 未启用（未安装 / 已停用 / 名称不符），已跳过: "
                f"{', '.join(unavailable)}"
            )
        if not wanted:
            return []

    cached = _mcp_tools_cache.get(path_key)
    # 只有「同一文件 + 同一 mtime」的缓存可用；命中后按 server 补差量加载
    cached_servers = cached[1] if cached and cached[0] == mtime else {}
    missing = [name for name in wanted if name not in cached_servers]
    if missing:
        client = MultiServerMCPClient({name: config[name] for name in missing})
        cached_servers = {**cached_servers, **await _load_server_tools(client, missing)}
        total = sum(len(cached_servers.get(name, [])) for name in wanted)
        logger.info(
            f"MCP tools loaded ({path_key}, mtime={mtime}): servers={missing} | "
            f"{total} tool(s)"
        )
        for name in missing:
            for t in cached_servers.get(name, []):
                logger.info(f"  └─ [{name}] {t.name}")
    else:
        total = sum(len(cached_servers.get(name, [])) for name in wanted)
        logger.info(
            f"MCP tools cache hit ({path_key}, mtime={mtime}): servers={wanted} | "
            f"{total} tool(s)"
        )

    _mcp_tools_cache[path_key] = (mtime, cached_servers)

    # 打来源标记并包装返回值：模型看到的每份连接器数据都带「来源 + 使用规则」，
    # 前端 tool_result 也据此展示来源（见 collect_mcp_tool_sources）。
    displays = _connector_display_names()
    tools: list = []
    for name in wanted:
        display = displays.get(name, name)
        for tool in cached_servers.get(name, []):
            _prepare_mcp_tool(tool, name, display)
            tools.append(tool)
    return tools


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
