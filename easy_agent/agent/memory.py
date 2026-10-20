"""每次运行都重新加载记忆源的 MemoryMiddleware（DA-03 P0）。

deepagents 的 ``MemoryMiddleware`` 在 ``before_agent`` 里只要 state 已有
``memory_contents`` 就跳过加载，即每个 thread 只读一次记忆。本项目的记忆源每轮都可能变：

- 会话记忆 ``/workspace/memory.md``：每轮结束后由后台任务改写（``streaming/side_effects.py``）；
- 长期记忆 ``/memories/AGENTS.md``：其他会话也会写；
- 上传清单 ``/workspace/uploads/index.md``：随上传变化。

每消息一个 thread 时每轮都是新 thread，自然每轮重读；改成会话级 thread 后就会停在第一轮
（``docs/audit/evidence-2026-10-19-deepagents/exp_session_thread.py`` 实测）。
``memory_contents`` 是私有状态，不能经运行输入重置，所以这里重写加载钩子：每次运行都忽略
已缓存的内容重新读取，其余逻辑（注入 system prompt、prompt cache 断点）原样继承。

``name`` 与框架默认项相同，经 ``create_deep_agent(middleware=...)`` 原位替换默认实例。
"""

from __future__ import annotations

from typing import Any

from deepagents.middleware.memory import MemoryMiddleware

_MEMORY_CONTENTS_KEY = "memory_contents"


def _without_cached_memory(state: Any) -> dict:
    return {key: value for key, value in state.items() if key != _MEMORY_CONTENTS_KEY}


class SessionMemoryMiddleware(MemoryMiddleware):
    """每次运行都重新读取记忆源（框架默认每个 thread 只读一次）。"""

    @property
    def name(self) -> str:
        return "MemoryMiddleware"

    def before_agent(self, state, runtime, config):
        return super().before_agent(_without_cached_memory(state), runtime, config)

    async def abefore_agent(self, state, runtime, config):
        return await super().abefore_agent(_without_cached_memory(state), runtime, config)
