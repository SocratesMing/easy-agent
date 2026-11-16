"""对话内可视化的提示词段与 visual 配置。

- 提示词按开关组装：总开关关掉时整段不出现，关掉 HTML 交互组件时不再教 ```widget；
- 装配顺序：在联网说明之后、专家补充内容之前（专家内容仍从属于系统规则）；
- visual 段能从 config.yaml 解析，未知键照常告警。
"""

from __future__ import annotations

from easy_agent.agent.visual_prompt import build_visual_prompt
from easy_agent.bootstrap import config_validation
from easy_agent.bootstrap.config import Config
from easy_agent.bootstrap.config_models import VisualConfig
from easy_agent.security.prompt_guard import assemble_system_prompt

_MINIMAL_CONFIG = """
model: test-model
models:
  test-model:
    provider: test-provider
    api_key: sk-test
"""


def test_prompt_teaches_all_three_blocks_by_default():
    prompt = build_visual_prompt(VisualConfig())

    for fence in ("```echarts", "```mermaid", "```widget"):
        assert fence in prompt
    assert "合法 JSON" in prompt
    assert "不要编造" in prompt
    assert "chart 字段" in prompt


def test_widgets_off_drops_only_the_widget_block():
    prompt = build_visual_prompt(VisualConfig(html_widgets=False))

    assert "```echarts" in prompt
    assert "```mermaid" in prompt
    assert "```widget" not in prompt


def test_master_switch_off_returns_nothing():
    assert build_visual_prompt(VisualConfig(enabled=False)) == ""
    assert build_visual_prompt(None) == build_visual_prompt(VisualConfig())


def test_visual_note_sits_between_web_search_and_expert_content():
    prompt = assemble_system_prompt(
        base_prompt="BASE",
        web_search_prompt="WEB",
        visual_note=build_visual_prompt(VisualConfig()),
        extra="EXPERT",
        hitl_note="HITL",
    )

    assert prompt.index("WEB") < prompt.index("```echarts") < prompt.index("EXPERT") < prompt.index("HITL")
    assert "图表与流程图" not in assemble_system_prompt(base_prompt="BASE", visual_note="")


def test_visual_section_parsed_from_yaml(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(_MINIMAL_CONFIG, encoding="utf-8")
    assert Config.from_yaml(path).visual == VisualConfig()

    path.write_text(_MINIMAL_CONFIG + "visual:\n  enabled: true\n  html_widgets: false\n", encoding="utf-8")
    visual = Config.from_yaml(path).visual
    assert visual.enabled is True
    assert visual.html_widgets is False


def test_visual_keys_are_known_and_typos_still_warn():
    data = {"visual": {"enabled": True, "html_widgets": False, "html_widget": True}}
    assert config_validation.find_unknown_config_keys(data) == ["visual.html_widget"]
