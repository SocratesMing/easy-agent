"""Chat service package - agent management and streaming responses"""

from .agent_manager import (
    init_agent_config,
    get_or_create_agent_for_session,
    remove_session_agent,
    get_agent_config,
)
from .streaming import (
    chat_stream_generator,
    compress_context,
    build_context_messages,
)

__all__ = [
    "init_agent_config",
    "get_or_create_agent_for_session",
    "remove_session_agent",
    "get_agent_config",
    "chat_stream_generator",
    "compress_context",
    "build_context_messages",
]
