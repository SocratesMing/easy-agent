"""MCP 工具调用证据信封：为每次 MCP 调用附加来源 / 结果元数据。

设计目标（审计 FW-14）：MCP server 可能把错误包装成 ``isError=false`` 的文本，
模型无法区分「工具返回的事实」与「无依据内容」。本模块把 server / tool / 时间 /
错误 / 截断等证据作为 ToolMessage 内容前缀返回，不改工具名、description、
args_schema，也不改调用语义。

约定：

- 前缀格式 ``[MCP source: <server>/<tool>; fetched_at=<UTC ISO8601>; ...]``；
- 原始结果原样保留在前缀之后（文本块）或紧随前缀块（多模态块）；
- 工具抛异常时返回 ``is_error=true`` 的文本结果，不向 Agent 抛异常，
  错误原文完整保留（受同一长度上限约束）。

模块本身不依赖网络，可独立单测。
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool
from pydantic import Field

logger = logging.getLogger("easy-agent")

# 单次 MCP 结果进入模型上下文的文本上限（字符）。图片 / 文件等二进制块不截断。
DEFAULT_MAX_CONTENT_CHARS = 20000
_TRUNCATION_NOTICE = "\n...[MCP output truncated: {omitted} chars omitted]"

# 保守的「文本包装错误」启发式（FW-14）。只检查首个非空行，避免长文档中间
# 偶尔出现 error 字样就误判。
_ERROR_LINE_PATTERNS = (
    re.compile(r"^(?:mcp\s+)?error\s*[:：\-–—]", re.IGNORECASE),
    re.compile(
        r"^(?:error|exception)\s+(?:occurred|while|when|executing|in)\b",
        re.IGNORECASE,
    ),
    re.compile(r"^(?:failed|failure)\s+to\b", re.IGNORECASE),
    re.compile(r"^(?:failed|failure)\s*[:：\-–—]", re.IGNORECASE),
    re.compile(r"^traceback\s*\(most recent call last\)", re.IGNORECASE),
    re.compile(r"^(?:错误|失败|异常|报错)\s*[:：\-–—]"),
    re.compile(r"^(?:错误|失败|异常|报错)\b"),
)
_ERROR_STATUS_WORDS = frozenset({"error", "failed", "failure", "fail"})


def _safe_label(value: Any) -> str:
    """把 server / tool 名清洗成不会破坏前缀结构的单行标签。"""
    text = str(value if value is not None else "").replace("\r", " ")
    text = text.replace("\n", " ").replace("]", ")").replace(";", ",")
    return text.strip() or "unknown"


def _utc_now() -> str:
    """调用完成时间（UTC，秒级 ISO 8601）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class MCPEvidence:
    """一次 MCP 工具调用的证据信封。

    ``content`` 是截断后的原始结果（不含来源前缀）；``source_marker`` 渲染成
    模型可见的前缀；``render`` 返回「前缀 + 结果」的最终 ToolMessage 内容。
    """

    server: str
    tool: str
    ok: bool
    is_error: bool
    fetched_at: str
    truncated: bool
    content: Any
    error: str = ""
    error_from_text: bool = False

    def source_marker(self) -> str:
        marker = (
            f"[MCP source: {_safe_label(self.server)}/{_safe_label(self.tool)}; "
            f"fetched_at={self.fetched_at}; "
            f"ok={str(self.ok).lower()}; "
            f"truncated={str(self.truncated).lower()}"
        )
        if self.error_from_text:
            marker += "; error_source=text"
        # is_error 放在最后，保持 ``...; is_error=false]`` 这一稳定尾部。
        return marker + f"; is_error={str(self.is_error).lower()}]"

    def render(self) -> Any:
        return _prefix_content(self.content, self.source_marker())

    def to_dict(self) -> dict[str, Any]:
        return {
            "server": self.server,
            "tool": self.tool,
            "ok": self.ok,
            "is_error": self.is_error,
            "fetched_at": self.fetched_at,
            "truncated": self.truncated,
            "content": self.content,
            "error": self.error,
            "error_from_text": self.error_from_text,
        }


# ---------------------------------------------------------------------------
# 内容处理
# ---------------------------------------------------------------------------


