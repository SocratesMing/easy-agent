"""业务服务包：会话 Agent 缓存、连接器、SSE 流式编排、流事件、记忆、MCP 与定时任务等。

包级重导出路由层常用的入口函数。
"""

from easy_agent.services.agent_manager import (
    init_agent_config,
    get_or_create_agent_for_session,
    remove_session_agent,
    register_stream_task,
    unregister_stream_task,
    is_session_streaming,
    cancel_stream_task,
    drain_stream_tasks,
    get_agent_config,
    invalidate_user_agents,
    get_session_expert,
    get_session_connectors,
    set_session_connectors,
    clear_session_connectors,
    set_session_edit_target,
    get_session_edit_target,
)
from easy_agent.services.connectors import (
    parse_connector_directives,
    parse_message_connectors,
    resolve_connector_servers,
)
from easy_agent.services.streaming.orchestrator import (
    chat_stream_generator,
    resume_stream_generator,
)
from easy_agent.services.stream_events import StreamEventWriter, flush_all_stream_events
from easy_agent.services.scheduler import (
    init_scheduler,
    get_scheduler,
    shutdown_scheduler,
    register_scheduled_task,
    unregister_scheduled_task,
    reload_all_tasks,
)

__all__ = [
    "init_agent_config",
    "get_or_create_agent_for_session",
    "remove_session_agent",
    "register_stream_task",
    "unregister_stream_task",
    "is_session_streaming",
    "cancel_stream_task",
    "drain_stream_tasks",
    "get_agent_config",
    "invalidate_user_agents",
    "get_session_expert",
    "get_session_connectors",
    "set_session_connectors",
    "clear_session_connectors",
    "set_session_edit_target",
    "get_session_edit_target",
    "parse_message_connectors",
    "parse_connector_directives",
    "resolve_connector_servers",
    "chat_stream_generator",
    "resume_stream_generator",
    "StreamEventWriter",
    "flush_all_stream_events",
    "init_scheduler",
    "get_scheduler",
    "shutdown_scheduler",
    "register_scheduled_task",
    "unregister_scheduled_task",
    "reload_all_tasks",
]
