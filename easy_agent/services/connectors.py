"""公共 MCP 连接器目录的加载与组装。

连接器 = 一条可直接写入用户 ``mcp.json`` 的 MCP 服务配置。公共配置数据文件为
``config/mcp.json``（与 config.yaml 同目录），单条格式::

    {
      "id": "market-data",          # 同时作为写入用户 mcp.json 的 server 名
      "name": "行情数据",
      "description": "……",
      "icon": "chart",
      "config": {"transport": "streamable_http", "url": "...", "headers": {}}
    }

文件缺失、JSON 非法或单项字段缺失时一律降级（跳过该条并记 warning），
不向调用方抛异常——连接器页应始终能打开。
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("easy-agent")

#: 公共 MCP 配置文件名（放在 config 目录下，与 config.yaml 同级）
PUBLIC_MCP_FILENAME = "mcp.json"

# id 会作为 mcp.json 的 key 与前端 key，限制为安全标识符
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}$")


def resolve_public_mcp_path() -> Path | None:
    """定位公共 MCP 配置文件 ``config/mcp.json``，找不到返回 None。

    复用 ``Config.find_config_file`` 的查找顺序（``./easy_agent/config`` →
    ``~/.easy-agent/config`` → 包内 ``config``），与 config.yaml / 全局 mcp.json
    的解析保持一致，保证源码运行与安装运行都能命中。
    """
    from ..config import Config

    return Config.find_config_file(PUBLIC_MCP_FILENAME)


def _resolve_catalog_path(path: str | Path | None = None) -> Path | None:
    """显式路径优先（测试/单测用），否则解析 config/mcp.json。"""
    return Path(path) if path else resolve_public_mcp_path()


def load_catalog(path: str | Path | None = None) -> list[dict[str, Any]]:
    """读取公共 MCP 配置，返回规范化后的条目列表。

    非法条目（id 不合法、缺 name、缺 config、id 重复、非对象）会被跳过。
    """
    catalog_file = _resolve_catalog_path(path)
    if catalog_file is None:
        logger.warning(
            f"公共 MCP 配置文件不存在: config/{PUBLIC_MCP_FILENAME}（按空目录处理）"
        )
        return []
    try:
        data = json.loads(catalog_file.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning(f"公共 MCP 配置文件不存在: {catalog_file}（按空目录处理）")
        return []
    except Exception as e:
        logger.warning(f"公共 MCP 配置解析失败: {catalog_file} | {e}")
        return []

    items = data.get("connectors") if isinstance(data, dict) else data
    if not isinstance(items, list):
        logger.warning(f"连接器目录格式非法（connectors 需为数组）: {catalog_file}")
        return []

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            logger.warning(f"连接器目录存在非对象条目，已跳过: {raw!r}")
            continue
        cid = str(raw.get("id") or "").strip()
        name = str(raw.get("name") or "").strip()
        config = raw.get("config")
        if not _ID_RE.match(cid):
            logger.warning(f"连接器 id 非法，已跳过: {raw!r}")
            continue
        if not name:
            logger.warning(f"连接器 {cid} 缺少 name，已跳过")
            continue
        if not isinstance(config, dict) or not config:
            logger.warning(f"连接器 {cid} 缺少有效的 config，已跳过")
            continue
        if cid in seen:
            logger.warning(f"连接器 id 重复，已跳过: {cid}")
            continue
        seen.add(cid)
        result.append({
            "id": cid,
            "name": name,
            "description": str(raw.get("description") or ""),
            "icon": str(raw.get("icon") or ""),
            "config": config,
        })
    return result


def find_catalog_entry(
    connector_id: str,
    path: str | Path | None = None,
) -> dict[str, Any] | None:
    """按 id 取单条目录项，不存在返回 None。"""
    for entry in load_catalog(path):
        if entry["id"] == connector_id:
            return entry
    return None


def catalog_connectors(username: str, config: Any = None) -> dict[str, Any]:
    """组装连接器页数据。

    Args:
        username: 当前用户
        config: 运行期 ``Config``（可为 None，用于解析用户 mcp.json 路径）

    Returns:
        {
          "connectors": [{id, name, description, icon, config, installed}],
          "custom":     [{name, config}],   # 用户 mcp.json 里不在目录中的服务
          "path": "<用户 mcp.json 绝对路径>",
        }
    """
    from ..config import Config
    from .mcp import load_user_mcp_config

    entries = load_catalog()
    # 只看用户自己的 mcp.json（不回退全局）：全局那份现在是公共连接器目录，
    # 混进来会把目录里的条目误判成「已安装」。
    user_servers = load_user_mcp_config(username, config) or {}

    connectors = [
        {**entry, "installed": entry["id"] in user_servers}
        for entry in entries
    ]
    catalog_ids = {entry["id"] for entry in entries}
    custom = sorted(
        (
            {"name": name, "config": cfg}
            for name, cfg in user_servers.items()
            if name not in catalog_ids
        ),
        key=lambda item: item["name"],
    )

    return {
        "connectors": connectors,
        "custom": custom,
        "path": str(Config.get_user_mcp_path(username, config)),
    }
