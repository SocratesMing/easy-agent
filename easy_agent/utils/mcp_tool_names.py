"""MCP 工具名规范化（D4）：``{server}__{tool}``。

背景：多个 MCP server 暴露同名工具时，原名会让模型/审计无法区分来源，冲突只能
静默丢弃（见 ``docs/design/2026-10-20-d4-mcp-tool-naming.md``）。

灰度开关：``EASY_MCP_TOOL_PREFIX``，默认 **off**，此模块的命名函数只有在开关打开时
才会被 ``services/mcp.py`` 使用；关闭时行为与旧版完全一致。

命名规则（稳定、可回退）：

- server 段：非 ``[A-Za-z0-9_-]`` 替换为 ``_``，截断到 24 字符；被截断时追加 6 位
  sha256（``server__abcd12``），避免不同 server 撞名；
- tool 段：保留服务端原始名，非法字符替换，总长截断到 64（OpenAI 工具名上限），
  截断时同样追加哈希后缀；
- 原始名写入 ``tool.metadata["mcp_original_name"]``，HITL 启发式与审计展示都用它。
"""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Iterable, Mapping
from typing import Any

MAX_TOOL_NAME_CHARS = 64
MAX_SERVER_CHARS = 24
_HASH_CHARS = 6
_ILLEGAL = re.compile(r"[^A-Za-z0-9_-]")
_SEPARATOR = "__"

_PREFIX_ENV = "EASY_MCP_TOOL_PREFIX"
_TRUTHY = {"1", "true", "yes", "on"}


def prefix_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """开关是否打开（默认关闭，灰度期显式开启）。"""
    env = os.environ if environ is None else environ
    return str(env.get(_PREFIX_ENV, "")).strip().lower() in _TRUTHY


def _hash_suffix(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:_HASH_CHARS]


def _sanitize(segment: str, limit: int, *, hash_source: str) -> str:
    # 连续下划线折叠成单个：避免与 server/tool 分隔符 __ 冲突
    cleaned = re.sub(r"_+", "_", _ILLEGAL.sub("_", segment or "")).strip("_")
    if not cleaned:
        cleaned = "tool"
    if len(cleaned) <= limit:
        return cleaned
    suffix = _hash_suffix(hash_source)
    return f"{cleaned[: limit - _HASH_CHARS - 1]}_{suffix}"


def server_segment(server: str) -> str:
    """server key → 可用作工具名前缀的片段。"""
    return _sanitize(str(server or ""), MAX_SERVER_CHARS, hash_source=str(server or ""))


def prefixed_name(server: str, tool: str) -> str:
    """``{server}__{tool}``，总长不超过 64。"""
    server_part = server_segment(server)
    room = MAX_TOOL_NAME_CHARS - len(server_part) - len(_SEPARATOR)
    tool_part = _sanitize(str(tool or ""), max(room, _HASH_CHARS + 1), hash_source=str(tool or ""))
    return f"{server_part}{_SEPARATOR}{tool_part}"


def original_name(tool: Any) -> str:
    """取工具原名：优先 metadata 里记录的原名，其次当前 name。"""
    metadata = getattr(tool, "metadata", None)
    if isinstance(metadata, Mapping):
        raw = metadata.get("mcp_original_name")
        if isinstance(raw, str) and raw:
            return raw
    return str(getattr(tool, "name", "") or "")


def rename_tool(tool: Any, server: str) -> Any:
    """把工具重命名为 ``{server}__{tool}``，并记录原名；失败时原样返回。"""
    current = str(getattr(tool, "name", "") or "")
    if not current:
        return tool
    new_name = prefixed_name(server, current)
    if new_name == current:
        return tool
    metadata = getattr(tool, "metadata", None)
    merged = dict(metadata) if isinstance(metadata, Mapping) else {}
    merged.setdefault("mcp_original_name", current)
    merged.setdefault("mcp_server", server)
    try:
        # langchain BaseTool 是 pydantic 模型，model_copy 不会改动原对象
        return tool.model_copy(update={"name": new_name, "metadata": merged})
    except Exception:  # noqa: BLE001 - 非 pydantic 工具退回就地赋值
        try:
            tool.name = new_name
            tool.metadata = merged
        except Exception:  # noqa: BLE001
            return tool
        return tool


def resolve_tool_names(
    requested: Iterable[str] | None, available: Iterable[str] | None
) -> tuple[dict[str, str], list[str]]:
    """把「用户写的工具名」解析成实际工具名（D4 兼容层）。

    规则：先精确匹配；未命中时按 ``__`` 后缀唯一匹配（``save_expert`` → ``github__save_expert``）；
    多个候选视为歧义。

    Returns:
        (映射: 请求名 → 实际名, 歧义/未找到的请求名列表)
    """
    actual = [str(name) for name in (available or ()) if name]
    actual_set = set(actual)
    suffix_map: dict[str, list[str]] = {}
    for name in actual:
        if _SEPARATOR in name:
            suffix_map.setdefault(name.rsplit(_SEPARATOR, 1)[-1], []).append(name)

    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    for name in requested or ():
        if name in actual_set:
            resolved[name] = name
            continue
        candidates = suffix_map.get(name, [])
        if len(candidates) == 1:
            resolved[name] = candidates[0]
        else:
            unresolved.append(name)
    return resolved, unresolved
