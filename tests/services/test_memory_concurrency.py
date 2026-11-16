"""记忆更新的并发安全：同一份记忆按提交顺序串行，写回前核对文件没被别处改写。

记忆更新是「读 → 调模型（几秒，慢时几十秒）→ 写」，锁只罩住写。改造前两次更新并行时，
后写的会把先写的整份覆盖：同一会话连续两轮、同一用户的两个会话前后脚结束、设置页手动
修改长期记忆时后台更新正好在跑，都会丢内容。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from easy_agent.services.agents import prompts as prompt_loader
from easy_agent.services.memory.manager import (
    update_long_term_memory_after_session,
    update_memory_after_session,
    write_memory,
)
from easy_agent.services.memory.triggers import EVERY_N_TURNS, TurnBatcher
from easy_agent.services.streaming import side_effects


@pytest.fixture(autouse=True)
def _use_authoritative_prompts():
    old = prompt_loader._CONFIGURED_DIR
    prompt_loader.configure_prompts_dir(
        Path(__file__).resolve().parents[2] / "easy_agent" / "config" / "prompts"
    )
    yield
    prompt_loader._CONFIGURED_DIR = old


@pytest.fixture()
def executor(monkeypatch):
    pool = ThreadPoolExecutor(max_workers=2)
    monkeypatch.setattr(side_effects, "_MEMORY_UPDATE_EXECUTOR", pool)
    yield pool
    pool.shutdown(wait=True)


# ── 同一份记忆按提交顺序串行 ─────────────────────────────────────────────


def test_memory_jobs_for_the_same_memory_run_in_submission_order(executor):
    release = threading.Event()
    first_started = threading.Event()
    second_ran = threading.Event()
    order: list[str] = []

    def first():
        first_started.set()
        release.wait(5)
        order.append("first")

    def second():
        second_ran.set()
        order.append("second")

    side_effects._submit_memory_job("session:s1", first)
    done = side_effects._submit_memory_job("session:s1", second)

    assert first_started.wait(5)
    # 线程池还有空闲线程，但同一份记忆的第二个任务必须等第一个结束
    assert not second_ran.wait(0.2)
    release.set()
    done.result(timeout=5)
    assert order == ["first", "second"]
    assert "session:s1" not in side_effects._MEMORY_JOB_TAILS


def test_memory_jobs_for_different_memories_do_not_wait_for_each_other(executor):
    release = threading.Event()
    side_effects._submit_memory_job("file:/a/AGENTS.md", lambda: release.wait(5))
    other = side_effects._submit_memory_job("file:/b/AGENTS.md", lambda: None)
    try:
        other.result(timeout=5)
    finally:
        release.set()


def test_long_term_updates_from_two_sessions_of_one_user_are_serialized(executor, monkeypatch, tmp_path):
    """长期记忆按文件排队：同一用户的两个会话前后脚结束，第二个更新要等第一个写完。"""
    release = threading.Event()
    first_started = threading.Event()
    second_started = threading.Event()
    calls: list[str] = []
    q1, q2 = "记住我是固收研究员", "以后都用表格输出"  # 有画像信号：当轮整理（triggers.py）

    def fake_long_term(memory_file, **kwargs):
        calls.append(kwargs["user_message"])
        if kwargs["user_message"] == q1:
            first_started.set()
            release.wait(5)
        else:
            second_started.set()

    monkeypatch.setattr(side_effects, "update_long_term_memory_after_session", fake_long_term)
    monkeypatch.setattr(side_effects, "LONG_TERM_BATCHER", TurnBatcher(every_n_turns=EVERY_N_TURNS))
    shared = tmp_path / "memories" / "u1" / "AGENTS.md"
    for session_id, question in (("s1", q1), ("s2", q2)):
        agent = SimpleNamespace(memory_file=None, long_term_memory_file=shared, model=object())
        side_effects.submit_memory_updates(agent, session_id, None, question, "answer", session_id)

    assert first_started.wait(5)
    assert not second_started.wait(0.2)
    release.set()
    assert second_started.wait(5)
    assert calls == [q1, q2]


# ── 写回前核对：文件在调模型期间被改写，就基于最新内容重做 ─────────────────


class _ScriptedLLM:
    """按顺序返回脚本内容；``on_call[i]`` 在第 i 次调用时模拟别处写入（发生在锁外）。"""

    def __init__(self, outputs: list[str], on_call: dict[int, callable] | None = None):
        self.outputs = list(outputs)
        self.on_call = on_call or {}
        self.prompts: list[str] = []

    def invoke(self, messages):
        index = len(self.prompts)
        self.prompts.append(messages[0].content)
        if index in self.on_call:
            self.on_call[index]()
        return SimpleNamespace(content=self.outputs[index])


def _session_memory(*items: str) -> str:
    return "# 会话记忆\n\n## 偏好与约定\n" + "".join(f"- {item}\n" for item in items)


def test_session_memory_update_redoes_on_top_of_a_concurrent_write(tmp_path):
    memory_file = tmp_path / "memory.md"
    write_memory(memory_file, _session_memory("原有约定"))
    manual = _session_memory("原有约定", "用户在别处新加的一条")
    llm = _ScriptedLLM(
        [_session_memory("原有约定", "第一次的结论"), _session_memory("用户在别处新加的一条", "第二次的结论")],
        on_call={0: lambda: write_memory(memory_file, manual)},
    )

    assert update_memory_after_session(memory_file, "q", "a", llm=llm) is True

    assert len(llm.prompts) == 2
    assert "用户在别处新加的一条" in llm.prompts[1]  # 第二遍基于最新内容
    final = memory_file.read_text(encoding="utf-8")
    assert "用户在别处新加的一条" in final and "第二次的结论" in final
    assert "第一次的结论" not in final


def test_session_memory_update_gives_up_instead_of_overwriting(tmp_path):
    memory_file = tmp_path / "memory.md"
    write_memory(memory_file, _session_memory("原有约定"))
    newest = _session_memory("第二次别处写入")
    llm = _ScriptedLLM(
        [_session_memory("第一次的结论"), _session_memory("第二次的结论")],
        on_call={
            0: lambda: write_memory(memory_file, _session_memory("第一次别处写入")),
            1: lambda: write_memory(memory_file, newest),
        },
    )

    assert update_memory_after_session(memory_file, "q", "a", llm=llm) is False
    assert memory_file.read_text(encoding="utf-8") == newest


def test_long_term_memory_update_redoes_on_top_of_a_manual_edit(tmp_path):
    """设置页手动改了长期记忆，正在跑的后台更新不能把它盖掉。"""
    memory_file = tmp_path / "AGENTS.md"
    write_memory(memory_file, "# 用户长期记忆\n\n## 工作背景\n- 固收研究员\n")
    manual = "# 用户长期记忆\n\n## 工作背景\n- 固收研究员\n\n## 个人背景\n- 偏好表格输出\n"
    llm = _ScriptedLLM(
        [
            "# 用户长期记忆\n\n## 工作背景\n- 固收研究员，关注利率债\n",
            "# 用户长期记忆\n\n## 工作背景\n- 固收研究员，关注利率债\n\n## 个人背景\n- 偏好表格输出\n",
        ],
        on_call={0: lambda: write_memory(memory_file, manual)},
    )

    assert update_long_term_memory_after_session(memory_file, "q", "a", llm=llm) is True

    assert len(llm.prompts) == 2
    assert "偏好表格输出" in llm.prompts[1]
    final = memory_file.read_text(encoding="utf-8")
    assert "偏好表格输出" in final and "关注利率债" in final
