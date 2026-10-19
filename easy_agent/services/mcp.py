"""MCP (Model Context Protocol) tool integration service.

Uses langchain-mcp-adapters to connect to MCP servers and convert
their tools into LangChain BaseTool instances for the agent.
"""

import asyncio
import json
import logging
import os
from easy_agent.bootstrap.env import env_str
import shutil
from pathlib import Path
from typing import Any

from langchain_mcp_adapters.client import MultiServerMCPClient

from easy_agent.bootstrap.config import Config, _expand_env_recursive
from easy_agent.services import mcp_sessions
from easy_agent.services.mcp_evidence import wrap_mcp_tools
from easy_agent.tools.registry import builtin_tool_names

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

    # Agent 工具加载：过滤停用（enabled: false）的 server。
    # 用户自己的 mcp.json 不做 ${VAR} 展开（SEC-04）：否则用户写 ${EASY_JWT_SECRET}
    # 就能把服务端密钥拼进 URL / 请求头带出去；只有管理员维护的全局文件展开。
    user_file = bool(username) and config_path == Config.get_user_mcp_path(username)
    return _load_servers_from_file(
        config_path, active_only=True, expand_env=not user_file
    )


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
    return _load_servers_from_file(user_path, expand_env=False)


def _load_servers_from_file(
    config_path: Path, active_only: bool = False, *, expand_env: bool = True
) -> dict[str, dict[str, Any]]:
    """解析单个 mcp.json，返回归一化后的 servers 映射；任何异常都降级为空映射。

    ``expand_env`` 只对管理员维护的全局文件开启；用户文件原样读取（SEC-04）。
    """
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        if expand_env:
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


_TRANSPORT_ALIASES = {
    "streamable-http": "streamable_http",
    "streamablehttp": "streamable_http",
    "http": "streamable_http",
    "sse": "sse",
    "stdio": "stdio",
    "websocket": "websocket",
}


def _normalize_transport(value: str) -> str:
    """统一 transport 别名，避免 "streamable-http"/"http" 等写法被适配器拒绝。"""
    lowered = value.strip().lower()
    return _TRANSPORT_ALIASES.get(lowered, lowered)


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
        transport = normalized.pop("transport", None) or normalized.pop("type", None)
        if transport is None:
            # 标准 Claude Desktop 格式不写 type：
            # 有 command/args 的是 stdio 服务；URL-only 默认走现代 streamable_http
            # （FW-10：旧默认 sse 会用到已废弃的 HTTP+SSE 传输；仍显式写 sse 的配置保持兼容）。
            if "command" in normalized:
                transport = "stdio"
            elif "url" in normalized:
                transport = "streamable_http"
        if transport is not None:
            normalized["transport"] = _normalize_transport(str(transport))
        result[name] = normalized
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Per-path MCP tool cache: {resolved_path_str: (mtime, {server_name: [tools]})}
# 按 server 分组缓存：既服务「注入全部启用连接器」，也服务「使用连接器[X]」只注入
# 选中连接器（见 get_mcp_tools 的 server_names），两种取法复用同一份缓存、只补差量。
_mcp_tools_cache: dict[str, tuple[float, dict[str, list]]] = {}
_mcp_clients: dict[str, MultiServerMCPClient] = {}

# deepagents 内建工具名：MCP 工具同名时会静默覆盖内建实现（实测一个叫 delete 的
# MCP 工具会顶掉 0.7 新增的内建 delete），而 HITL 审批按工具名匹配 → 必须显式拒绝冲突。
# 名单以工具注册表为唯一事实来源，框架新增内建工具时只需登记注册表（DA-08）。
_BUILTIN_TOOL_NAMES = frozenset(builtin_tool_names())


def _client_for(servers: dict[str, dict[str, Any]]) -> MultiServerMCPClient:
    key = json.dumps(servers, sort_keys=True, ensure_ascii=False, default=str)
    client = _mcp_clients.get(key)
    if client is None:
        client = MultiServerMCPClient(servers)
        _mcp_clients[key] = client
        logger.info("复用 MCP 客户端已建立 | servers=%s", ",".join(servers))
    return client


