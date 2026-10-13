"""连接器（MCP）API：内置目录、安装/卸载、mcp.json 原文编辑、自定义服务增删。

连接器 = 一条可直接写入用户 ``mcp.json``（``workspace/{username}/mcp.json``）的
MCP 服务配置。原先挂在 ``/agent/settings/mcp*`` 下的接口整体迁移到这里，
原「公共市场」（全局 mcp.json）接口已下线。
"""

import json
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from langchain_mcp_adapters.client import MultiServerMCPClient

from ..config import Config
from ..middleware import get_current_username
from ..services import get_agent_config, invalidate_user_agents
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
    """逐个加载指定 server 并返回状态，异常不抛出（供前端提示）。"""
    results: list[dict[str, Any]] = []
    try:
        all_config = load_mcp_config(username)
    except Exception as e:
        logger.warning(f"MCP 校验失败（读取配置）| 用户: {username} | {e}")
        return results

    for name in names:
        cfg = all_config.get(name)
        if not cfg:
            continue
        try:
            client = MultiServerMCPClient({name: cfg})
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


@router.post("/install", summary="安装内置连接器到用户 mcp.json")
async def install_connector(
    request: ConnectorIdRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """把目录中的连接器配置写入当前用户 mcp.json，即时生效。"""
    config = _runtime_config()
    entry = find_catalog_entry(request.id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"连接器不存在: {request.id}")

    raw = _read_user_mcp_raw(username, config)
    existing: dict[str, Any] = raw.get("servers", {})
    if request.id in existing:
        raise HTTPException(status_code=409, detail=f"连接器已添加: {request.id}")

    existing[request.id] = dict(entry["config"])
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
        "server_status": await _validate_servers([request.id], username),
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
