"""MCP 工具错误契约（FW-14）。

约定：

- 业务工具把执行失败转成 :class:`~mcp.server.fastmcp.exceptions.ToolError`，
  由 FastMCP 输出 ``isError=true``，客户端不会把失败当成功；
- 对外文案只包含稳定的错误码 + 人类可读说明，**不含**异常类型/repr、堆栈、
  连接串、密码、API Key 等敏感信息；
- 原始异常只写服务端日志，写之前先做密钥脱敏与单行化，避免日志注入。

错误码使用 ``snake_case``，形如 ``market_quotes_unavailable``；客户端可据此做
分支处理，文案可随时调整而不破坏协议。
"""

from __future__ import annotations

import logging
import os
import re

from mcp.server.fastmcp.exceptions import ToolError

logger = logging.getLogger("easy-mcp-server")

# key=value / key: value 形式的敏感字段（覆盖 DSN 查询串与常见配置写法）
_SECRET_FIELD_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)"
    r"(\s*[=:]\s*)(\S+)"
)
# Authorization: Bearer <token>（单独处理，避免把 Bearer 前缀当成密钥值）
_BEARER_RE = re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]+")

_MAX_LOG_CHARS = 500


def redact(text: str) -> str:
    """把异常文本整理成可安全写日志的单行字符串。

    仅用于服务端日志；返回给客户端的错误文案由调用方显式给出，不经过这里。
    """
    cleaned = " ".join((text or "").split())
    cleaned = _SECRET_FIELD_RE.sub(r"\1\2***", cleaned)
    cleaned = _BEARER_RE.sub(r"\1***", cleaned)

    # 环境变量里的真实密码可能不带 key 前缀地出现在驱动报错中
    password = os.environ.get("MYSQL_PASSWORD", "")
    if len(password) >= 4:
        cleaned = cleaned.replace(password, "***")

    if len(cleaned) > _MAX_LOG_CHARS:
        cleaned = cleaned[:_MAX_LOG_CHARS] + "...[truncated]"
    return cleaned


def tool_failure(
    code: str,
    message: str,
    exc: BaseException,
    *,
    tool: str,
) -> ToolError:
    """记录脱敏后的原始异常，并构造对外可见的 :class:`ToolError`。

    返回异常对象而非直接 ``raise``，调用方用 ``raise ... from None`` 抛出，
    避免把原始异常链带进 FastMCP 生成的响应文本。
    """
    logger.error(
        "MCP 工具 %s 执行失败 [%s]: %s: %s",
        tool,
        code,
        type(exc).__name__,
        redact(str(exc)),
    )
    return ToolError(f"{code}: {message}")
