"""系统提示词契约测试。

校验的是**实际生效**的提示词（由 prompt_loader 加载 system.md + fragments），
而不是某个具体文件，这样调整文件组织方式时测试不会失效。

显式传入包内 config 目录，避免受其他测试（启动 app 会 configure_prompts_dir）
设置的全局提示词目录影响。
"""

from pathlib import Path

import pytest

from easy_agent.services.prompt_loader import clear_cache, load_system_prompt
from easy_agent.services.prompt_loader import DEFAULT_SYSTEM_PROMPT

CONFIG_DIR = Path(__file__).resolve().parents[2] / "easy_agent" / "config"


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

    # 项目级规则；通用任务行为由 DeepAgents 默认提示词提供
    assert "中文" in prompt
    assert "破坏性操作" in prompt
    assert "密钥" in prompt

    # 记忆体系
    assert "系统自动维护" in prompt
    assert "不要手动改写记忆文件" in prompt

    # 已废弃的旧表述不应再出现
    assert "业务场景" not in prompt
    assert "2000" not in prompt


def test_system_prompt_excludes_scheduled_task_fragment():
    """定时任务使用说明由工具描述与参数说明提供，不重复拼接片段。"""
    prompt = _load()

    assert "create_scheduled_task" not in prompt
    assert "task_prompt" not in prompt


def test_system_prompt_defers_runtime_and_framework_details():
    """运行时路径与 DeepAgents 已注入能力不写入基础提示词。"""
    prompt = _load()

    assert "/workspace/" not in prompt
    assert "技能" not in prompt
    assert "MCP" not in prompt


def test_builtin_prompt_matches_prompt_file():
    assert DEFAULT_SYSTEM_PROMPT.strip() == _load().strip()
