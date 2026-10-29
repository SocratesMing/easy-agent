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
    from easy_agent.bootstrap.config import Config

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
            # 需要用户填写的字段（鉴权 Key、路径等）：[{key, label, placeholder, required}]
            # key 是 config 内的路径："headers.Authorization" / "env.TCA_TOKEN" / "args.1"
            "fields": _normalize_fields(raw.get("fields")),
        })
    return result


def _normalize_fields(raw: Any) -> list[dict[str, Any]]:
    """规范化目录条目的 fields 声明，非法项跳过。"""
    if not isinstance(raw, list):
        return []
    fields: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        fields.append({
            "key": key,
            "label": str(item.get("label") or key),
            "placeholder": str(item.get("placeholder") or ""),
            "required": bool(item.get("required", True)),
            # "workspace"：值是文件路径，普通用户只能指向自己的工作区（见 mcp_policy）
            "path": str(item.get("path") or "").strip(),
        })
    return fields


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
    from easy_agent.bootstrap.config import Config
    from easy_agent.services.mcp import load_user_mcp_config

    entries = load_catalog()
    # 只看用户自己的 mcp.json（不回退全局）：全局那份现在是公共连接器目录，
    # 混进来会把目录里的条目误判成「已安装」。
    user_servers = load_user_mcp_config(username, config) or {}

    connectors = []
    for entry in entries:
        user_cfg = user_servers.get(entry["id"])
        installed = user_cfg is not None
        item = {
            **entry,
            "installed": installed,
            # 开关状态：已安装项读用户配置（默认开启），未安装项视为开启
            "enabled": bool(user_cfg.get("enabled", True)) if user_cfg else True,
        }
        if installed:
            # 已安装项回填用户实际配置（可能已改过 url/key），供详情页编辑展示
            item["user_config"] = user_cfg
        connectors.append(item)

    catalog_ids = {entry["id"] for entry in entries}
    custom = sorted(
        (
            {"name": name, "config": cfg, "enabled": bool(cfg.get("enabled", True))}
            for name, cfg in user_servers.items()
            if name not in catalog_ids
        ),
        key=lambda item: item["name"],
    )

    # 已安装的排在列表前面（含自定义项，由前端统一合并排序）
    connectors.sort(key=lambda c: not c["installed"])

    return {
        "connectors": connectors,
        "custom": custom,
        "path": str(Config.get_user_mcp_path(username, config)),
    }


# ---------------------------------------------------------------------------
# 「使用连接器[X]」指令解析
# ---------------------------------------------------------------------------

#: 输入框连接器胶囊对应的指令：``使用连接器[名称]``（中英文方括号都认，名称内允许空格）
CONNECTOR_DIRECTIVE_RE = re.compile(r"使用连接器\s*[\[【]\s*([^\[\]【】]+?)\s*[\]】]")

#: 单轮最多解析的连接器数：避免超长消息构造出大量解析与 MCP 加载
MAX_CONNECTOR_DIRECTIVES = 32


def parse_connector_directives(message: str) -> list[str]:
    """从消息文本里解析「使用连接器[名称]」指令。

    前端输入框选中连接器后会把这些指令前置到消息文本（多条以空格分隔），例如::

        使用连接器[MySQL 数据库] 帮我看看订单表

    返回按出现顺序去重后的连接器名（可能是展示名，也可能是 server 名，此处不做映射）；
    空名字与超出 ``MAX_CONNECTOR_DIRECTIVES`` 的部分忽略。无指令返回空列表。
    """
    if not message:
        return []
    names: list[str] = []
    seen: set[str] = set()
    for matched in CONNECTOR_DIRECTIVE_RE.finditer(message):
        name = matched.group(1).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= MAX_CONNECTOR_DIRECTIVES:
            break
    return names


def resolve_connector_servers(
    names: list[str],
    username: str,
    config: Any = None,
) -> tuple[list[str], list[str]]:
    """把指令里的连接器名映射为 mcp.json 的 server 名。

    前端胶囊展示、并写进消息文本的是连接器的**展示名**（公共目录的 name，如
    「MySQL 数据库」），而 mcp.json 的 key 是目录 id（如 ``mysql``）；自定义连接器
    则是用户自取的 server 名。因此按「先精确、后目录」两步匹配：

    1. 用户 mcp.json 里的 server 名（自定义连接器，或用户手写的目录 id）；
    2. 公共目录里 name 命中该名字、且该 id 确实在用户 mcp.json 里的条目 → 其 id。

    只有「这个用户自己的连接器」会被命中：未安装的目录连接器、停用项、完全无关的名字
    都归入未识别（停用项会被 mcp 加载层再提示一次「未启用已跳过」）。

    Args:
        names: ``parse_connector_directives`` 的结果
        username: 当前用户
        config: 运行期 ``Config``（可为 None，用于解析用户 mcp.json 路径）

    Returns:
        ``(命中的 server 名列表, 未识别的名字列表)``，均保持输入顺序且去重。
    """
    if not names:
        return [], []
    from easy_agent.services.mcp import load_user_mcp_config

    user_servers = load_user_mcp_config(username, config) or {}
    # 目录展示名 -> id：同名多条时全部保留（调用方按名字已无法区分）
    by_display_name: dict[str, list[str]] = {}
    for entry in load_catalog():
        by_display_name.setdefault(entry["name"], []).append(entry["id"])

    servers: list[str] = []
    unknown: list[str] = []
    for name in names:
        if name in user_servers:
            matched = [name]
        else:
            matched = [
                cid for cid in by_display_name.get(name, []) if cid in user_servers
            ]
        if not matched:
            unknown.append(name)
            continue
        for server in matched:
            if server not in servers:
                servers.append(server)
    return servers, unknown


def parse_message_connectors(
    message: str,
    username: str,
    config: Any = None,
) -> tuple[list[str], list[str]]:
    """一步完成「解析指令 + 映射为 server 名」，供 chat / 专家聊天接口共用。

    Returns:
        ``(命中的 server 名列表, 未识别的连接器名列表)``
    """
    return resolve_connector_servers(
        parse_connector_directives(message), username, config
    )
