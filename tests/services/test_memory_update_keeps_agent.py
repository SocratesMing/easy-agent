"""记忆更新后不再驱逐会话 Agent。

记忆每次运行都由 ``SessionMemoryMiddleware`` 重新加载，不需要重建 Agent。改造前每轮记忆写入后
都会 ``remove_session_agent``：下一轮重新加载 MCP 工具、重建 Agent，还顺带清掉「待修改专家」
绑定——「点编辑 → 模型先追问 → 下一轮再保存」时 save_expert 找不到修改目标。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import easy_agent.services.memory_manager as memory_manager
from easy_agent.services import agent_manager
from easy_agent.services.streaming import side_effects


async def test_memory_update_keeps_the_session_agent_and_edit_binding(tmp_path, monkeypatch):
    executor = ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(side_effects, "_MEMORY_UPDATE_EXECUTOR", executor)
    monkeypatch.setattr(memory_manager, "update_memory_after_session", lambda *args, **kwargs: True)
    removed = []
    monkeypatch.setattr(agent_manager, "remove_session_agent", removed.append)
    agent_manager.set_session_edit_target("sess-memory", "expert-1")
    agent = SimpleNamespace(memory_file=tmp_path / "memory.md", long_term_memory_file=None, model=object())

    try:
        side_effects.submit_memory_updates(agent, "sess-memory", None, "q", "a", "memory")
        executor.shutdown(wait=True)  # 等后台记忆任务跑完

        assert removed == []
        assert agent_manager.get_session_edit_target("sess-memory") == "expert-1"
    finally:
        agent_manager._session_edit_targets.pop("sess-memory", None)
