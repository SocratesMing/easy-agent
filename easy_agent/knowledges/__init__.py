"""新版知识库模块（v2，按前端实际调用面精简）。

基础层：config / models / schema / repository / file_validation；客户端：
ragflow（Ragflow v0.26.3 标准 HTTP API）；服务与接口层：service / api /
worker / operations_repository / chat_bridge / agent_extension /
original_tool / observability / lifecycle。
"""

from .api import router
from .lifecycle import shutdown_knowledge, startup_knowledge
from .observability import audit_metadata, metrics, resolve_request_id, should_audit
from .operations_repository import KnowledgeOperationsRepository

__all__ = [
    "KnowledgeOperationsRepository",
    "audit_metadata",
    "metrics",
    "resolve_request_id",
    "router",
    "should_audit",
    "shutdown_knowledge",
    "startup_knowledge",
]
