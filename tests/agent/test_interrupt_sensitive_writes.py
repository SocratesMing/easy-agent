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
