"""宿主能力端口：知识域不反向依赖 ``api.chat`` / ``services``（A1）。

知识模块（``knowledges.api``、``knowledges.chat.streaming``）需要复用主聊天
链路的三类宿主能力：

- ``api.chat.chat_stream``：知识面板发问；
- ``services.streaming.chat_stream_generator``：主聊天流的知识适配包装；
- ``services`` 的取消/清理/配置读取：``cancel_stream_task`` /
  ``remove_session_agent`` / ``get_agent_config``。

改造前这些能力靠 ``knowledges.api`` 反向 import 宿主模块取得，形成
``knowledges.api → api.chat → knowledge_impl → knowledges.api`` 的强连通环。
现在改为**端口 + 装配根注册**：宿主在 ``easy_agent/api/chat.py``（app 启动
必经路径）把真实实现注册进来，知识域只按端口取用。

本模块顶层**不 import 任何包内模块**，是 import 图的叶子节点，因此不会扩大
SCC。未注册时 fail-closed（``RuntimeError``，端点转 503），不会在 import 阶段
制造新的反向依赖。

设计文档：``docs/archive/design/2026-10-18-a1-knowledge-streaming.md``。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class HostCapabilities:
    """宿主提供给知识域的最小能力集合（均为真实实现的可调用引用）。"""

    chat_stream: Callable[..., Any]
    host_chat_stream_generator: Callable[..., Any]
    cancel_stream_task: Callable[..., Any]
    remove_session_agent: Callable[..., Any]
    get_agent_config: Callable[..., Any]


_host_capabilities: HostCapabilities | None = None


def register_host_capabilities(
    *,
    chat_stream: Callable[..., Any],
    host_chat_stream_generator: Callable[..., Any],
    cancel_stream_task: Callable[..., Any],
    remove_session_agent: Callable[..., Any],
    get_agent_config: Callable[..., Any],
) -> HostCapabilities:
    """注册宿主能力；重复调用会覆盖旧值（测试注入假实现后需恢复）。

    非可调用对象一律 ``TypeError``：端口拼错时应在启动阶段暴露，而不是在
    用户发问时静默 500。
    """

    values = {
        "chat_stream": chat_stream,
        "host_chat_stream_generator": host_chat_stream_generator,
        "cancel_stream_task": cancel_stream_task,
        "remove_session_agent": remove_session_agent,
        "get_agent_config": get_agent_config,
    }
    for name, value in values.items():
        if not callable(value):
            raise TypeError(f"宿主能力 {name} 必须是可调用对象，实际为 {type(value)!r}")

    global _host_capabilities
    _host_capabilities = HostCapabilities(**values)
    return _host_capabilities


def is_host_capabilities_registered() -> bool:
    """宿主能力是否已注册（诊断/测试用）。"""

    return _host_capabilities is not None


def get_host_capabilities() -> HostCapabilities:
    """取宿主能力；未注册时 fail-closed。"""

    if _host_capabilities is None:
        raise RuntimeError(
            "宿主聊天能力尚未注册：请先 import easy_agent.api.chat（应用启动路径），"
            "或显式调用 register_host_capabilities()"
        )
    return _host_capabilities


__all__ = [
    "HostCapabilities",
    "get_host_capabilities",
    "is_host_capabilities_registered",
    "register_host_capabilities",
]