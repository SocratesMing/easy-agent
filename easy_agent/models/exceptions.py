"""领域异常：数据层 / 服务层只抛这些异常，不依赖 FastAPI（T11）。

分层约定（L0）：``easy_agent.models`` 不 import 任何上层模块，也不 import web
框架。数据库层（``easy_agent/db``）、服务层（``easy_agent/services``）遇到「资源
不存在 / 状态冲突 / 参数不合法」时抛这里的异常，**不再直接抛 ``HTTPException``**，
这样同一条业务逻辑可以被 HTTP 路由、后台任务、CLI 复用，不必为了非 HTTP 调用方
构造一个带状态码的 web 异常。

映射到 HTTP 的职责在 ``easy_agent/app.py`` 的异常处理器里（见
``_DOMAIN_ERROR_STATUS``）：``NotFoundError`` → 404、``ConflictError`` → 409、
``BadRequestError`` → 400，响应体与 ``StarletteHTTPException`` 处理器完全一致
（``{"detail": ..., "code": "HTTP_<status>", "request_id": ...}``），因此除
「异常类型」外，HTTP 层行为与迁移前完全一致。

契约约束：``message`` 是**面向用户的文案**，路由层 / 处理器不得改写；迁移既有
``HTTPException`` 时文案必须逐字保留，否则前端提示语会变。
"""

from __future__ import annotations


class DomainError(Exception):
    """领域异常基类：只携带一条面向用户的中文提示。

    Attributes:
        message: 面向用户的错误文案（HTTP 响应体的 ``detail``）。
    """

    def __init__(self, message: str = "") -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message


class NotFoundError(DomainError):
    """资源不存在（HTTP 映射 404）。

    会话场景注意：**「不存在」与「不属于当前用户」都用它**，调用方不应暴露两者
    的区别，避免通过状态码 / 文案泄露资源是否存在。
    """


class ConflictError(DomainError):
    """与资源当前状态冲突（HTTP 映射 409），如重名、并发抢占。"""


class BadRequestError(DomainError):
    """请求不满足业务前置条件（HTTP 映射 400）。

    与 ``ConflictError`` 的分工：400 是「这次请求本身就不该发」，409 是「请求本身
    合法，但和服务器当前状态撞了」。既有代码里 400 的文案 / 语义保持不变，故保留
    这一类型（设计文档只列了前两个，这里按「不改 HTTP 状态码」的约束补齐）。
    """


__all__ = [
    "BadRequestError",
    "ConflictError",
    "DomainError",
    "NotFoundError",
]
