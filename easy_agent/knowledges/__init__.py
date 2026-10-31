"""知识库模块（按前端实际调用面精简）。

基础层 core：config / models / schema / repository / operations_repository /
file_validation / chunks；聊天与 Agent 集成层 chat：chat_bridge / streaming /
agent_extension / original_tool；运行时层 runtime：lifecycle / worker /
observability / source_sync；客户端 ragflow（Ragflow v0.26.3 标准 HTTP
API）；服务与接口层 service / api；脚本 scripts；人员子包 personnel
（知识库域的人员管理 / Excel 导入 / 用户扩展字段，HTTP 前缀
``/agent/personnel``）。

``personnel`` 不在此 import：它依赖 ``knowledges.core``，由 ``app.py`` 显式
导入（导入即注册默认管理员策略与用户扩展字段读写）。
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
