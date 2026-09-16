"""Stable failures raised by the RAGFlow HTTP boundary."""

from __future__ import annotations


class RagflowError(RuntimeError):
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
    def __init__(self, message: str = "RAGFlow authentication failed"):
        super().__init__(message, code="RAGFLOW_AUTHENTICATION_FAILED")


class RagflowUnavailableError(RagflowError):
    def __init__(self, message: str = "RAGFlow is temporarily unavailable"):
        super().__init__(
            message, code="RAGFLOW_UNAVAILABLE", retryable=True
        )


class RagflowContractError(RagflowError):
    def __init__(self, message: str = "RAGFlow response contract mismatch"):
        super().__init__(message, code="RAGFLOW_CONTRACT_MISMATCH")


__all__ = [
    "RagflowAuthenticationError",
    "RagflowContractError",
    "RagflowError",
    "RagflowUnavailableError",
]
