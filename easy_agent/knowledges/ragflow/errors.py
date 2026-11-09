"""RAGFlow HTTP 边界抛出的稳定异常体系。

服务层只应捕获本模块中的异常类型；HTTP 状态码与响应信封的细节
在客户端内部统一转译，不向外泄露。
"""

from __future__ import annotations


class RagflowError(RuntimeError):
    """RAGFlow 请求失败基类。

    属性:
        code: 稳定错误码，供服务层分类处理。
        retryable: 该错误是否值得重试。
        http_status: 触发错误的 HTTP 状态码（信封错误时通常为 200）。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "RAGFLOW_ERROR",
        retryable: bool = False,
        http_status: int | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.http_status = http_status


class RagflowAuthenticationError(RagflowError):
    """认证失败：API Key 无效、过期或无权限（信封 code=109 / HTTP 401、403）。"""

    def __init__(self, message: str = "RAGFlow 认证失败：API Key 无效或无权限"):
        super().__init__(message, code="RAGFLOW_AUTHENTICATION_FAILED")


class RagflowNotFoundError(RagflowError):
    """目标资源不存在或当前用户无权访问。"""

    def __init__(self, message: str = "RAGFlow 目标资源不存在"):
        super().__init__(message, code="RAGFLOW_NOT_FOUND")


class RagflowRateLimitError(RagflowError):
    """触发统一流量限制（HTTP 429），可稍后重试。"""

    def __init__(self, message: str = "RAGFlow 请求被限流"):
        super().__init__(message, code="RAGFLOW_RATE_LIMITED", retryable=True)


class RagflowUnavailableError(RagflowError):
    """RAGFlow 服务暂不可用：网络故障、超时或 HTTP 5xx。"""

    def __init__(self, message: str = "RAGFlow 服务暂不可用"):
        super().__init__(message, code="RAGFLOW_UNAVAILABLE", retryable=True)


class RagflowContractError(RagflowError):
    """RAGFlow 响应与契约不符：非 JSON 响应、信封缺失、结构错误等。"""

    def __init__(self, message: str = "RAGFlow 响应与契约不符"):
        super().__init__(message, code="RAGFLOW_CONTRACT_MISMATCH")


__all__ = [
    "RagflowAuthenticationError",
    "RagflowContractError",
    "RagflowError",
    "RagflowNotFoundError",
    "RagflowRateLimitError",
    "RagflowUnavailableError",
]
