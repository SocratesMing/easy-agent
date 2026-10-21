"""C2 兼容性测试：旧 ``easy_agent.services.streaming`` 导入面必须完整。"""

from pathlib import Path

from easy_agent.services import sse, stream_messages
from easy_agent.services.streaming import persistence
from easy_agent.services.streaming import side_effects


LEGACY_SYMBOLS = [
    # 原模块自有函数/常量
    "_extract_file_paths_from_command",
    "_MODEL_CONTEXT_LIMITS",
    "_get_model_context_limit",
    "_persist_incremental",
    "_persist_thinking_event",
    "_mark_hitl_pending",
    "_maybe_rename_workspace",
    "_resolve_recorded_path",
    "_drop_generated_records",
    "_register_session_skills",
    "_auto_assign_skill_category",
    "_submit_memory_updates",
    "_MEMORY_UPDATE_EXECUTOR",
    "_assistant_content_message",
    "_historical_tool_records",
    "_partial_message_dict",
    "_tool_call_tuples",
    "build_assistant_message_dict",
    "build_context_messages",
    "tool_call_records_to_dicts",
    "chat_stream_generator",
    "resume_stream_generator",
    "logger",
    # 原模块重导出的依赖
    "SSEEvent",
    "format_sse",
    "StreamProcessor",
    "EasyAgent",
    "Database",
    "ChatRequest",
    "SessionLogger",
    "resolve_context_length",
    "get_agent_config",
    "env_bool",
    "AIMessage",
    "HumanMessage",
    "ToolMessage",
    "Command",
    "AsyncGenerator",
    "ThreadPoolExecutor",
    "datetime",
    "Path",
    "_extract_hitl_request",
    "_hitl_action_names",
    "_hitl_interrupt_items",
    "_hitl_review_config_map",
    "hanging_hitl_tool_count",
    "normalize_resume_decisions",
    # 新分层新增的公开符号
    "IncrementalPersister",
    "ResumeSnapshot",
    "mark_hitl_pending",
    "persist_incremental",
    "register_session_skills",
    "submit_memory_updates",
    "resolve_chat_context",
    "pump_stream",
    "HitlInterruption",
    "finalize_stream",
]


def test_streaming_exposes_all_legacy_and_new_symbols():
    import easy_agent.services.streaming as streaming

    missing = [name for name in LEGACY_SYMBOLS if not hasattr(streaming, name)]
    assert missing == []


def test_streaming_reexports_same_objects():
    import easy_agent.services.streaming as streaming
    from easy_agent.services.streaming import orchestrator

    assert streaming.format_sse is sse.format_sse
    assert streaming.SSEEvent is sse.SSEEvent
    assert streaming.build_context_messages is stream_messages.build_context_messages
    assert (
        streaming.build_assistant_message_dict
        is stream_messages.build_assistant_message_dict
    )
    assert streaming._persist_incremental is persistence.persist_incremental
    assert (
        streaming._MEMORY_UPDATE_EXECUTOR
        is side_effects._MEMORY_UPDATE_EXECUTOR
    )
    assert (
        streaming.chat_stream_generator
        is orchestrator.chat_stream_generator
    )
    assert (
        streaming.resume_stream_generator
        is orchestrator.resume_stream_generator
    )
    assert streaming.finalize_stream is orchestrator.finalize_stream


def test_streaming_is_package_and_py_shim_remains():
    import easy_agent.services.streaming as streaming

    # 运行时解析到包；同目录 streaming.py 作为按路径加载的垫片保留。
    assert hasattr(streaming, "__path__")
    assert Path(streaming.__file__).name == "__init__.py"
    shim = Path(streaming.__file__).parent.parent / "streaming.py"
    assert shim.is_file()
    assert len(shim.read_text(encoding="utf-8").splitlines()) < 40