async def _load_server_tools(
    client: MultiServerMCPClient,
    server_names: list[str],
    connections: dict[str, dict[str, Any]],
    scope: str = "",
) -> dict[str, list]:
    """并发加载若干 server 的工具。

    优先使用 D3 持久会话（``client.session`` + ``load_mcp_tools``）；不支持
    session API 的客户端（旧适配器 / 测试替身）退回原有 stateless
    ``client.get_tools``。单个 server 起不来只记 warning 并返回空列表，
    不拖累其他 server（与连接器页的逐个探测状态一致）。
    """

    persistent = mcp_sessions.supports_persistent_sessions(client)

    async def _load_one(name: str) -> tuple[str, list]:
        try:
            if persistent:
                tools = await mcp_sessions.load_server_tools(
                    client,
                    name,
                    connections.get(name, {}),
                    scope=scope,
                )
            else:
                tools = await client.get_tools(server_name=name)
            return name, tools
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
    其余连接器的工具照常可用。

    D3：工具绑定到 ``mcp_sessions`` 管理的持久会话（同一 server 连续调用只握手
    一次）。会话在 idle 过期 / ``invalidate_mcp_cache`` / 传输故障后按需重建；
    工具对象本身缓存不变。适配器或测试替身没有 ``client.session`` 时自动退回
    stateless 的 ``client.get_tools``。
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
    cached = _mcp_tools_cache.get(path_key)
    # 只有「同一文件 + 同一 mtime」的缓存可用；配置变化时先关闭旧会话，
    # 避免旧连接一直占着 stdio 子进程 / HTTP 连接直到 idle 过期。
    if cached and cached[0] != mtime:
        mcp_sessions.close_mcp_scope_sync(path_key)
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

    cached_servers = cached[1] if cached and cached[0] == mtime else {}
    missing = [name for name in wanted if name not in cached_servers]
    if env_str("AGENT_ENV").lower() == "prod":
        stdio_servers = [
            name
            for name, cfg in config.items()
            if str(cfg.get("transport", "stdio")).lower() == "stdio"
        ]
        if stdio_servers:
            logger.warning(
                "生产环境仍使用 stdio MCP（每 Pod 拉起子进程，多副本会重复连接）: %s。"
                "请改为 streamable-http / sse 并走集群内 Service。",
                ", ".join(stdio_servers),
            )

    if missing:
        missing_config = {name: config[name] for name in missing}
        client = _client_for(missing_config)
        cached_servers = {
            **cached_servers,
            **await _load_server_tools(
                client, missing, missing_config, scope=path_key
            ),
        }
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
    tools_by_server: dict[str, list] = {}
    seen: set[str] = set()
    for name in wanted:
        server_tools: list = []
        for tool in cached_servers.get(name, []):
            tool_name = str(getattr(tool, "name", "") or "")
            if tool_name in _BUILTIN_TOOL_NAMES:
                logger.warning(
                    "MCP 工具 %s 与内建工具同名，已跳过（避免静默覆盖内建工具/HITL 误判）",
                    tool_name,
                )
                continue
            if tool_name and tool_name in seen:
                logger.warning("MCP 工具名重复，保留第一个: %s", tool_name)
                continue
            if tool_name:
                seen.add(tool_name)
            server_tools.append(tool)
        tools_by_server[name] = server_tools
    # 证据信封：返回前为每个 server 的工具附加来源/结果元数据（FW-14）。
    # 缓存仍保存原始工具，避免包装器跨请求复用导致 server 归属错乱。
    return wrap_mcp_tools(tools_by_server)


def invalidate_mcp_cache(username: str | None = None) -> None:
    """Invalidate cached MCP tools.

    With ``username``, drops the cache entry for that user's mcp.json (and the
    global cache when falling back). Without ``username``, clears the entire
    cache. Call after writing a new mcp.json to force a reload on next use.
    """
    if username is None:
        _mcp_tools_cache.clear()
        _mcp_clients.clear()
        mcp_sessions.close_mcp_sessions_sync()
        logger.info("MCP tools cache cleared (all entries)")
        return

    # client 缓存按「配置 JSON 子集」为 key，无法按用户精确回收；整体清空以
    # 防止配置反复修改导致对象无界泄漏（有 miss 时会自动重建）。
    _mcp_clients.clear()

    user_path = Config.get_user_mcp_path(username)
    key = str(user_path)
    scopes = [key]
    if key in _mcp_tools_cache:
        _mcp_tools_cache.pop(key, None)
        logger.info(f"MCP tools cache invalidated for user {username} ({key})")
    # Also drop global cache so a fallback reload is fresh.
    global_path = _find_mcp_config()
    if global_path:
        gkey = str(global_path)
        scopes.append(gkey)
        if gkey in _mcp_tools_cache:
            _mcp_tools_cache.pop(gkey, None)
            logger.info(f"MCP tools cache invalidated for global ({gkey})")

    mcp_sessions.close_mcp_scopes_sync(scopes)
