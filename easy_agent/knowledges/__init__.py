"""新版知识库模块（功能对齐旧版 knowledge，代码精简规整）。

基础层：config / models / schema / repository / auth / file_validation；
客户端：ragflow（行内契约 34 方法）；服务与接口层：service / api /
worker / operations_repository / ops_api / chat_bridge / agent_extension /
original_tool / observability / lifecycle。
"""

from .api import router
from .lifecycle import shutdown_knowledge, startup_knowledge
from .observability import audit_metadata, metrics, resolve_request_id, should_audit
from .operations_repository import KnowledgeOperationsRepository
from .ops_api import router as ops_router

__all__ = [
    "KnowledgeOperationsRepository",
    "audit_metadata",
    "metrics",
    "ops_router",
    "resolve_request_id",
    "router",
    "should_audit",
    "shutdown_knowledge",
    "startup_knowledge",
]
