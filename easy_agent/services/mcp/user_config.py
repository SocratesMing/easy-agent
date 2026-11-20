"""用户级 mcp.json 读写与格式归一化（从 api/connectors.py 下沉到服务层）。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from easy_agent.bootstrap.config import Config
from easy_agent.services.mcp.tools import normalize_servers_mapping
from easy_agent.services.memory.locks import atomic_write_text, memory_lock

logger = logging.getLogger("easy_agent.services.mcp.user_config")


def read_user_mcp_raw(username: str, config: Config | None) -> dict[str, Any]:
    """读取用户专属 mcp.json 原始内容，返回 ``{"servers": {name: cfg}}``。

    兼容标准 ``mcpServers`` 顶层键与旧版误存的嵌套外壳，统一展开归一化；
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


def write_user_mcp_raw(
    username: str, config: Config | None, payload: dict[str, Any]
) -> Path:
    """原子加锁写入用户专属 mcp.json，避免并发请求写坏配置文件。"""
    user_mcp_path = Config.get_user_mcp_path(username, config)
    user_mcp_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, indent=2, ensure_ascii=False)
    with memory_lock(user_mcp_path):
        atomic_write_text(user_mcp_path, serialized)
    return user_mcp_path


def extract_servers(user_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """从用户粘贴的 JSON 中提取 ``{name: cfg}`` 映射（支持 4 种常见格式）。"""
    servers = user_config.get("servers")
    if isinstance(servers, dict):
        return normalize_servers_mapping(servers)
    mcp_servers = user_config.get("mcpServers")
    if isinstance(mcp_servers, dict):
        return normalize_servers_mapping(mcp_servers)
    if "name" in user_config and isinstance(user_config["name"], str):
        name = user_config["name"]
        cfg = {k: v for k, v in user_config.items() if k != "name"}
        return normalize_servers_mapping({name: cfg})
    return normalize_servers_mapping(user_config)
