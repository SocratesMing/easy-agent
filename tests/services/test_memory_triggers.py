"""记忆不再每轮整理：有信号当轮整理，否则攒够几轮再整理一次（services/memory/triggers.py）。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

import easy_agent.services.memory.manager as memory_manager
from easy_agent.services.agents import prompts as prompt_loader
from easy_agent.services.memory.manager import (
    update_long_term_memory_after_session,
    update_memory_after_session,
)
from easy_agent.services.memory.triggers import (
    EVERY_N_TURNS,
    SESSION_EVERY_N_TURNS,
    TurnBatcher,
    has_long_term_signal,
    has_session_signal,
)
from easy_agent.services.streaming import side_effects


@pytest.fixture()
def authoritative_prompts(monkeypatch):
    monkeypatch.setattr(
        prompt_loader,
        "_CONFIGURED_DIR",
        Path(__file__).resolve().parents[2] / "easy_agent" / "config" / "prompts",
    )


@pytest.fixture()
def executor(monkeypatch):
    pool = ThreadPoolExecutor(max_workers=2)
    monkeypatch.setattr(side_effects, "_MEMORY_UPDATE_EXECUTOR", pool)
    monkeypatch.setattr(side_effects, "LONG_TERM_BATCHER", TurnBatcher(every_n_turns=EVERY_N_TURNS))
    monkeypatch.setattr(
        side_effects, "SESSION_BATCHER", TurnBatcher(every_n_turns=SESSION_EVERY_N_TURNS, max_note_chars=900)
    )
    yield pool
    pool.shutdown(wait=True)


# ── 信号规则 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "记住我喜欢表格输出",
        "我是固收研究员，主要看利率债",
        "以后代码注释都用中文",
        "我们组主要做信用债",
        "我在招商银行做资产配置",
        "最近在准备一份宏观周报",
        "Remember that I work on bonds",
    ],
)
def test_profile_signals_are_detected(text):
    assert has_long_term_signal(text)


@pytest.mark.parametrize(
    "text",
    ["你好", "帮我分析一下 10 年国债收益率走势", "/docx 写一个智能体介绍的文档", "总结主对话里的要点", ""],
)
def test_plain_tasks_are_not_profile_signals(text):
    assert not has_long_term_signal(text)


@pytest.mark.parametrize(
    "text",
    ["就用 MySQL，不要 SQLite", "以后金额单位统一用万元", "不对，应该是按季度汇总", "接口必须带 /agent 前缀"],
)
def test_session_signals_are_decisions_constraints_or_corrections(text):
    assert has_session_signal(text)


def test_session_signal_from_a_rejected_action():
    assert not has_session_signal("帮我清理一下工作区")
    assert has_session_signal("帮我清理一下工作区", "- delete /workspace/old ✗ 用户拒绝")


# ── 攒批 ────────────────────────────────────────────────────────────────


def _add(batcher, key, text):
    return batcher.add(key, text, signal=has_long_term_signal(text))


def test_turns_without_signal_are_batched_until_every_n_turns():
    batcher = TurnBatcher(every_n_turns=3)

    first = _add(batcher, "u1", "分析国债")
    second = _add(batcher, "u1", "再看信用利差")
    third = _add(batcher, "u1", "写一段结论")

    assert (first.due, first.pending) == (False, 1)
    assert (second.due, second.pending) == (False, 2)
    assert third.due and third.reason == "count"
    assert third.earlier == ("分析国债", "再看信用利差")
    # 整理过后重新攒
    assert _add(batcher, "u1", "继续").pending == 1


def test_signal_turn_is_due_at_once_and_takes_the_pending_turns_along():
    batcher = TurnBatcher(every_n_turns=5)
    _add(batcher, "u1", "分析国债")

    decision = _add(batcher, "u1", "记住我是固收研究员")

    assert decision.due and decision.reason == "signal"
    assert decision.earlier == ("分析国债",)


def test_pending_turns_are_kept_per_memory_and_truncated():
    batcher = TurnBatcher(every_n_turns=2, max_note_chars=5)
    _add(batcher, "u1", "一二三四五六七")

    assert _add(batcher, "u2", "别的用户").due is False
    assert _add(batcher, "u1", "第二轮").earlier == ("一二三四五",)


def test_least_recently_used_memories_are_evicted():
    batcher = TurnBatcher(every_n_turns=5, max_keys=2)
    _add(batcher, "s1", "a")
    _add(batcher, "s2", "b")
    _add(batcher, "s1", "c")  # s1 最近动过
    _add(batcher, "s3", "d")  # 超出上限：淘汰最久没动的 s2

    assert _add(batcher, "s1", "f").pending == 3  # s1 攒的轮次还在
    assert _add(batcher, "s2", "e").pending == 1  # s2 已被淘汰，从头攒


# ── 交给模型的内容 ────────────────────────────────────────────────────────


def test_long_term_update_shows_the_batched_turns_to_the_model(tmp_path, authoritative_prompts):
    prompts: list[str] = []

    class _LLM:
        def invoke(self, messages):
            prompts.append(messages[0].content)
            return SimpleNamespace(content="# 用户长期记忆\n\n## 当前关注\n- 利率债与信用利差\n")

    assert update_long_term_memory_after_session(
        tmp_path / "AGENTS.md",
        "写一段结论",
        "好的",
        llm=_LLM(),
        earlier_user_messages=("分析国债", "再看信用利差"),
    )

    assert "1. 分析国债" in prompts[0] and "2. 再看信用利差" in prompts[0]
    assert "（本轮）\n写一段结论" in prompts[0]


def test_session_update_shows_the_batched_turns_to_the_model(tmp_path, authoritative_prompts):
    prompts: list[str] = []

    class _LLM:
        def invoke(self, messages):
            prompts.append(messages[0].content)
            return SimpleNamespace(content="# 会话记忆\n\n## 决策与理由\n- 数据库用 MySQL —— 与生产一致\n")

    assert update_memory_after_session(
        tmp_path / "memory.md",
        "继续写迁移脚本",
        "好的",
        llm=_LLM(),
        earlier_turns=("用户：选数据库\n助手：建议 MySQL，与生产一致",),
    )

    assert "之前几轮还没整理过的对话" in prompts[0]
    assert "1. 用户：选数据库\n   助手：建议 MySQL，与生产一致" in prompts[0]
    assert "（本轮）\n继续写迁移脚本" in prompts[0]


# ── 收尾流程 ──────────────────────────────────────────────────────────────


def test_long_term_memory_skips_turns_without_signal(executor, monkeypatch, tmp_path):
    """没有画像信号的轮次不调模型；攒够轮数时把之前几轮一起交给整理。"""
    calls: list[dict] = []
    monkeypatch.setattr(
        side_effects,
        "update_long_term_memory_after_session",
        lambda memory_file, **kwargs: calls.append(kwargs),
    )
    agent = SimpleNamespace(memory_file=None, long_term_memory_file=tmp_path / "AGENTS.md", model=object())

    for turn in range(EVERY_N_TURNS):
        side_effects.submit_memory_updates(agent, "s1", None, f"任务 {turn}", "答复", "s1")
    executor.shutdown(wait=True)

    assert len(calls) == 1
    assert calls[0]["user_message"] == f"任务 {EVERY_N_TURNS - 1}"
    assert calls[0]["earlier_user_messages"] == tuple(f"任务 {i}" for i in range(EVERY_N_TURNS - 1))


def test_session_memory_is_batched_and_merges_earlier_turns(executor, monkeypatch, tmp_path):
    calls: list[dict] = []
    monkeypatch.setattr(
        memory_manager, "update_memory_after_session", lambda memory_file, **kwargs: calls.append(kwargs)
    )
    agent = SimpleNamespace(memory_file=tmp_path / "memory.md", long_term_memory_file=None, model=object())

    for turn in range(SESSION_EVERY_N_TURNS):
        side_effects.submit_memory_updates(agent, "s1", None, f"任务 {turn}", f"回复 {turn}", "s1")
    executor.shutdown(wait=True)

    assert len(calls) == 1
    assert calls[0]["user_message"] == f"任务 {SESSION_EVERY_N_TURNS - 1}"
    assert calls[0]["earlier_turns"] == tuple(
        f"用户：任务 {i}\n助手：回复 {i}" for i in range(SESSION_EVERY_N_TURNS - 1)
    )


def test_session_memory_updates_at_once_on_a_decision(executor, monkeypatch, tmp_path):
    calls: list[dict] = []
    monkeypatch.setattr(
        memory_manager, "update_memory_after_session", lambda memory_file, **kwargs: calls.append(kwargs)
    )
    agent = SimpleNamespace(memory_file=tmp_path / "memory.md", long_term_memory_file=None, model=object())

    side_effects.submit_memory_updates(agent, "s1", None, "看看两种方案", "方案一更稳", "s1")
    side_effects.submit_memory_updates(agent, "s1", None, "就用方案一", "好的", "s1")
    executor.shutdown(wait=True)

    assert [call["user_message"] for call in calls] == ["就用方案一"]
    assert calls[0]["earlier_turns"] == ("用户：看看两种方案\n助手：方案一更稳",)
