"""知识服务对外契约：业务异常与文档二进制响应封装。

API 层只依赖本模块的错误码、HTTP 状态码与可重试标记；调整对外错误语义时
只需修改这里，不必触碰业务实现。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator


@dataclass(frozen=True)
class KnowledgeDocumentBinary:
    """文档二进制内容封装：供响应层按区间分段读取。"""

    size_bytes: int
    content_type: str
    sha256: str | None
    iterator_factory: Callable[[int, int | None], Iterator[bytes]]

    def iter_bytes(self, start: int = 0, end: int | None = None) -> Iterator[bytes]:
        return self.iterator_factory(start, end)


class KnowledgeServiceError(RuntimeError):
    """知识服务业务异常：携带错误码、HTTP 状态码与可重试标记。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


def _not_found(message: str = "知识资源不存在") -> KnowledgeServiceError:
    """构造 404 业务错误：不区分「无权」与「不存在」，避免资源存在性泄露。"""

    return KnowledgeServiceError("KNOWLEDGE_NOT_FOUND", message, status_code=404)


def _folder_missing_error(message: str = "文件夹不存在") -> KnowledgeServiceError:
    return KnowledgeServiceError("KNOWLEDGE_FOLDER_NOT_FOUND", message, status_code=404)


def _folder_depth_error() -> KnowledgeServiceError:
    return KnowledgeServiceError(
        "KNOWLEDGE_FOLDER_DEPTH_LIMIT",
        "文件夹最多支持 5 层",
        status_code=409,
    )