def _content_limit() -> int:
    """当前文本上限；环境变量优先，异常配置回退到默认值。"""
    raw = os.getenv("EASY_MCP_MAX_CONTENT_CHARS", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            logger.warning(
                "EASY_MCP_MAX_CONTENT_CHARS=%r 不是整数，使用默认值 %s",
                raw,
                DEFAULT_MAX_CONTENT_CHARS,
            )
        else:
            if value > 0:
                return value
            logger.warning(
                "EASY_MCP_MAX_CONTENT_CHARS=%s 必须为正整数，使用默认值 %s",
                raw,
                DEFAULT_MAX_CONTENT_CHARS,
            )
    try:
        return max(1, int(DEFAULT_MAX_CONTENT_CHARS))
    except (TypeError, ValueError):
        return 20000


def _text_blocks(content: Any) -> list[str]:
    """提取内容中的文本块，用于错误启发式判断。"""
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                parts.append(item)
        return parts
    if isinstance(content, dict):
        if content.get("type") == "text":
            return [str(content.get("text", ""))]
        return []
    if content is None:
        return []
    return [str(content)]


def _content_to_text(content: Any) -> str:
    return "\n".join(_text_blocks(content))


def _looks_like_error_text(text: str) -> bool:
    """判断文本结果是否像 server 包装成成功的错误（保守启发式）。"""
    if not text or not text.strip():
        return False

    match = re.search(r"\S[^\n]*", text)
    if match is None:
        return False
    # 去掉常见 markdown 列表 / 引用前缀后再匹配
    first = match.group(0).strip().lstrip("#>*-–— \t")
    if not first:
        return False

    for pattern in _ERROR_LINE_PATTERNS:
        if pattern.match(first):
            return True

    if first.startswith("{"):
        try:
            data = json.loads(first)
        except (TypeError, ValueError):
            return False
        if isinstance(data, dict):
            if data.get("isError") is True or data.get("is_error") is True:
                return True
            if data.get("success") is False or data.get("ok") is False:
                return True
            if str(data.get("status", "")).lower() in _ERROR_STATUS_WORDS:
                return True
            if data.get("error"):
                return True
    return False


def _truncate_text(text: str, limit: int) -> tuple[str, bool]:
    if limit <= 0 or len(text) <= limit:
        return text, False
    omitted = len(text) - limit
    return text[:limit] + _TRUNCATION_NOTICE.format(omitted=omitted), True


def _truncate_content(content: Any, limit: int) -> tuple[Any, bool]:
    """截断文本内容；多模态列表只累计文本块，不破坏图片 / 文件块。"""
    if isinstance(content, str):
        return _truncate_text(content, limit)

    if isinstance(content, list):
        result: list[Any] = []
        remaining = limit
        omitted = 0
        truncated = False
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = str(item.get("text", ""))
                if remaining >= len(text):
                    result.append(item)
                    remaining -= len(text)
                else:
                    head = text[: max(remaining, 0)]
                    omitted += len(text) - len(head)
                    if head:
                        result.append({**item, "text": head})
                    remaining = 0
                    truncated = True
            elif isinstance(item, str):
                if remaining >= len(item):
                    result.append(item)
                    remaining -= len(item)
                else:
                    head = item[: max(remaining, 0)]
                    omitted += len(item) - len(head)
                    if head:
                        result.append(head)
                    remaining = 0
                    truncated = True
            else:
                result.append(item)
        if truncated:
            result.append(
                {"type": "text", "text": _TRUNCATION_NOTICE.format(omitted=omitted)}
            )
        return result, truncated

    text = "" if content is None else str(content)
    return _truncate_text(text, limit)


def _prefix_content(content: Any, marker: str) -> Any:
    """把来源前缀放到原始内容之前，尽量保留内容块结构。"""
    if isinstance(content, str):
        return f"{marker}\n{content}" if content else marker

    if isinstance(content, list):
        if all(isinstance(item, dict) for item in content):
            return [{"type": "text", "text": marker}, *content]
        if all(isinstance(item, str) for item in content):
            return [
                {"type": "text", "text": marker},
                *({"type": "text", "text": item} for item in content),
            ]
        return f"{marker}\n" + "\n".join(str(item) for item in content)

    if content is None:
        return marker
    return f"{marker}\n{content}"


# ---------------------------------------------------------------------------
# 结果解包 / 包装器
# ---------------------------------------------------------------------------


def _unwrap_result(result: Any) -> tuple[Any, Any, str | None]:
    """把原工具的返回值解包成 ``(content, artifact, status)``。"""
    if isinstance(result, ToolMessage):
        return result.content, result.artifact, getattr(result, "status", None)
    if isinstance(result, tuple) and len(result) == 2:
        return result[0], result[1], None
    return result, None, None


def _inner_input(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
    """从 BaseTool 解析后的参数还原传给原工具的输入。"""
    if kwargs and not args:
        return kwargs
    if args and not kwargs:
        return args[0] if len(args) == 1 else args
    if kwargs:
        return kwargs
    return {}


def _as_tool_call(inner: Any, payload: Any) -> Any:
    """给原工具一个标准 ToolCall，便于拿回 content + artifact + status。"""
    if isinstance(payload, dict):
        return {
            "type": "tool_call",
            "name": getattr(inner, "name", None) or "mcp_tool",
            "args": payload,
            "id": f"mcp-evidence-{uuid.uuid4().hex}",
        }
    return payload


class _MCPEvidenceTool(BaseTool):
    """透传原工具，只在结果前附加证据前缀。"""

    inner_tool: Any = Field(exclude=True, repr=False)
    server: str = "unknown"
    response_format: Literal["content", "content_and_artifact"] = "content"

    def _success_result(self, result: Any, fetched_at: str, limit: int) -> Any:
        # 极端情况下（如 interceptor 返回 LangGraph Command）不破坏控制流。
        if (
            not isinstance(result, (str, list, tuple, dict, ToolMessage))
            and hasattr(result, "goto")
        ):
            logger.warning(
                "MCP 工具 %s/%s 返回 Command，跳过证据前缀以保留控制流",
                self.server,
                self.name,
            )
            return result

        content, artifact, status = _unwrap_result(result)
        truncated_content, truncated = _truncate_content(content, limit)
        text = _content_to_text(truncated_content)
        status_error = status == "error"
        text_error = (not status_error) and _looks_like_error_text(text)
        is_error = status_error or text_error
        evidence = MCPEvidence(
            server=self.server,
            tool=self.name,
            ok=not is_error,
            is_error=is_error,
            fetched_at=fetched_at,
            truncated=truncated,
            content=truncated_content,
            error=text if text_error else "",
            error_from_text=text_error,
        )
        rendered = evidence.render()
        if self.response_format == "content_and_artifact":
            return rendered, artifact
        return rendered

    def _error_result(self, exc: Exception, fetched_at: str, limit: int) -> Any:
        message = str(exc).strip() or type(exc).__name__
        logger.warning(
            "MCP 工具调用失败 | %s/%s | %s: %s",
            self.server,
            self.name,
            type(exc).__name__,
            message,
        )
        truncated_content, truncated = _truncate_content(
            f"MCP tool call failed: {message}", limit
        )
        evidence = MCPEvidence(
            server=self.server,
            tool=self.name,
            ok=False,
            is_error=True,
            fetched_at=fetched_at,
            truncated=truncated,
            content=truncated_content,
            error=message,
            error_from_text=False,
        )
        rendered = evidence.render()
        if self.response_format == "content_and_artifact":
            return rendered, None
        return rendered

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        fetched_at = _utc_now()
        limit = _content_limit()
        payload = _inner_input(args, kwargs)
        try:
            result = self.inner_tool.invoke(_as_tool_call(self.inner_tool, payload))
        except Exception as exc:  # noqa: BLE001 - 工具错误要转成证据文本
            return self._error_result(exc, fetched_at, limit)
        return self._success_result(result, fetched_at, limit)

    async def _arun(self, *args: Any, **kwargs: Any) -> Any:
        fetched_at = _utc_now()
        limit = _content_limit()
        payload = _inner_input(args, kwargs)
        try:
            result = await self.inner_tool.ainvoke(_as_tool_call(self.inner_tool, payload))
        except Exception as exc:  # noqa: BLE001 - 工具错误要转成证据文本
            return self._error_result(exc, fetched_at, limit)
        return self._success_result(result, fetched_at, limit)


def wrap_mcp_tool(tool: Any, *, server: str) -> Any:
    """包装单个 MCP 工具；已包装过的工具原样返回（幂等）。

    保持 ``name`` / ``description`` / ``args_schema`` / ``response_format`` 不变；
    包装失败时退回原工具，避免因为证据层导致 MCP 工具整体不可用。
    """
    if isinstance(tool, _MCPEvidenceTool):
        if _safe_label(tool.server) != _safe_label(server):
            logger.warning(
                "MCP 工具 %s 已包装为 server=%s，忽略新的 server=%s",
                tool.name,
                tool.server,
                server,
            )
        return tool
    if tool is None:
        return tool

    name = getattr(tool, "name", None)
    if not isinstance(name, str) or not name:
        name = type(tool).__name__
    description = getattr(tool, "description", "") or ""
    if not isinstance(description, str):
        description = str(description)
    args_schema = getattr(tool, "args_schema", None)
    response_format = getattr(tool, "response_format", "content")
    if response_format not in ("content", "content_and_artifact"):
        response_format = "content"

    optional: dict[str, Any] = {}
    for attr in (
        "return_direct",
        "metadata",
        "tags",
        "extras",
        "handle_tool_error",
        "handle_validation_error",
    ):
        if hasattr(tool, attr):
            optional[attr] = getattr(tool, attr)

    try:
        return _MCPEvidenceTool(
            inner_tool=tool,
            server=str(server or "unknown"),
            name=name,
            description=description,
            args_schema=args_schema,
            response_format=response_format,
            **optional,
        )
    except Exception:  # noqa: BLE001 - 包装失败不应影响工具可用性
        logger.warning(
            "MCP 工具 %s 证据包装失败，退回原工具", name, exc_info=True
        )
        return tool


def wrap_mcp_tools(tools_by_server: Mapping[str, Iterable[Any]] | None) -> list[Any]:
    """按 server 映射批量包装，返回扁平工具列表（保持映射内顺序）。"""
    if not tools_by_server:
        return []
    wrapped: list[Any] = []
    for server, tools in tools_by_server.items():
        for tool in tools or []:
            wrapped.append(wrap_mcp_tool(tool, server=str(server)))
    return wrapped