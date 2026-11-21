"""领域异常。数据层和服务层只抛这些，不依赖 FastAPI。

``easy_agent.models`` 不 import 上层模块，也不 import web 框架。
HTTP 映射在 ``app.py`` 的 ``_DOMAIN_ERROR_STATUS``。
``message`` 是给用户看的文案，路由和处理器不要改写。
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


class UnprocessableError(DomainError):
    """请求语义或参数在当前配置下不可处理（HTTP 映射 422）。"""


class BadGatewayError(DomainError):
    """上游模型或外部依赖执行失败（HTTP 映射 502）。"""


class GatewayTimeoutError(DomainError):
    """上游模型或外部依赖执行超时（HTTP 映射 504）。"""


__all__ = [
    "BadGatewayError",
    "BadRequestError",
    "ConflictError",
    "DomainError",
    "GatewayTimeoutError",
    "NotFoundError",
    "UnprocessableError",
]
