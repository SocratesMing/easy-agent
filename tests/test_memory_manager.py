from easy_agent.services.memory_manager import (
    MAX_LONG_TERM_MEMORY_CHARS,
    MAX_MEMORY_CHARS,
    build_long_term_memory_update_prompt,
    build_memory_update_prompt,
    update_long_term_memory_after_session,
    update_memory_after_session,
)


def test_session_memory_prompt_preserves_project_handoff_context():
    prompt = build_memory_update_prompt(
        current_memory="",
        user_message="请总结当前项目的主要逻辑并修复报错",
        assistant_response="已完成架构梳理和测试。",
    )

    # 按契约校验（不锁死文案措辞）：具备恢复上下文所需的结构要素
    assert "## 当前项目" in prompt
    assert "## 下一步" in prompt
    assert "## 关键决策" in prompt
    assert "## 踩坑与解法" in prompt


def test_session_memory_prompt_keeps_restore_sections_and_safety_rules():
    prompt = build_memory_update_prompt(
        current_memory="",
        user_message="请继续实现项目模块",
        assistant_response="已完成实现。",
    )

    assert "## 当前项目" in prompt
    assert "## 下一步" in prompt
    assert "## 踩坑与解法" in prompt
    assert "原始日志" in prompt
    assert "密钥" in prompt
    assert str(MAX_MEMORY_CHARS) in prompt


def test_all_sessions_use_context_summary_without_task_mode():
    prompt = build_memory_update_prompt(
        current_memory="",
        user_message="帮我写一句问候语",
        assistant_response="你好，祝你今天顺利！",
        max_chars=2000,
    )

    assert "输出模式" not in prompt
    assert "简单任务" not in prompt
    assert "项目上下文" not in prompt
    assert str(MAX_MEMORY_CHARS) in prompt
    assert "省略" in prompt


def test_single_weak_project_signal_still_uses_compact_budget():
    prompt = build_memory_update_prompt(
        current_memory="",
        user_message="帮我优化这句话",
        assistant_response="已优化。",
        max_chars=2000,
    )

    assert "输出模式" not in prompt
    assert str(MAX_MEMORY_CHARS) in prompt


def test_existing_non_project_memory_is_recompressed_to_compact_budget():
    prompt = build_memory_update_prompt(
        current_memory="# 会话记忆\n\n## 日常问答\n" + "x" * 1500,
        user_message="帮我写一句问候语",
        assistant_response="你好，祝你今天顺利！",
        max_chars=2000,
    )

    assert "输出模式" not in prompt
    assert str(MAX_MEMORY_CHARS) in prompt


def test_project_session_also_uses_compact_memory_prompt_budget():
    prompt = build_memory_update_prompt(
        current_memory="# 会话记忆",
        user_message="请重构项目架构并修复报错",
        assistant_response="已完成模块拆分和测试。",
        max_chars=2000,
    )

    assert "输出模式" not in prompt
    assert str(MAX_MEMORY_CHARS) in prompt
    assert "## 当前项目" in prompt
    assert "## 踩坑与解法" in prompt


def test_update_memory_enforces_compact_budget_without_task_mode(tmp_path):
    class FakeLLM:
        def __init__(self):
            self.calls = []

        def invoke(self, messages):
            self.calls.append(messages[0].content)
            return type("Response", (), {"content": "x" * 900})()

    llm = FakeLLM()
    memory_file = tmp_path / "memory.md"

    updated = update_memory_after_session(
        memory_file,
        user_message="帮我写一句问候语",
        assistant_response="你好，祝你今天顺利！",
        llm=llm,
    )

    assert updated is True
    assert len(memory_file.read_text(encoding="utf-8")) <= 600
    assert str(MAX_MEMORY_CHARS) in llm.calls[0]
    assert str(MAX_MEMORY_CHARS) in llm.calls[1]


def test_long_term_memory_uses_compact_context_budget():
    prompt = build_long_term_memory_update_prompt(
        current_memory="",
        user_message="帮我写一句问候语",
        assistant_response="你好，祝你今天顺利！",
        max_chars=4000,
    )

    assert str(MAX_LONG_TERM_MEMORY_CHARS) in prompt
    assert "跨会话长期有效" in prompt
    assert "## 用户偏好" in prompt
    assert "## 项目背景" in prompt
    assert "## 可复用经验" in prompt
    assert "一次性任务" in prompt


def test_long_term_memory_update_enforces_compact_budget(tmp_path):
    class FakeLLM:
        def __init__(self):
            self.calls = []

        def invoke(self, messages):
            self.calls.append(messages[0].content)
            return type("Response", (), {"content": "x" * 900})()

    llm = FakeLLM()
    memory_file = tmp_path / "AGENTS.md"

    updated = update_long_term_memory_after_session(
        memory_file,
        user_message="帮我写一句问候语",
        assistant_response="你好，祝你今天顺利！",
        llm=llm,
    )

    assert updated is True
    assert len(memory_file.read_text(encoding="utf-8")) <= 800
    assert "不超过 800 字符" in llm.calls[0]
    assert "不超过 800 个字符" in llm.calls[1]
