"""知识库实现统一出口：从 knowledges（Ragflow 0.26.3 标准 API）导出全部符号。

所有跨模块引用（app.py / api/chat.py / knowledges/personnel/api.py / services/agent_manager.py）
一律从本模块导入，避免散落多处的直接包依赖。
"""

from easy_agent.knowledges.api import router as knowledge_router  # noqa: E402
from easy_agent.knowledges.runtime.source_sync import (  # noqa: E402
    run_source_sync,
    shutdown_source_sync,
    startup_source_sync,
)
from easy_agent.knowledges.runtime.lifecycle import shutdown_knowledge, startup_knowledge  # noqa: E402
from easy_agent.knowledges.runtime.observability import (  # noqa: E402
    audit_metadata,
    metrics,
    resolve_request_id,
    should_audit,
)
from easy_agent.knowledges.core.operations_repository import KnowledgeOperationsRepository  # noqa: E402
from easy_agent.knowledges.chat.chat_bridge import (  # noqa: E402
    authorize_scoped_chat,
    empty_knowledge_context,
    is_knowledge_panel_title,
    prepare_knowledge_chat,
)
from easy_agent.knowledges.chat.streaming import chat_stream_generator  # noqa: E402
from easy_agent.knowledges.chat.agent_extension import extend_agent_tools  # noqa: E402


class LegacyHostRoutes:
    """将 /api/<prefix> 路径改写为 /agent/<prefix> 的 ASGI 中间件（宿主兼容层）。"""

    PREFIXES = (
        "auth",
        "chat",
        "sessions",
        "files",
        "settings",
        "prompts",
        "skills",
        "scheduled-tasks",
        "terminal",
        "knowledge",
        "personnel",
    )

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] in {"http", "websocket"}:
            for prefix in self.PREFIXES:
                old = f"/api/{prefix}"
                if path == old or path.startswith(old + "/"):
                    scope = dict(scope)
                    scope["path"] = "/agent/" + path[5:]
                    if "raw_path" in scope:
                        scope["raw_path"] = scope["raw_path"].replace(
                            b"/api/", b"/agent/", 1
                        )
                    break
        await self.app(scope, receive, send)


__all__ = [
    "LegacyHostRoutes",
    "KnowledgeOperationsRepository",
    "audit_metadata",
    "authorize_scoped_chat",
    "chat_stream_generator",
    "empty_knowledge_context",
    "extend_agent_tools",
    "is_knowledge_panel_title",
    "knowledge_router",
    "metrics",
    "prepare_knowledge_chat",
    "resolve_request_id",
    "run_source_sync",
    "should_audit",
    "shutdown_knowledge",
    "shutdown_source_sync",
    "startup_knowledge",
    "startup_source_sync",
]
