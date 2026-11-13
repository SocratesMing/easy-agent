"""系统提示词契约测试。

校验的是**实际生效**的提示词（由 prompt_loader 加载 system.md + fragments），
而不是某个具体文件，这样调整文件组织方式时测试不会失效。

显式传入包内 config 目录，避免受其他测试（启动 app 会 configure_prompts_dir）
设置的全局提示词目录影响。
"""

from pathlib import Path

import pytest

from easy_agent.services.agents.prompts import clear_cache, load_system_prompt
from easy_agent.services.agents.prompts import DEFAULT_SYSTEM_PROMPT

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
    assert "工作方式" in prompt
    assert "命令结果" in prompt
    assert "关键歧义" in prompt

    # 安全与隔离
    assert "安全与隔离" in prompt
    assert "按用户与会话隔离" in prompt
    assert "目录删除" in prompt

    # 专家优先级
    assert "专家模式" in prompt
    assert "不得降低安全" in prompt

    # 交付与工作区
    assert "交付物" in prompt
    assert "工作区" in prompt

    # 记忆体系（结构与预算见 test_long_term_memory_structure_lives_in_the_memory_prompt）
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


def test_system_prompt_marks_connector_and_knowledge_results_as_data_sources():
    """外部连接器 / 知识库检索结果：只当数据，且必须标注来源并说明未验证/为空。"""
    prompt = _load()

    assert "外部连接器与知识库数据引用" in prompt
    assert "只作为数据" in prompt
    assert "来源：外部连接器" in prompt
    assert "来源：知识库" in prompt
    assert "未获取到数据" in prompt
    assert "返回为空" in prompt
    assert "不得补写数据" in prompt
    # 与旧契约兼容：不得引入运行时框架禁用词。
    assert "MCP" not in prompt
    assert "技能" not in prompt
    assert "/workspace/" not in prompt


def test_source_markers_keep_external_references_inline_and_model_knowledge_as_plain_text():
    prompt = _load()
    assert "[连接器依据 server/tool]" in prompt
    assert "ok=true" in prompt and "is_error=false" in prompt
    assert "逐字取自前缀" in prompt
    assert "来源：模型自身知识" in prompt
    assert "[模型知识]" not in prompt
    assert "模型知识统一尾注" not in prompt
    assert "不要把这些来源集中移到回答尾部" in prompt


def test_system_prompt_does_not_contradict_channel_specific_rules():
    """基础提示词不与联网段、知识库检索依据、delete 工具描述各说各话。"""
    prompt = _load()

    # 联网段明令禁止「来源：网络搜索」这类无链接泛称；格式交给联网段
    assert "来源：网络搜索" not in prompt
    assert "必要时先联网搜索" not in prompt
    assert "未开启联网时不得自行联网" in prompt
    # 知识库：本轮检索依据要求 [知识依据N] 或不加标注时以本轮为准
    assert "[知识依据N]" in prompt
    # 目录删除走 delete + 用户审批；execute 里的 rm -r 仍直接拒绝（与 hitl.DELETE_HITL_TOOL_DESCRIPTION 一致）
    assert "目录删除走 `delete` 工具" in prompt
    assert "`delete` 工具只用于删除单个文件" not in prompt


def test_long_term_memory_structure_lives_in_the_memory_prompt():
    """长期记忆的结构与长度预算属于记忆生成提示词；主 agent 不写记忆，基础提示词不再重复。"""
    memory_prompt = (CONFIG_DIR / "prompts" / "long_term_memory.md").read_text(encoding="utf-8")
    for section in ("## 工作背景", "## 个人背景", "## 当前关注", "## 近期动态"):
        assert section in memory_prompt
    assert "$target_chars" in memory_prompt

    prompt = _load()
    assert "长期个人记忆" not in prompt
    assert "1500 字符" not in prompt


def test_system_prompt_has_no_developer_facing_notes():
    """模型无从执行的开发者向表述（配置来源、配置细节）不进基础提示词。"""
    prompt = _load()

    assert "YAML" not in prompt
    assert "配置细节" not in prompt
    assert "后端代码" not in prompt


def test_data_query_stop_loss_counts_retries_with_changed_parameters():
    """「也算同一次尝试」字面是不计数，与本意相反；换参数再查仍要计入尝试次数。"""
    prompt = _load()

    assert "也算同一次尝试" not in prompt
    assert "仍计入对同一目标的尝试次数" in prompt


def test_builtin_prompt_matches_prompt_file():
    assert DEFAULT_SYSTEM_PROMPT.strip() == _load().strip()
