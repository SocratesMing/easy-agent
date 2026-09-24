"""知识库实现统一出口：从 knowledges（v2，Ragflow 0.26.3 标准 API）导出全部符号。

所有跨模块引用（app.py / api/chat.py / personnel/api.py / services/agent_manager.py）
一律从本模块导入，避免散落多处的直接包依赖。旧版 easy_agent/knowledge 已移除，
config.yaml 的 knowledge.implementation 键保留但仅作日志提示（值仅支持 v2）。
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def resolve_knowledge_impl() -> str:
    """读取活动 YAML 的 knowledge.implementation；当前仅支持 v2，其他值告警回退。"""
    config_path = os.environ.get("EASY_CONFIG") or str(
        Path(__file__).resolve().parent / "config" / "config.yaml"
    )
    try:
        import yaml

        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        value = ((raw.get("knowledge") or {}).get("implementation") or "v2").strip()
        if value != "v2":
            logger.warning(
                f"knowledge.implementation={value!r} 不再受支持（旧版已移除），使用 v2"
            )
        return "v2"
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"读取 knowledge.implementation 失败，使用 v2: {exc}")
        return "v2"


KNOWLEDGE_IMPL = resolve_knowledge_impl()

from .knowledges.api import router as knowledge_router  # noqa: E402
from .knowledges.lifecycle import shutdown_knowledge, startup_knowledge  # noqa: E402
from .knowledges.observability import (  # noqa: E402
    audit_metadata,
    metrics,
    resolve_request_id,
    should_audit,
)
from .knowledges.operations_repository import KnowledgeOperationsRepository  # noqa: E402
from .knowledges.chat_bridge import (  # noqa: E402
    authorize_scoped_chat,
    empty_knowledge_context,
    is_knowledge_panel_title,
    knowledge_panel_system_prompt,
    prepare_knowledge_chat,
)
from .knowledges.streaming import knowledge_chat_stream_generator  # noqa: E402
from .knowledges.host_streaming import chat_stream_generator  # noqa: E402
from .knowledges.agent_extension import extend_agent_tools  # noqa: E402


class LegacyHostRoutes:
    """将旧 /api/<prefix> 路径改写为 /agent/<prefix> 的 ASGI 中间件（宿主兼容层）。"""

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
    "KNOWLEDGE_IMPL",
    "LegacyHostRoutes",
    "KnowledgeOperationsRepository",
    "audit_metadata",
    "authorize_scoped_chat",
    "chat_stream_generator",
    "empty_knowledge_context",
    "extend_agent_tools",
    "is_knowledge_panel_title",
    "knowledge_chat_stream_generator",
    "knowledge_router",
    "knowledge_panel_system_prompt",
    "metrics",
    "prepare_knowledge_chat",
    "resolve_request_id",
    "should_audit",
    "shutdown_knowledge",
    "startup_knowledge",
]
