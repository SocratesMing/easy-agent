"""系统提示词契约测试。

校验的是**实际生效**的提示词（由 prompt_loader 加载 system.md + fragments），
而不是某个具体文件，这样调整文件组织方式时测试不会失效。

显式传入包内 config 目录，避免受其他测试（启动 app 会 configure_prompts_dir）
设置的全局提示词目录影响。
"""

from pathlib import Path

import pytest

from easy_agent.services.prompt_loader import clear_cache, load_system_prompt

CONFIG_DIR = Path(__file__).resolve().parent.parent / "easy_agent" / "config"


@pytest.fixture(autouse=True)
def _isolated():
    clear_cache()
    yield
    clear_cache()


def _load() -> str:
    return load_system_prompt(config_dir=CONFIG_DIR)


def test_system_prompt_matches_easy_agent_workflow():
    prompt = _load()

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
    prompt = _load()

    assert "create_scheduled_task" in prompt
    assert "task_prompt" in prompt
