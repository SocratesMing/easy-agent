from easy_agent.services.memory_manager import (
    MAX_LONG_TERM_MEMORY_CHARS,
    MAX_MEMORY_CHARS,
    build_long_term_memory_update_prompt,
    build_memory_update_prompt,
    strip_role_scope_clauses,
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


def test_long_term_memory_uses_structured_context_budget():
    prompt = build_long_term_memory_update_prompt(
        current_memory="",
        user_message="我最近在做一个 AI 项目，想让你记住我的工作背景。",
        assistant_response="已记录长期背景。",
        max_chars=4000,
    )

    assert str(MAX_LONG_TERM_MEMORY_CHARS) in prompt
    assert "1500" in prompt
    assert "跨会话长期有效" in prompt
    assert "## 工作背景" in prompt
    assert "## 个人背景" in prompt
    assert "## 当前关注" in prompt
    assert "## 近期动态" in prompt
    assert "一次性任务" in prompt


def test_long_term_memory_update_enforces_structured_budget(tmp_path):
    class FakeLLM:
        def __init__(self):
            self.calls = []

        def invoke(self, messages):
            self.calls.append(messages[0].content)
            return type("Response", (), {"content": "x" * 1500})()

    llm = FakeLLM()
    memory_file = tmp_path / "AGENTS.md"

    updated = update_long_term_memory_after_session(
        memory_file,
        user_message="帮我写一句问候语",
        assistant_response="你好，祝你今天顺利！",
        llm=llm,
    )

    assert updated is True
    assert len(memory_file.read_text(encoding="utf-8")) <= 1500
    assert "不超过 1500 字符" in llm.calls[0]
    assert "不超过 1500 个字符" in llm.calls[1]


def test_long_term_memory_prompt_forbids_expert_role_scope():
    """用户级长期记忆会注入所有专家，提示词必须明确禁止写入角色职责/拒答规则。"""
    prompt = build_long_term_memory_update_prompt(
        current_memory="",
        user_message="我只做利率债投研",
        assistant_response="已记录。",
    )

    assert "禁止记入" in prompt
    assert "拒答" in prompt
    assert "职责范围" in prompt


def test_strip_role_scope_clauses_removes_expert_boundaries_but_keeps_identity():
    """剔除「只答/不覆盖/越界」类子句，同时保住同一行里的身份信息。"""
    text = (
        "# 用户长期记忆\n\n"
        "## 工作背景\n"
        "- 金融：16年国债、国开、地方债利率债投研；只接利率债/宏观利率，"
        "亦不覆盖汇率/外汇，越界不臆断。\n"
        "- 资深Java架构师，擅长微服务与智能体。\n"
    )

    cleaned, dropped = strip_role_scope_clauses(text)

    # 身份留下、边界被剔除
    assert "16年国债" in cleaned
    assert "资深Java架构师" in cleaned
    assert "不覆盖汇率" not in cleaned
    assert "越界" not in cleaned
    assert "只接利率债" not in cleaned
    assert len(dropped) >= 2


def test_strip_role_scope_clauses_drops_whole_bullet_when_nothing_left():
    """整条都是角色边界时，连列表符号一起删掉，不留空壳行。"""
    text = "## 个人背景\n- 代码委托仅限利率债投研内，领域外Java微服务等不做。\n- 偏好结论先行。\n"

    cleaned, dropped = strip_role_scope_clauses(text)

    assert "仅限利率债" not in cleaned
    assert "领域外" not in cleaned
    assert "偏好结论先行" in cleaned
    assert all(line.strip() not in ("-", "*", "+") for line in cleaned.splitlines())
    assert dropped


def test_long_term_memory_update_strips_role_scope_written_by_llm(tmp_path):
    """模型仍把角色边界写进来时，落盘前必须剔除（不依赖提示词自觉）。"""
    dirty = (
        "# 用户长期记忆\n\n"
        "## 工作背景\n"
        "- 资深Java架构师，擅长微服务。\n"
        "- 只接利率债/宏观利率，不覆盖汇率/外汇，汇率类问题一律拒答。\n"
    )

    class FakeLLM:
        def invoke(self, messages):
            return type("Response", (), {"content": dirty})()

    memory_file = tmp_path / "AGENTS.md"
    updated = update_long_term_memory_after_session(
        memory_file,
        user_message="我主要做利率债",
        assistant_response="已记录。",
        llm=FakeLLM(),
    )

    assert updated is True
    written = memory_file.read_text(encoding="utf-8")
    assert "资深Java架构师" in written
    assert "不覆盖汇率" not in written
    assert "拒答" not in written


def test_long_term_memory_not_clobbered_when_only_role_scope(tmp_path):
    """剔除后若整份记忆为空，不写入空文件（避免把已有记忆清空）。"""
    class FakeLLM:
        def invoke(self, messages):
            return type(
                "Response", (), {"content": "## 工作背景\n- 只答利率债，越界拒答。"}
            )()

    memory_file = tmp_path / "AGENTS.md"
    memory_file.write_text("# 用户长期记忆\n- 偏好表格输出。\n", encoding="utf-8")

    updated = update_long_term_memory_after_session(
        memory_file,
        user_message="x",
        assistant_response="y",
        llm=FakeLLM(),
    )

    assert updated is False
    assert "偏好表格输出" in memory_file.read_text(encoding="utf-8")
