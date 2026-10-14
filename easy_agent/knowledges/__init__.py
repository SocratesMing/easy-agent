"""知识库模块（按前端实际调用面精简）。

基础层 core：config / models / schema / repository / operations_repository /
file_validation；聊天与 Agent 集成层 chat：chat_bridge / streaming /
agent_extension / original_tool；运行时层 runtime：lifecycle / worker /
observability / catalog_sync；客户端 ragflow（Ragflow v0.26.3 标准 HTTP
API）；服务与接口层 service / api；脚本 scripts。
"""

from .api import router
from .runtime.lifecycle import shutdown_knowledge, startup_knowledge
from .runtime.observability import audit_metadata, metrics, resolve_request_id, should_audit
from .core.operations_repository import KnowledgeOperationsRepository

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
