"""D7：敏感路径写入走人工审批（``agent.interrupt_sensitive_writes``）。

覆盖两条「能跨会话影响后续行为」的路径：

- ``/memories/**``：长期记忆（AGENTS.md），写坏会污染之后所有会话；
- ``/user-skills/**``：技能正文/脚本，下一轮会被当指令读取，等于持久化提示注入。

规则顺序很关键：硬禁令（已有技能不可改写、公共技能只读）必须排在 interrupt 之前，
否则模型改既有技能会变成「批准后就能改」。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from easy_agent.agent import EasyAgent


def _agent(tmp_path: Path, *, enabled: bool, skills_mounted: bool = True) -> EasyAgent:
    agent = EasyAgent.__new__(EasyAgent)
    agent.config = SimpleNamespace(
        agent=SimpleNamespace(
            denied_dirs=[],
            skill_write="new-only",
            interrupt_sensitive_writes=enabled,
        )
    )
    agent.sid = "test-session"
    agent.workspace_virtual_path = "/workspace"
    agent.builtin_skills_dir = None
    skills_dir = tmp_path / "skills"
    if skills_mounted:
        skills_dir.mkdir(parents=True, exist_ok=True)
    agent.user_skills_dir = skills_dir
    agent._discover_user_skill_names = lambda: []
    return agent


def _interrupt_rules(rules):
    return [rule for rule in rules if rule.mode == "interrupt"]


def test_disabled_by_default_adds_no_interrupt_rules(tmp_path: Path):
    rules = _agent(tmp_path, enabled=False)._build_permissions(["/user-skills/"])
    assert _interrupt_rules(rules) == []


def test_enabled_covers_memories_and_user_skills(tmp_path: Path):
    rules = _agent(tmp_path, enabled=True)._build_permissions(["/user-skills/"])
    interrupt = _interrupt_rules(rules)
    assert len(interrupt) == 1
    assert interrupt[0].paths == ["/memories/**", "/user-skills/**"]
    assert interrupt[0].operations == ["write"]


def test_interrupt_rule_comes_after_hard_denies(tmp_path: Path):
    rules = _agent(tmp_path, enabled=True)._build_permissions(["/user-skills/"])
    interrupt_index = rules.index(_interrupt_rules(rules)[0])
    deny_indexes = [i for i, rule in enumerate(rules) if rule.mode == "deny"]
    assert deny_indexes, "new-only 策略应当产生 deny 规则"
    assert interrupt_index > max(deny_indexes), "deny 必须先命中，避免「批准即可改写既有技能」"


def test_skills_not_mounted_only_protects_memories(tmp_path: Path):
    agent = _agent(tmp_path, enabled=True, skills_mounted=False)
    rules = agent._build_permissions(None)
    interrupt = _interrupt_rules(rules)
    assert len(interrupt) == 1
    assert interrupt[0].paths == ["/memories/**"]


# ── 框架级验证：真实建图后写入是否真的暂停 ──────────────────────────

import tempfile

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver


class _ScriptedModel(GenericFakeChatModel):
    """按脚本产出一次 write_file 工具调用；GenericFakeChatModel 不支持 bind_tools。"""

    def bind_tools(self, tools, **kwargs):
        return self


def _interrupt_for(path: str):
    model = _ScriptedModel(
        messages=iter(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "write_file", "args": {"file_path": path, "content": "x"}, "id": "c1"}
                    ],
                ),
                AIMessage(content="done"),
            ]
        )
    )
    agent = create_deep_agent(
        model=model,
        system_prompt="x",
        backend=FilesystemBackend(root_dir=tempfile.mkdtemp(), virtual_mode=True),
        permissions=[
            FilesystemPermission(operations=["write"], paths=["/memories/**"], mode="interrupt")
        ],
        checkpointer=MemorySaver(),
    )
    result = agent.invoke(
        {"messages": [HumanMessage(content="write")]},
        config={"configurable": {"thread_id": "t-interrupt"}},
    )
    return result.get("__interrupt__")


def test_write_to_memories_really_pauses():
    interrupt = _interrupt_for("/memories/AGENTS.md")
    assert interrupt, "写入 /memories 应当触发人工审批"
    actions = interrupt[0].value["action_requests"]
    assert actions[0]["name"] == "write_file"
    assert actions[0]["args"]["file_path"] == "/memories/AGENTS.md"


def test_write_to_workspace_is_not_interrupted():
    assert not _interrupt_for("/workspace/notes.md"), "普通工作区写入不应打断"


# ── 关闭 HITL 的运行（定时任务）：没人审批，不能中断 ──────────────────────


def test_hitl_off_adds_no_interrupt_rules(tmp_path: Path):
    """定时任务以 enable_hitl=False 运行：中断没人批，只会让写入丢失。"""
    agent = _agent(tmp_path, enabled=True)
    agent.enable_hitl = False
    assert _interrupt_rules(agent._build_permissions(["/user-skills/"])) == []


import json  # noqa: E402

import pytest  # noqa: E402
from langchain_core.messages import AIMessageChunk  # noqa: E402
from langchain_core.outputs import ChatGenerationChunk  # noqa: E402

import easy_agent.agent.core as agent_mod  # noqa: E402
from easy_agent.agent.graph_cache import reset_agent_graph_cache  # noqa: E402
from easy_agent.bootstrap.config import Config  # noqa: E402


class _StreamingToolModel(_ScriptedModel):
    """流式输出时保留 tool_calls（GenericFakeChatModel 的流式实现会丢掉它们）。"""

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


@pytest.fixture
def scheduled_run(tmp_path, db, monkeypatch):
    """用真实 EasyAgent 跑一次定时任务；模型先写 /memories/AGENTS.md 再结束。

    ``force_hitl``：忽略调度器传的 ``enable_hitl=False``，模拟「运行停在审批处」。
    """
    import easy_agent.services.agent_manager as agent_manager
    import easy_agent.services.scheduler as sched
    from easy_agent.bootstrap.config import DistributedLockConfig
    from easy_agent.models.db import ScheduledTaskModel
    from easy_agent.utils.distributed_lock import DistributedLock

    reset_agent_graph_cache()
    monkeypatch.setenv("EASY_AGENT_GRAPH_CACHE", "1")
    saver = MemorySaver()
    monkeypatch.setattr(agent_mod, "_shared_graph_checkpointer", lambda: saver)
    memory_file = tmp_path / "memories" / "testuser" / "AGENTS.md"

    async def run(*, force_hitl: bool | None = None):
        script = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"file_path": "/memories/AGENTS.md", "content": "偏好：周报用表格"},
                        "id": "w1",
                    }
                ],
            ),
            AIMessage(content="已记住"),
        ]
        model = _StreamingToolModel(messages=iter(script))
        monkeypatch.setattr(agent_mod, "create_model", lambda *a, **k: model)
        cfg = Config.load()
        cfg.agent.workspace_dir = str(tmp_path / "workspace")
        cfg.agent.memories_dir = str(tmp_path / "memories")
        cfg.agent.interrupt_sensitive_writes = True

        async def fake_get_agent(session_id, username="default", workspace_name="", **kwargs):
            enable_hitl = kwargs.get("enable_hitl", True) if force_hitl is None else force_hitl
            return EasyAgent(
                config=cfg,
                system_prompt="P",
                username=username,
                session_id=session_id,
                workspace_name=workspace_name or "w-sched",
                enable_hitl=enable_hitl,
            )

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
                task_id="task-d7",
                username="testuser",
                name="记偏好",
                schedule_cron="0 9 * * *",
                task_prompt="把我的周报偏好记下来",
                enabled=1,
                created_at="2026-01-01T00:00:00",
                updated_at="2026-01-01T00:00:00",
            )
        )
        await sched._execute_task("task-d7")
        return db.list_scheduled_task_runs("task-d7")

    run.memory_file = memory_file
    yield run
    reset_agent_graph_cache()


async def test_scheduled_run_writes_memories_without_pausing(scheduled_run):
    """改造前：写入在审批处暂停，调度器记为 succeeded 并删掉 thread，写入悄悄丢失。"""
    runs = await scheduled_run()

    assert [run.status for run in runs] == ["succeeded"]
    assert scheduled_run.memory_file.read_text(encoding="utf-8") == "偏好：周报用表格"


async def test_scheduled_run_paused_for_approval_is_recorded_as_failed(scheduled_run):
    """兜底：运行停在审批处（这里强制开着 HITL 模拟）不能记为成功。"""
    runs = await scheduled_run(force_hitl=True)

    assert [run.status for run in runs] == ["failed"]
    assert "人工审批" in runs[0].error_message and "write_file" in runs[0].error_message
    assert not scheduled_run.memory_file.exists()
