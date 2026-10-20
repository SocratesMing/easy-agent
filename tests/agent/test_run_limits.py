"""DA-10：运行上限与调用参数的一致性。

- ``recursion_limit`` 兜底必须容得下 ``max_steps`` 轮完整循环（改造前恢复流用的
  ``max(2 * max_steps, 50)`` 约 6 轮就会 ``GraphRecursionError``，聊天流则干脆没设）；
- 子代理（含框架自带的 general-purpose）必须受自己的调用上限约束；
- 定时任务与聊天流同一套调用参数，共享编译图必须拿到 ``SessionContext``。
"""

from __future__ import annotations

import json
from itertools import chain, repeat

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from langgraph.checkpoint.memory import MemorySaver
from langgraph.errors import GraphRecursionError

import easy_agent.agent.core as agent_mod
from easy_agent.agent import EasyAgent, build_subagent_specs
from easy_agent.agent.graph_cache import reset_agent_graph_cache
from easy_agent.agent.run_config import build_run_config, recursion_limit_for, steps_per_turn
from easy_agent.bootstrap.config import Config


class _ToolCallingFake(GenericFakeChatModel):
    """GenericFakeChatModel 的流式实现会丢掉 tool_calls，这里补上。"""

    def bind_tools(self, tools, **kwargs):
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        message = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs).generations[0].message
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=message.content,
                id=message.id,
                tool_call_chunks=[
                    {"name": tc["name"], "args": json.dumps(tc["args"]), "id": tc["id"], "index": i}
                    for i, tc in enumerate(message.tool_calls or [])
                ],
            )
        )


def _ls_turns(n: int) -> list[AIMessage]:
    return [
        AIMessage(content="", tool_calls=[{"name": "ls", "args": {"path": "/workspace/"}, "id": f"c{i}"}])
        for i in range(n)
    ]


@pytest.fixture
def real_agent(tmp_path, monkeypatch):
    """真实 EasyAgent 共享编译图 + 假模型（不联网）。"""
    reset_agent_graph_cache()
    monkeypatch.setenv("EASY_AGENT_GRAPH_CACHE", "1")
    saver = MemorySaver()
    monkeypatch.setattr(agent_mod, "_shared_graph_checkpointer", lambda: saver)

    def build(messages, *, max_steps: int, session_id: str = "s1"):
        # 每个用例自带假模型脚本：清掉进程内编译图缓存，避免复用上一张图里的模型
        reset_agent_graph_cache()
        cfg = Config.load()
        cfg.agent.workspace_dir = str(tmp_path / "workspace")
        cfg.agent.memories_dir = str(tmp_path / "memories")
        cfg.agent.max_steps = max_steps
        monkeypatch.setattr(agent_mod, "create_model", lambda *a, **k: _ToolCallingFake(messages=iter(messages)))
        return EasyAgent(
            config=cfg,
            system_prompt="P",
            username="u1",
            session_id=session_id,
            workspace_name=f"w-{session_id}",
            enable_hitl=True,
        )

    yield build
    reset_agent_graph_cache()


async def _run(agent, config):
    async for _ in agent.agent.astream(
        {"messages": [HumanMessage(content="go")]},
        stream_mode=["updates"],
        config=config,
        context=agent.session_context,
    ):
        pass
    return await agent.agent.aget_state(config)


def test_steps_per_turn_reads_hook_nodes_from_real_graph(real_agent):
    agent = real_agent([AIMessage(content="done")], max_steps=5)
    # model + tools + 每个 before_model / after_model 钩子节点
    nodes = agent.agent.get_graph().nodes
    hooks = [n for n in nodes if n.endswith((".before_model", ".after_model"))]
    assert steps_per_turn(agent.agent) == 2 + len(hooks)
    assert recursion_limit_for(agent) == steps_per_turn(agent.agent) * 6 + 25


