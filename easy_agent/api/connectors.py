"""连接器（MCP）API：内置目录、安装/卸载、mcp.json 原文编辑、自定义服务增删。

连接器 = 一条可直接写入用户 ``mcp.json``（``workspace/{username}/mcp.json``）的
MCP 服务配置。原先挂在 ``/agent/settings/mcp*`` 下的接口整体迁移到这里，
原「公共市场」（全局 mcp.json）接口已下线。
"""

import asyncio
import copy
import json
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from langchain_mcp_adapters.client import MultiServerMCPClient

from ..config import Config
from ..middleware import get_current_username
from ..services import (
    get_agent_config,
    get_session_connectors,
    invalidate_user_agents,
    resolve_connector_servers,
    set_session_connectors,
)
from ..services.connectors import catalog_connectors, find_catalog_entry
from ..services.mcp import (
    _unpack_error,
    invalidate_mcp_cache,
    load_mcp_config,
    normalize_servers_mapping,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent/connectors", tags=["Connectors"])

EMPTY_MCP_JSON = json.dumps({"servers": {}}, indent=2, ensure_ascii=False)


def _runtime_config() -> Config | None:
    """运行期配置（Agent 初始化后优先使用）。"""
    _cfg = get_agent_config()
    return _cfg["config"] if _cfg and _cfg.get("config") else None


def _read_user_mcp_raw(username: str, config: Config | None) -> dict[str, Any]:
    """读取用户专属 mcp.json 原始内容，返回 {"servers": {name: cfg}}。

    兼容标准 mcpServers 顶层键与旧版误存的嵌套外壳（名为 "mcpServers" 的
    伪 server），统一展开归一化，避免写入时保留脏结构。
    文件不存在或格式异常时返回空 servers。
    """
    user_mcp_path = Config.get_user_mcp_path(username, config)
    if not user_mcp_path.exists():
        return {"servers": {}}
    try:
        data = json.loads(user_mcp_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            servers = data.get("servers")
            if servers is None:
                servers = data.get("mcpServers", {})
            if isinstance(servers, dict):
                return {"servers": normalize_servers_mapping(servers)}
    except Exception as e:
        logger.warning(f"读取用户 mcp.json 失败 | 用户: {username} | {e}")
    return {"servers": {}}


def _write_user_mcp_raw(
    username: str, config: Config | None, payload: dict[str, Any]
) -> Path:
    """写入用户专属 mcp.json，返回文件路径。"""
    user_mcp_path = Config.get_user_mcp_path(username, config)
    user_mcp_path.parent.mkdir(parents=True, exist_ok=True)
    user_mcp_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return user_mcp_path


def _extract_servers(user_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """从用户粘贴的 JSON 中提取 {name: cfg} 映射。

    支持四种格式（以 mcp.json 的 servers 结构为基准）：
      1. {"servers": {"mysql": {...}}}          - 完整 mcp.json 片段
      2. {"mysql": {...}}                        - 对象格式
      3. {"name": "mysql", "transport": ...}     - 单条 server
      4. {"mcpServers": {"mysql": {...}}}        - 标准 Claude Desktop 格式

    所有格式都会把标准字段 "type" 归一化为应用内部字段 "transport"
    （如 {"type": "sse", "url": ...} -> {"transport": "sse", "url": ...}）。
    """
    # 格式1: {"servers": {"mysql": {...}}}
    servers = user_config.get("servers")
    if isinstance(servers, dict):
        return normalize_servers_mapping(servers)
    # 格式4: {"mcpServers": {"mysql": {...}}} - 标准 Claude Desktop 格式
    mcp_servers = user_config.get("mcpServers")
    if isinstance(mcp_servers, dict):
        return normalize_servers_mapping(mcp_servers)
    # 格式3: {"name": "mysql", ...}
    if "name" in user_config and isinstance(user_config["name"], str):
        name = user_config["name"]
        cfg = {k: v for k, v in user_config.items() if k != "name"}
        return normalize_servers_mapping({name: cfg})
    # 格式2: {"mysql": {...}}
    return normalize_servers_mapping(user_config)


async def _validate_servers(
    names: list[str], username: str
) -> list[dict[str, Any]]:
    """逐个加载指定 server 并返回状态，异常不抛出（供前端提示）。并发校验。"""
    try:
        all_config = load_mcp_config(username)
    except Exception as e:
        logger.warning(f"MCP 校验失败（读取配置）| 用户: {username} | {e}")
        return []

    tasks = [
        _validate_one(name, all_config[name])
        for name in names
        if all_config.get(name)
    ]
    return list(await asyncio.gather(*tasks))


async def _validate_one(name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """连接单个 server 校验状态，异常不抛出。剥离 enabled 标记（非 MCP 配置字段）。"""
    server_cfg = {k: v for k, v in cfg.items() if k != "enabled"}
    try:
        client = MultiServerMCPClient({name: server_cfg})
        tools = await client.get_tools()
        logger.info(f"MCP 校验 | {name} ✅ 成功 ({len(tools)} 工具)")
        return {"name": name, "status": "ok", "tools_count": len(tools), "error": ""}
    except Exception as e:
        err_text = _unpack_error(e)
        logger.warning(f"MCP 校验 | {name} ❌ 失败: {err_text}")
        return {"name": name, "status": "error", "tools_count": 0, "error": err_text}


@router.get("/status", summary="校验当前用户所有已安装 MCP 的连接状态")
async def get_connectors_status(
    username: Annotated[str, Depends(get_current_username)],
):
    """并发校验用户 mcp.json 中全部 server（含停用项），返回连接状态列表。

    停用（enabled: false）的 server 也会校验——用户开启前可先了解其可用性。
    连接失败不抛 500，逐条返回 {name, status: ok|error, tools_count, error}。
    """
    config = _runtime_config()
    raw = _read_user_mcp_raw(username, config)
    servers = raw.get("servers") or {}
    tasks = [
        _validate_one(name, cfg)
        for name, cfg in servers.items()
        if isinstance(cfg, dict)
    ]
    statuses = list(await asyncio.gather(*tasks))
    ok_count = sum(1 for s in statuses if s["status"] == "ok")
    logger.info(
        f"连接器状态校验 | 用户: {username} | 共 {len(statuses)} 个 "
        f"(正常 {ok_count} / 异常 {len(statuses) - ok_count})"
    )
    return {"statuses": statuses}


# ── 目录 / 安装 / 卸载 ────────────────────────────────────────────────


@router.get("", summary="获取连接器目录与用户自定义连接器")
async def list_connectors(
    username: Annotated[str, Depends(get_current_username)],
):
    """返回内置目录（标记是否已安装）+ 用户 mcp.json 里不在目录中的自定义项。"""
    data = catalog_connectors(username, _runtime_config())
    installed = sum(1 for c in data["connectors"] if c["installed"])
    logger.info(
        f"获取连接器 | 用户: {username} | 目录: {len(data['connectors'])} "
        f"(已安装 {installed}) | 自定义: {len(data['custom'])} | 路径: {data['path']}"
    )
    return data


class ConnectorIdRequest(BaseModel):
    id: str


class ConnectorInstallRequest(ConnectorIdRequest):
    """安装请求：values 填充目录条目 fields 声明的字段（Key/路径等）。

    validate=false 时跳过安装后的连接测试（前端「保存」按钮语义），
    默认 true（「运行」按钮语义：保存并立即验证连接）。
    """
    values: dict[str, str] = {}
    validate: bool = True


@router.post("/install", summary="安装内置连接器到用户 mcp.json")
async def install_connector(
    request: ConnectorInstallRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """把目录中的连接器配置写入当前用户 mcp.json，即时生效（默认启用）。

    条目声明了 fields 时，values 必须提供全部必填字段的值（缺失返回 400 并
    列出缺失字段的 label，供前端弹窗提示）；values 按 key 路径填充进 config。
    """
    config = _runtime_config()
    entry = find_catalog_entry(request.id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"连接器不存在: {request.id}")

    fields = entry.get("fields") or []

    # 必填字段校验：缺一个都不行（有多少列多少），缺失列表整体返回给前端
    missing = [
        f["label"] for f in fields
        if f.get("required", True) and not str(request.values.get(f["key"], "")).strip()
    ]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"请先填写以下必填信息: { '、'.join(missing) }",
            headers={"X-Missing-Fields": json.dumps(missing, ensure_ascii=False)},
        )

    # 深拷贝目录配置并填充用户填写的字段值，标记默认启用
    server_cfg = copy.deepcopy(entry["config"])
    for f in fields:
        if str(request.values.get(f["key"], "")).strip():
            _set_config_field(server_cfg, f["key"], request.values[f["key"]].strip())
    server_cfg.setdefault("enabled", True)

    raw = _read_user_mcp_raw(username, config)
    existing: dict[str, Any] = raw.get("servers", {})
    if request.id in existing:
        raise HTTPException(status_code=409, detail=f"连接器已添加: {request.id}")

    existing[request.id] = server_cfg
    raw["servers"] = existing
    user_mcp_path = _write_user_mcp_raw(username, config, raw)
    invalidate_mcp_cache(username)
    evicted = invalidate_user_agents(username)
    logger.info(
        f"安装连接器 | 用户: {username} | 连接器: {request.id} | "
        f"路径: {user_mcp_path} | 失效 Agent: {evicted} 个"
    )

    return {
        "status": "ok",
        "installed": request.id,
        "servers": list(existing.keys()),
        "path": str(user_mcp_path),
        "agents_invalidated": evicted,
        "server_status": (
            await _validate_servers([request.id], username)
            if request.validate
            else []
        ),
    }


@router.post("/uninstall", summary="卸载用户 mcp.json 中的连接器")
async def uninstall_connector(
    request: ConnectorIdRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """按 mcp.json 的 server 名删除连接器，即时生效。"""
    config = _runtime_config()
    raw = _read_user_mcp_raw(username, config)
    existing: dict[str, Any] = raw.get("servers", {})
    if request.id not in existing:
        raise HTTPException(status_code=404, detail=f"连接器不存在: {request.id}")

    existing.pop(request.id, None)
    raw["servers"] = existing
    user_mcp_path = _write_user_mcp_raw(username, config, raw)
    invalidate_mcp_cache(username)
    evicted = invalidate_user_agents(username)
    logger.info(
        f"卸载连接器 | 用户: {username} | 连接器: {request.id} | "
        f"剩余: {list(existing.keys())} | 路径: {user_mcp_path}"
    )

    return {
        "status": "ok",
        "uninstalled": request.id,
        "servers": list(existing.keys()),
        "path": str(user_mcp_path),
        "agents_invalidated": evicted,
    }


# ── 字段路径读写（fields 声明的 key → config 内的值）──────────────────
#
# key 是 config 内的路径，支持两种段：
#   字典段："headers.Authorization" / "env.TCA_TOKEN"
#   数组段："args.1"（数字段索引进 list，用于 stdio 的位置参数）


def _get_config_field(cfg: dict[str, Any], key_path: str) -> tuple[bool, Any]:
    """读取 config 内字段路径的值，返回 (是否存在, 值)。"""
    cur: Any = cfg
    for seg in key_path.split("."):
        if isinstance(cur, dict):
            if seg not in cur:
                return False, None
            cur = cur[seg]
        elif isinstance(cur, list) and seg.isdigit() and int(seg) < len(cur):
            cur = cur[int(seg)]
        else:
            return False, None
    return True, cur


def _set_config_field(cfg: dict[str, Any], key_path: str, value: Any) -> None:
    """写入 config 内字段路径的值，沿途容器不存在时自动创建。

    数字段表示数组索引：沿途缺失时补建 list（用 None 占位到目标下标），
    已存在时原地覆盖（如 sqlite 的 args.1 / args.5）。
    """
    segs = key_path.split(".")
    cur: Any = cfg
    for i, seg in enumerate(segs[:-1]):
        next_is_index = segs[i + 1].isdigit()
        if isinstance(cur, dict):
            if not isinstance(cur.get(seg), (dict, list)):
                cur[seg] = [] if next_is_index else {}
            cur = cur[seg]
        elif isinstance(cur, list) and seg.isdigit():
            idx = int(seg)
            while len(cur) <= idx:
                cur.append({})
            cur = cur[idx]
        else:  # 理论不可达：调用前已按同样规则校验过路径
            raise ValueError(f"非法字段路径: {key_path}")
    last = segs[-1]
    if isinstance(cur, dict):
        cur[last] = value
    elif isinstance(cur, list) and last.isdigit():
        idx = int(last)
        while len(cur) <= idx:
            cur.append(None)
        cur[idx] = value
    else:
        raise ValueError(f"非法字段路径: {key_path}")


def _allowed_config_keys(entry: dict[str, Any] | None) -> list[str] | None:
    """可编辑字段白名单：目录条目用其 fields 声明；自定义服务放开 url/headers/env。"""
    if entry and entry.get("fields"):
        return [f["key"] for f in entry["fields"]]
    if entry is None:  # 自定义服务（不在目录中）
        return ["url", "headers.*", "env.*"]
    return []  # 目录条目未声明 fields：不允许在线改配置（可走 mcp.json 原文）


def _key_allowed(allowed: list[str], key_path: str) -> bool:
    for pattern in allowed:
        if pattern.endswith(".*") and key_path.startswith(pattern[:-1]):
            return True
        if pattern == key_path:
            return True
    return False


# ── 开关 / 工具列表 / 已安装项配置修改 ────────────────────────────────


class ConnectorToggleRequest(BaseModel):
    id: str
    enabled: bool


@router.post("/toggle", summary="启用/停用已安装的连接器（开关）")
async def toggle_connector(
    request: ConnectorToggleRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """切换用户 mcp.json 中该 server 的 enabled 标记（默认开启）。

    停用后 Agent 不再加载该 server 的工具（缓存同步失效），配置本身保留，
    重新打开即恢复。id 同时接受目录条目 id 与自定义 server 名。
    """
    config = _runtime_config()
    raw = _read_user_mcp_raw(username, config)
    existing: dict[str, Any] = raw.get("servers", {})
    cfg = existing.get(request.id)
    if not isinstance(cfg, dict):
        raise HTTPException(status_code=404, detail=f"连接器不存在: {request.id}")

    cfg["enabled"] = request.enabled
    raw["servers"] = existing
    user_mcp_path = _write_user_mcp_raw(username, config, raw)
    invalidate_mcp_cache(username)
    evicted = invalidate_user_agents(username)
    logger.info(
        f"切换连接器开关 | 用户: {username} | 连接器: {request.id} | "
        f"enabled: {request.enabled} | 失效 Agent: {evicted} 个"
    )
    return {
        "status": "ok",
        "id": request.id,
        "enabled": request.enabled,
        "agents_invalidated": evicted,
    }


# ── 会话级开关（输入框「+」→ 连接器面板，仅本次会话临时生效） ──────────────


class SessionConnectorsRequest(BaseModel):
    """会话级连接器开关：全量提交该会话当前**启用**的连接器。

    元素为公共目录的 id 或自定义 server 名（与 mcp.json 的 server key 一致），
    由 ``resolve_connector_servers`` 归一化。空列表表示该会话把所有连接器都关掉。
    """

    session_id: str
    connectors: list[str] = []


@router.get("/session", summary="查询会话级连接器开关（null = 尚未设置，等价于全部启用）")
async def get_session_connector_switches(
    session_id: str,
    username: Annotated[str, Depends(get_current_username)],
):
    """返回该会话显式启用的连接器 server 名列表。

    ``connectors: null`` 表示这个会话还没动过开关（默认全部启用），
    与「空数组 = 用户把开关全部关闭」是两种不同状态。
    """
    servers = get_session_connectors(session_id)
    return {
        "session_id": session_id,
        "connectors": None if servers is None else list(servers),
    }


@router.post("/session", summary="设置会话级连接器开关（仅本次会话，不写 mcp.json）")
async def set_session_connector_switches(
    request: SessionConnectorsRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """全量写入该会话的连接器启用集合，并驱逐其缓存 Agent 让新工具集立即生效。

    与 ``/toggle``（持久开关，写用户 mcp.json 的 ``enabled``）区分：这里只影响
    当前会话的 Agent 工具集，进程重启或换会话即回到默认的「全部启用」。
    """
    servers, unknown = resolve_connector_servers(
        request.connectors, username, _runtime_config()
    )
    evicted = set_session_connectors(request.session_id, servers)
    if unknown:
        logger.warning(
            f"会话连接器开关存在未识别项（未安装 / 已停用 / 名称不符），已忽略: {unknown}"
        )
    logger.info(
        f"设置会话连接器开关 | 用户: {username} | 会话: {request.session_id} | "
        f"启用: {servers or '(全部关闭)'} | 驱逐 Agent: {evicted} 个"
    )
    return {
        "status": "ok",
        "session_id": request.session_id,
        "connectors": servers,
        "unknown": unknown,
        "agents_invalidated": evicted,
    }


@router.get("/tools", summary="查看已安装连接器的工具列表")
async def list_connector_tools(
    name: str,
    username: Annotated[str, Depends(get_current_username)],
):
    """连接单个 server 拉取其实际暴露的工具（名称 + 描述）。

    与 Agent 加载不同，这里对停用的 server 也能查看（便于开启前了解能力）。
    连接失败不抛 500，返回 {tools: [], error: "..."} 供前端展示。
    """
    config = _runtime_config()
    raw = _read_user_mcp_raw(username, config)
    cfg = (raw.get("servers") or {}).get(name)
    if not isinstance(cfg, dict):
        raise HTTPException(status_code=404, detail=f"连接器不存在: {name}")

    server_cfg = {k: v for k, v in cfg.items() if k != "enabled"}
    try:
        client = MultiServerMCPClient({name: server_cfg})
        tools = await client.get_tools()
        result = [
            {"name": t.name, "description": (t.description or "").strip()}
            for t in tools
        ]
        logger.info(
            f"获取连接器工具 | 用户: {username} | {name}: {len(result)} 个工具"
        )
        return {"name": name, "tools": result, "error": ""}
    except Exception as e:
        err_text = _unpack_error(e)
        logger.warning(f"获取连接器工具失败 | 用户: {username} | {name}: {err_text}")
        return {"name": name, "tools": [], "error": err_text}


class ConnectorConfigUpdateRequest(BaseModel):
    id: str
    values: dict[str, str]


@router.put("/config", summary="修改已安装连接器的配置字段（url / key 等）")
async def update_connector_config(
    request: ConnectorConfigUpdateRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """按字段白名单更新用户 mcp.json 中该 server 的配置值。

    白名单 = 目录条目的 fields 声明；自定义服务放开 url / headers.* / env.*。
    空字符串值会被跳过（避免误把已填的 Key 清空）。更新后自动校验连接状态。
    """
    config = _runtime_config()
    entry = find_catalog_entry(request.id)
    allowed = _allowed_config_keys(entry)
    if not allowed:
        raise HTTPException(
            status_code=400,
            detail=f"该连接器未声明可修改字段，请通过 mcp.json 原文编辑: {request.id}",
        )

    bad = [k for k in request.values if not _key_allowed(allowed, k)]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"以下字段不允许修改: {', '.join(bad)}",
        )

    raw = _read_user_mcp_raw(username, config)
    existing: dict[str, Any] = raw.get("servers", {})
    cfg = existing.get(request.id)
    if not isinstance(cfg, dict):
        raise HTTPException(status_code=404, detail=f"连接器不存在: {request.id}")

    applied = {}
    for key_path, value in request.values.items():
        if not str(value).strip():
            continue
        _set_config_field(cfg, key_path, value)
        applied[key_path] = value
    if applied:
        raw["servers"] = existing
        _write_user_mcp_raw(username, config, raw)
        invalidate_mcp_cache(username)
        evicted = invalidate_user_agents(username)
        logger.info(
            f"修改连接器配置 | 用户: {username} | 连接器: {request.id} | "
            f"字段: {list(applied.keys())}"
        )
    else:
        evicted = 0

    return {
        "status": "ok",
        "id": request.id,
        "applied": list(applied.keys()),
        "agents_invalidated": evicted,
        "server_status": (
            await _validate_servers([request.id], username) if applied else []
        ),
    }


# ── mcp.json 原文读写 ─────────────────────────────────────────────────


@router.get("/mcp/raw", summary="读取用户 mcp.json 原文")
async def get_mcp_raw(
    username: Annotated[str, Depends(get_current_username)],
):
    """返回配置文件路径与原始文本（文件不存在时给出空结构模板）。"""
    config = _runtime_config()
    user_mcp_path = Config.get_user_mcp_path(username, config)
    if not user_mcp_path.exists():
        logger.info(f"读取 mcp.json 原文 | 用户: {username} | 文件不存在，返回空模板")
        return {"path": str(user_mcp_path), "content": EMPTY_MCP_JSON}
    return {
        "path": str(user_mcp_path),
        "content": user_mcp_path.read_text(encoding="utf-8"),
    }


class SaveMcpRawRequest(BaseModel):
    content: str


@router.put("/mcp/raw", summary="保存用户 mcp.json 原文")
async def save_mcp_raw(
    request: SaveMcpRawRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """校验并保存整份 mcp.json，随后失效 MCP 缓存与用户 Agent。

    落盘前统一归一化为 ``{"servers": {...}}``，因此四种书写格式都可接受；
    保存后请重新拉取原文，前端展示的才是规范化结果。
    """
    try:
        parsed = json.loads(request.content)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=400,
            detail=f"JSON 格式无效：第 {e.lineno} 行第 {e.colno} 列 {e.msg}",
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"JSON 解析失败: {e}")

    if not isinstance(parsed, dict):
        raise HTTPException(status_code=400, detail="mcp.json 顶层必须是 JSON 对象")

    servers = _extract_servers(parsed)
    config = _runtime_config()
    user_mcp_path = _write_user_mcp_raw(username, config, {"servers": servers})
    invalidate_mcp_cache(username)
    evicted = invalidate_user_agents(username)
    logger.info(
        f"保存 mcp.json 原文 | 用户: {username} | 路径: {user_mcp_path} | "
        f"servers: {list(servers.keys())} | 失效 Agent: {evicted} 个"
    )

    return {
        "status": "ok",
        "path": str(user_mcp_path),
        "servers": list(servers.keys()),
        "agents_invalidated": evicted,
        "server_status": await _validate_servers(list(servers.keys()), username),
    }


# ── 单个 MCP 服务增删（粘贴 JSON 添加 / 弹层移除） ──────────────────────


class AddMcpServerRequest(BaseModel):
    config: dict[str, Any]


@router.post("/mcp", summary="粘贴 JSON 添加 MCP 服务")
async def add_mcp_server(
    request: AddMcpServerRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """添加一个或多个 MCP server。

    用户粘贴的 JSON 会被解析，以 servers 下的 key 作为 MCP 名称进行判重：
    若名称已存在则返回 409（不写入）。全部新增成功后立即失效缓存与 Agent，
    并逐个校验新添加的 server，异常的在前端提示。
    """
    config = _runtime_config()
    new_servers = _extract_servers(request.config)
    if not new_servers:
        raise HTTPException(
            status_code=400, detail="未找到有效的 server 配置，请检查 JSON 格式"
        )

    raw = _read_user_mcp_raw(username, config)
    existing: dict[str, Any] = raw.get("servers", {})

    duplicates = [n for n in new_servers if n in existing]
    if duplicates:
        raise HTTPException(
            status_code=409,
            detail=f"MCP 服务已存在: {', '.join(duplicates)}（请先删除或改名）",
        )

    existing.update(new_servers)
    raw["servers"] = existing
    user_mcp_path = _write_user_mcp_raw(username, config, raw)
    logger.info(
        f"添加 MCP | 用户: {username} | 新增: {list(new_servers.keys())} | "
        f"总数: {len(existing)} | 路径: {user_mcp_path}"
    )

    invalidate_mcp_cache(username)
    evicted = invalidate_user_agents(username)

    return {
        "status": "ok",
        "added": list(new_servers.keys()),
        "servers": list(existing.keys()),
        "path": str(user_mcp_path),
        "agents_invalidated": evicted,
        "server_status": await _validate_servers(list(new_servers.keys()), username),
    }


@router.delete("/mcp/{name}", summary="删除指定 MCP 服务")
async def delete_mcp_server(
    name: str,
    username: Annotated[str, Depends(get_current_username)],
):
    """按 servers 下的名称删除单个 MCP server，即时生效。"""
    config = _runtime_config()
    raw = _read_user_mcp_raw(username, config)
    existing: dict[str, Any] = raw.get("servers", {})
    if name not in existing:
        raise HTTPException(status_code=404, detail=f"MCP 服务不存在: {name}")

    existing.pop(name, None)
    raw["servers"] = existing
    user_mcp_path = _write_user_mcp_raw(username, config, raw)
    logger.info(
        f"删除 MCP | 用户: {username} | 删除: {name} | 剩余: {list(existing.keys())} | "
        f"路径: {user_mcp_path}"
    )

    invalidate_mcp_cache(username)
    evicted = invalidate_user_agents(username)

    return {
        "status": "ok",
        "deleted": name,
        "servers": list(existing.keys()),
        "path": str(user_mcp_path),
        "agents_invalidated": evicted,
    }
