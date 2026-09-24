"""知识库实现选择器：按 config.yaml 的 knowledge.implementation 统一导出 legacy/v2 符号。

可选值：
  - v2（默认）：easy_agent.knowledges，对接本地 Ragflow 0.26.3 标准 API
  - legacy：easy_agent.knowledge，旧版实现（已断开，仅保留代码可回退）

所有跨模块引用（app.py / api/chat.py / personnel/api.py / services/agent_manager.py）
一律从本模块导入，避免散落多处条件分支。切换实现只改 YAML，不改代码。
"""

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def resolve_knowledge_impl() -> str:
    """读取活动 YAML 的 knowledge.implementation，非法/缺省一律回退 v2（新版）。"""
    config_path = os.environ.get("EASY_CONFIG") or str(
        Path(__file__).resolve().parent / "config" / "config.yaml"
    )
    try:
        import yaml

        raw = yaml.safe_load(Path(config_path).read_text(encoding="utf-8")) or {}
        value = ((raw.get("knowledge") or {}).get("implementation") or "v2").strip()
        if value not in ("legacy", "v2"):
            logger.warning(f"knowledge.implementation 非法值 {value!r}，回退 v2")
            return "v2"
        return value
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"读取 knowledge.implementation 失败，回退 v2: {exc}")
        return "v2"


KNOWLEDGE_IMPL = resolve_knowledge_impl()

if KNOWLEDGE_IMPL == "v2":
    from .knowledges.api import router as knowledge_router
    from .knowledges.lifecycle import shutdown_knowledge, startup_knowledge
    from .knowledges.observability import (
        audit_metadata,
        metrics,
        resolve_request_id,
        should_audit,
    )
    from .knowledges.operations_repository import KnowledgeOperationsRepository
    from .knowledges.chat_bridge import (
        authorize_scoped_chat,
        empty_knowledge_context,
        is_knowledge_panel_title,
        knowledge_panel_system_prompt,
        prepare_knowledge_chat,
    )
    from .knowledges.streaming import knowledge_chat_stream_generator
    from .knowledges.host_streaming import chat_stream_generator
    from .knowledges.agent_extension import extend_agent_tools
else:
    from .knowledge.api import router as knowledge_router
    from .knowledge.lifecycle import shutdown_knowledge, startup_knowledge
    from .knowledge.observability import (
        audit_metadata,
        metrics,
        resolve_request_id,
        should_audit,
    )
    from .knowledge.operations_repository import KnowledgeOperationsRepository
    from .knowledge.chat_bridge import (
        authorize_scoped_chat,
        empty_knowledge_context,
        is_knowledge_panel_title,
        knowledge_panel_system_prompt,
        prepare_knowledge_chat,
    )
    from .knowledge.streaming import knowledge_chat_stream_generator
    from .knowledge.host_streaming import chat_stream_generator
    from .knowledge.agent_extension import extend_agent_tools

# 通用 ASGI 前缀改写器（与实现无关，始终复用 legacy 版本）。
from .knowledge.legacy_routes import LegacyHostRoutes  # noqa: E402

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
    "knowledge_panel_system_prompt",
    "knowledge_router",
    "metrics",
    "prepare_knowledge_chat",
    "resolve_request_id",
    "should_audit",
    "shutdown_knowledge",
    "startup_knowledge",
]
