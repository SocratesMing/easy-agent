"""知识库模块（按前端实际调用面精简）。

基础层 core：config / models / schema / repository / operations_repository /
file_validation；聊天与 Agent 集成层 chat：chat_bridge / streaming /
agent_extension / original_tool；运行时层 runtime：lifecycle / worker /
observability / catalog_sync；客户端 ragflow（Ragflow v0.26.3 标准 HTTP
API）；服务与接口层 service / api；脚本 scripts。
"""

from easy_agent.knowledges.api import router

# 导入即向 db.schema_registry 注册 knowledge 建表函数（见 db/schema_registry.py）。
from easy_agent.knowledges.core import schema as _schema  # noqa: F401,E402
from easy_agent.knowledges.runtime.lifecycle import shutdown_knowledge, startup_knowledge
from easy_agent.knowledges.runtime.observability import audit_metadata, metrics, resolve_request_id, should_audit
from easy_agent.knowledges.core.operations_repository import KnowledgeOperationsRepository

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
