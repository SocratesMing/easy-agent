"""流式服务包（C2 拆分）：兼容门面 + 模块边界。

运行时 ``easy_agent.services.streaming`` 指向本包（Python 同目录下包优先于
``streaming.py`` 文件），所有历史 ``from easy_agent.services.streaming import X``
仍可用：

- ``projector``：纯投影函数；
- ``persistence``：增量/最终落库；
- ``side_effects``：工作区/技能/记忆收尾；
- ``orchestrator``：chat/resume 编排与公开生成器。

``orchestrator`` / ``side_effects`` 采用 PEP 562 惰性重导出：包 ``__init__``
不静态 import 它们，避免 ``services.streaming`` 在 import-graph 中新增
SCC 成员（现有 16 模块环已白名单冻结，白名单只减不增）。首次访问
``chat_stream_generator`` 等名字时才加载编排器，运行期依赖关系与拆分前一致。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import AsyncGenerator

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from easy_agent.agent import EasyAgent
from easy_agent.db import Database
from easy_agent.bootstrap.env import env_bool
from easy_agent.agent.model import resolve_context_length
from easy_agent.models.api import ChatRequest
from easy_agent.utils.session_logger import SessionLogger
from easy_agent.services.agent_manager import get_agent_config
from easy_agent.services.hitl_state import (
    _extract_hitl_request,
    _hitl_action_names,
    _hitl_interrupt_items,
    _hitl_review_config_map,
    hanging_hitl_tool_count,
    normalize_resume_decisions,
)
from easy_agent.services.sse import SSE_EVENT_TYPES, SSEEvent, format_sse
from easy_agent.services.stream_messages import (
    _assistant_content_message,
    _historical_tool_records,
    _partial_message_dict,
    _tool_call_tuples,
    build_assistant_message_dict,
    build_context_messages,
    tool_call_records_to_dicts,
)
from easy_agent.services.stream_processor import StreamProcessor
from easy_agent.services.streaming.persistence import (
    IncrementalPersister,
    _handle_tool_result_event,
    _persist_incremental,
    _persist_thinking_event,
    persist_approval_decisions,
    persist_final_message,
    persist_incremental,
    persist_partial,
    persist_thinking_event,
    persist_todos,
    persist_tool_result_event,
    write_session_context_snapshot,
)
from easy_agent.services.streaming.projector import (
    ResumeSnapshot,
    _MODEL_CONTEXT_LIMITS,
    apply_decision_status_to_blocks,
    apply_decision_status_to_records,
    build_action_requests_payload,
    build_usage_payload,
    decision_status_map,
    extract_file_paths_from_command,
    finalize_blocks,
    get_model_context_limit,
    last_user_message,
    load_resume_snapshot,
    mark_hitl_pending,
    merge_tool_call_records,
    pending_tool_call_ids_for_approval,
    thinking_duration,
)

logger = logging.getLogger("easy_agent.chat_service")

# 旧 ``streaming.py`` 里的私有别名（语义完全一致，仅改名）。
_extract_file_paths_from_command = extract_file_paths_from_command
_mark_hitl_pending = mark_hitl_pending
_get_model_context_limit = get_model_context_limit

# 惰性重导出：name -> (子模块, 子模块内属性名)。
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    # orchestrator
    "chat_stream_generator": ("orchestrator", "chat_stream_generator"),
    "resume_stream_generator": ("orchestrator", "resume_stream_generator"),
    "HitlInterruption": ("orchestrator", "HitlInterruption"),
    "build_resume_command": ("orchestrator", "build_resume_command"),
    "detect_hitl_interruption": ("orchestrator", "detect_hitl_interruption"),
    "finalize_stream": ("orchestrator", "finalize_stream"),
    "pump_stream": ("orchestrator", "pump_stream"),
    "resolve_chat_context": ("orchestrator", "resolve_chat_context"),
    "resolve_resume_context": ("orchestrator", "resolve_resume_context"),
    # side_effects（旧私有名 -> 新公开名）
    "_MEMORY_UPDATE_EXECUTOR": ("side_effects", "_MEMORY_UPDATE_EXECUTOR"),
    "_auto_assign_skill_category": ("side_effects", "auto_assign_skill_category"),
    "_drop_generated_records": ("side_effects", "drop_generated_records"),
    "_maybe_rename_workspace": ("side_effects", "maybe_rename_workspace"),
    "_register_session_skills": ("side_effects", "register_session_skills"),
    "_resolve_recorded_path": ("side_effects", "resolve_recorded_path"),
    "_submit_memory_updates": ("side_effects", "submit_memory_updates"),
    "auto_assign_skill_category": ("side_effects", "auto_assign_skill_category"),
    "drop_generated_records": ("side_effects", "drop_generated_records"),
    "maybe_rename_workspace": ("side_effects", "maybe_rename_workspace"),
    "register_session_skills": ("side_effects", "register_session_skills"),
    "resolve_recorded_path": ("side_effects", "resolve_recorded_path"),
    "submit_memory_updates": ("side_effects", "submit_memory_updates"),
}


def __getattr__(name: str):
    """惰性加载 orchestrator / side_effects（PEP 562）。"""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    module = import_module(f"{__name__}.{module_name}")
    value = getattr(module, attribute)
    globals()[name] = value  # 缓存，后续访问不再触发 __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))
