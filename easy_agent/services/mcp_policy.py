"""用户 MCP 连接器的执行策略（SEC-04）。

stdio 连接器会在宿主机、沙箱外直接拉起子进程：任何登录用户都能提交 stdio 配置，
等于拿到一个不受沙箱约束的 shell。因此：

- admin 不受限制；
- 普通用户的 stdio 连接器必须与公共目录（管理员维护的 ``config/mcp.json``）中某个
  stdio 条目一致——只允许改目录声明的可填字段（``fields``，如 ``env.MYSQL_HOST``），
  command / args / env 的其余部分、cwd 等一律不能改，也不能加；
- HTTP 类连接器（streamable_http / sse / websocket）不受影响。

写入（安装 / 改字段 / 原文保存 / 粘贴添加）时拒绝违规配置；加载、校验、拉工具列表时
跳过已存在的违规项（兼容本策略上线前写入的配置），绝不为它们拉起进程。
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from easy_agent.services import connectors as _connectors
from easy_agent.services.mcp import load_mcp_config

logger = logging.getLogger(__name__)

ADMIN_USERNAME = "admin"

# 不参与一致性比较的键：传输类型写法（type / transport）与连接器开关
_IGNORED_KEYS = frozenset({"type", "transport", "enabled"})


class McpPolicyError(ValueError):
    """用户提交的 MCP 配置违反执行策略（接口转成 403）。"""


def is_stdio(cfg: dict[str, Any]) -> bool:
    """与 ``normalize_servers_mapping`` 同口径：未写传输类型但有 command 的也是 stdio。"""
    transport = cfg.get("transport") or cfg.get("type")
    if transport is None:
        return "command" in cfg
    return str(transport).strip().lower() == "stdio"


def _erase_path(node: Any, parts: list[str]) -> None:
    """去掉可填字段的值：dict 段删除键，list 段（``args.1``）置空但保留位置。"""
    head, rest = parts[0], parts[1:]
    if isinstance(node, dict):
        if head not in node:
            return
        if rest:
            _erase_path(node[head], rest)
        else:
            node.pop(head)
    elif isinstance(node, list) and head.isdigit() and int(head) < len(node):
        if rest:
            _erase_path(node[int(head)], rest)
        else:
            node[int(head)] = None


def _canonical(cfg: dict[str, Any], editable: list[str]) -> dict[str, Any]:
    data = {k: copy.deepcopy(v) for k, v in cfg.items() if k not in _IGNORED_KEYS}
    for key_path in editable:
        _erase_path(data, key_path.split("."))
    return data


def _matches_catalog_stdio(cfg: dict[str, Any]) -> bool:
    for entry in _connectors.load_catalog():
        entry_cfg = entry.get("config") or {}
        if not is_stdio(entry_cfg):
            continue
        editable = [f["key"] for f in entry.get("fields") or [] if f.get("key")]
        if _canonical(cfg, editable) == _canonical(entry_cfg, editable):
            return True
    return False


def stdio_violation(cfg: Any, username: str | None) -> str:
    """违规原因；允许运行时返回空串。"""
    if not isinstance(cfg, dict) or not is_stdio(cfg) or username == ADMIN_USERNAME:
        return ""
    if _matches_catalog_stdio(cfg):
        return ""
    return (
        "普通用户不能使用自定义 stdio 连接器（会在服务器上直接执行命令）；"
        "只能使用公共目录里的 stdio 连接器，且只能修改其声明的字段"
    )


def find_violations(servers: dict[str, Any], username: str | None) -> dict[str, str]:
    """``{server 名: 原因}``，没有违规返回空 dict。"""
    violations: dict[str, str] = {}
    for name, cfg in (servers or {}).items():
        reason = stdio_violation(cfg, username)
        if reason:
            violations[name] = reason
    return violations


def ensure_servers_allowed(servers: dict[str, Any], username: str | None) -> None:
    """写入前校验：有违规项时抛 :class:`McpPolicyError`（消息列出违规的 server 名）。"""
    violations = find_violations(servers, username)
    if violations:
        names = "、".join(violations)
        raise McpPolicyError(f"{next(iter(violations.values()))}：{names}")


def filter_server_names(
    username: str, server_names: list[str] | None
) -> list[str] | None:
    """Agent 加载工具前去掉违规的 server（``None`` = 全部启用项，语义同 ``get_mcp_tools``）。"""
    servers = load_mcp_config(username)
    blocked = find_violations(servers, username)
    if not blocked:
        return server_names
    logger.warning(
        f"跳过不允许运行的 stdio 连接器 | 用户: {username} | {', '.join(blocked)}"
    )
    if server_names is None:
        return [name for name in servers if name not in blocked]
    return [name for name in server_names if name not in blocked]
