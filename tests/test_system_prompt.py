"""系统提示词契约测试。

校验的是**实际生效**的提示词（由 prompt_loader 加载 system.md + fragments），
而不是某个具体文件，这样调整文件组织方式时测试不会失效。
"""

from easy_agent.services.prompt_loader import load_system_prompt


def test_system_prompt_matches_easy_agent_workflow():
    prompt = load_system_prompt()

    # 身份与能力范围
    assert "Easy Agent" in prompt
    assert "文档处理" in prompt
    assert "数据分析" in prompt
    assert "金融研究" in prompt

    # 运行环境
    assert "会话工作区 `/workspace/`" in prompt
    assert "技能" in prompt
    assert "MCP" in prompt

    # 行为准则
    assert "先检查现有文件" in prompt
    assert "验证结果" in prompt

    # 记忆体系（会话 600 / 长期 800 字符上限）
    assert "会话记忆" in prompt
    assert "用户长期记忆" in prompt
    assert "600" in prompt
    assert "800" in prompt

    # 已废弃的旧表述不应再出现
    assert "业务场景" not in prompt
    assert "2000" not in prompt


def test_system_prompt_includes_scheduled_task_fragment():
    """定时任务片段应拼接到系统提示词中。"""
    prompt = load_system_prompt()

    assert "create_scheduled_task" in prompt
    assert "task_prompt" in prompt
