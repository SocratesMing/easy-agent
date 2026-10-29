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

目录字段声明 ``"path": "workspace"`` 的（如 sqlite 连接器的 ``env.DB_PATH``）还要求值
落在用户自己的工作区内：写入时把相对路径换算成工作区内的绝对路径（stdio 进程的工作目录
是服务进程的，不是工作区），校验时 ``resolve()`` 后检查前缀，``..`` 与符号链接都逃不出去。
改造前该字段默认就指向应用自己的业务库，普通用户能读到所有用户的数据。
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

from easy_agent.bootstrap.config import Config
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


def _catalog_stdio_entry(cfg: dict[str, Any]) -> dict[str, Any] | None:
    """与 ``cfg`` 一致（可填字段除外）的公共目录 stdio 条目。"""
    for entry in _connectors.load_catalog():
        entry_cfg = entry.get("config") or {}
        if not is_stdio(entry_cfg):
            continue
        editable = [f["key"] for f in entry.get("fields") or [] if f.get("key")]
        if _canonical(cfg, editable) == _canonical(entry_cfg, editable):
            return entry
    return None


def _workspace_path_fields(entry: dict[str, Any]) -> list[str]:
    return [
        f["key"] for f in entry.get("fields") or [] if f.get("key") and f.get("path") == "workspace"
    ]


def _get_path(node: Any, key_path: str) -> Any:
    for part in key_path.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
            node = node[int(part)]
        else:
            return None
    return node


def _set_existing_path(node: Any, key_path: str, value: Any) -> None:
    *parents, last = key_path.split(".")
    for part in parents:
        node = node[int(part)] if isinstance(node, list) else node[part]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value


def _user_workspace(username: str) -> Path:
    return Config.get_user_workspace_dir(username).resolve()


def _workspace_path_violation(value: Any, username: str | None) -> str:
    if not username:
        return "缺少用户信息，无法校验文件路径"
    if not isinstance(value, str) or not value.strip():
        return "请填写文件路径"
    path = Path(value.strip())
    if not path.is_absolute():
        return "文件路径须位于个人工作区内（保存时相对路径会换算到工作区）"
    root = _user_workspace(username)
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        return "文件路径必须位于个人工作区内"
    return ""


CUSTOM_STDIO_REASON = (
    "普通用户不能使用自定义 stdio 连接器（会在服务器上直接执行命令）；"
    "只能使用公共目录里的 stdio 连接器，且只能修改其声明的字段"
)


def stdio_violation(cfg: Any, username: str | None) -> str:
    """违规原因；允许运行时返回空串。"""
    if not isinstance(cfg, dict) or not is_stdio(cfg) or username == ADMIN_USERNAME:
        return ""
    entry = _catalog_stdio_entry(cfg)
    if entry is None:
        return CUSTOM_STDIO_REASON
    for key_path in _workspace_path_fields(entry):
        reason = _workspace_path_violation(_get_path(cfg, key_path), username)
        if reason:
            return f"{key_path}：{reason}"
    return ""


def normalize_workspace_paths(servers: dict[str, Any], username: str) -> dict[str, Any]:
    """写入前把工作区路径字段的相对值换算成工作区内的绝对路径（返回新映射）。"""
    if username == ADMIN_USERNAME:
        return dict(servers or {})
    result: dict[str, Any] = {}
    for name, cfg in (servers or {}).items():
        entry = _catalog_stdio_entry(cfg) if isinstance(cfg, dict) and is_stdio(cfg) else None
        if entry is not None:
            for key_path in _workspace_path_fields(entry):
                value = _get_path(cfg, key_path)
                if isinstance(value, str) and value.strip() and not Path(value.strip()).is_absolute():
                    cfg = copy.deepcopy(cfg)
                    _set_existing_path(cfg, key_path, str(_user_workspace(username) / value.strip()))
        result[name] = cfg
    return result


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