async def test_recursion_limit_covers_the_whole_model_call_budget(real_agent):
    max_steps = 12
    turns = max_steps - 1  # 最后一次模型调用给出答案，正好用满 run_limit
    agent = real_agent([*_ls_turns(turns), AIMessage(content="done")], max_steps=max_steps)

    state = await _run(agent, build_run_config(agent, "s1-m1"))
    assert state.values["messages"][-1].content == "done"

    # 对照：改造前恢复流的上限在跑满预算前就会抛 GraphRecursionError
    legacy = real_agent(
        [*_ls_turns(turns), AIMessage(content="done")], max_steps=max_steps, session_id="s2"
    )
    with pytest.raises(GraphRecursionError):
        await _run(
            legacy,
            {"configurable": {"thread_id": "s2-m1"}, "recursion_limit": max(2 * max_steps, 50)},
        )


async def test_general_purpose_subagent_is_bounded_by_its_own_call_limit():
    from deepagents import create_deep_agent
    from deepagents.backends import StateBackend

    task_call = AIMessage(
        content="",
        tool_calls=[{"name": "task", "args": {"description": "loop", "subagent_type": "general-purpose"}, "id": "t1"}],
    )
    # 主代理派任务 → 子代理连续 ls（不收敛）→ 子代理第 3 次模型调用被拦下 → 主代理收尾
    script = [task_call, *_ls_turns(2), AIMessage(content="main done")]
    model = _ToolCallingFake(messages=chain(script, repeat(AIMessage(content="EXTRA CALL"))))
    agent = create_deep_agent(
        model=model,
        backend=StateBackend(),
        subagents=build_subagent_specs(max_steps=2),
        checkpointer=MemorySaver(),
    )

    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="go")]}, config={"configurable": {"thread_id": "t"}}
    )

    task_result = next(m for m in result["messages"] if m.type == "tool" and m.tool_call_id == "t1")
    assert "limit" in task_result.content.lower()
    assert result["messages"][-1].content == "main done"


async def _run_daily_report_task(real_agent, db, monkeypatch):
    """用真实共享编译图跑一次定时任务 ``task-da10``。"""
    import easy_agent.services.agent_manager as agent_manager
    import easy_agent.services.scheduler as sched
    from easy_agent.bootstrap.config import DistributedLockConfig
    from easy_agent.models.db import ScheduledTaskModel
    from easy_agent.utils.distributed_lock import DistributedLock

    agent = real_agent([AIMessage(content="report ready")], max_steps=5, session_id="sched-s1")

    async def fake_get_agent(session_id, username="default", workspace_name="", **kwargs):
        return agent

    monkeypatch.setattr(agent_manager, "get_or_create_agent_for_session", fake_get_agent)
    monkeypatch.setattr(agent_manager, "remove_session_agent", lambda session_id: None)
    monkeypatch.setattr(sched, "get_database", lambda: db)
    monkeypatch.setattr(sched, "get_scheduler", lambda: type("S", (), {"get_job": lambda self, tid: None})())
    monkeypatch.setattr(sched, "log_task_event", lambda **kwargs: None)
    monkeypatch.setattr(
        sched,
        "get_distributed_lock",
        lambda: DistributedLock(config=DistributedLockConfig(enabled=False), db=db, owner="pod"),
    )
    db.create_scheduled_task(
        ScheduledTaskModel(
            task_id="task-da10",
            username="testuser",
            name="日报",
            schedule_cron="0 9 * * *",
            task_prompt="写一份日报",
            enabled=1,
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
    )

    await sched._execute_task("task-da10")
    return db.list_scheduled_task_runs("task-da10")


async def test_scheduled_task_runs_on_shared_graph(real_agent, db, monkeypatch):
    """定时任务改造前不传 context：共享编译图在 before_agent 读记忆时即抛错。"""
    runs = await _run_daily_report_task(real_agent, db, monkeypatch)
    assert [run.status for run in runs] == ["succeeded"]


async def test_scheduled_task_deletes_its_thread_after_the_run(real_agent, db, monkeypatch):
    """DA-03：每次运行一个独立 thread 且关闭了 HITL，运行结束后删除，官方 saver 不会自动清理。"""
    import easy_agent.persistence.checkpoints as checkpoints

    deleted = []

    async def fake_adelete(thread_ids):
        deleted.extend(thread_ids)
        return len(deleted)

    monkeypatch.setattr(checkpoints, "adelete_threads", fake_adelete)

    runs = await _run_daily_report_task(real_agent, db, monkeypatch)

    assert deleted == [f"sched-task-da10-{runs[0].run_id}"]
