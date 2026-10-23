"""配置单一来源测试：模型声明的字段不需要额外的解析代码就能从 YAML 读到，已知键由模型生成。

改造前同一组字段在模型、手写解析、已知键列表里各写一份，出过「模型声明了字段、解析漏传」
（见 test_config_yaml.py 里 sandbox_enabled / result_log_truncate 两条历史 bug）。
"""

import pytest
import yaml

from easy_agent.bootstrap import config_validation
from easy_agent.bootstrap.config import Config
from easy_agent.bootstrap.config_models import (
    AgentConfig,
    DistributedLockConfig,
    FileContextConfig,
    MySQLConfig,
    MySQLPoolConfig,
    PersonnelConfig,
    ProviderConfig,
    SQLiteConfig,
    SummarizationConfig,
    ToolsConfig,
    UploadLimitsConfig,
    WebSearchConfig,
)

# YAML 里的位置 → 模型；空路径表示写在顶层（agent 字段）
SECTIONS = {
    "": AgentConfig,
    "tools": ToolsConfig,
    "summarization": SummarizationConfig,
    "web_search": WebSearchConfig,
    "distributed_lock": DistributedLockConfig,
    "upload_limits": UploadLimitsConfig,
    "file_context": FileContextConfig,
    "personnel": PersonnelConfig,
    "database.sqlite": SQLiteConfig,
    "database.mysql": MySQLConfig,
    "database.mysql.pool": MySQLPoolConfig,
    "models.demo": ProviderConfig,
}
# 读回解析结果的位置
PARSED = {"": "agent", "models.demo": "models"}


def _changed(default):
    """同类型的非默认值；容器类型（列表 / 字典 / 子模型）返回 None 表示跳过。"""
    if isinstance(default, bool):
        return not default
    if isinstance(default, int):
        return default + 7
    if isinstance(default, float):
        return default + 0.25
    if isinstance(default, str):
        return f"{default}-changed" if default else "changed"
    return None


def _scalar_fields(model):
    for name, info in model.model_fields.items():
        value = _changed(info.default)
        if value is not None:
            yield name, value


def _write(tmp_path, data):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _base():
    return {"model": "demo", "models": {"demo": {"api_key": "sk-test"}}}


def _resolve(parsed, path):
    target = parsed
    for part in PARSED.get(path, path).split("."):
        target = target[part] if isinstance(target, dict) else getattr(target, part)
    return target["demo"] if path == "models.demo" else target


def test_every_scalar_field_declared_on_a_model_is_read_from_yaml(tmp_path):
    data = _base()
    expected = {}
    for path, model in SECTIONS.items():
        section = data
        for part in [p for p in path.split(".") if p]:
            section = section.setdefault(part, {})
        for name, value in _scalar_fields(model):
            section[name] = value
            expected[(path, name)] = value

    parsed = Config.from_yaml(_write(tmp_path, data))

    for (path, name), value in expected.items():
        assert getattr(_resolve(parsed, path), name) == value, f"{path or '<顶层>'}.{name}"


def test_known_keys_are_generated_from_the_models():
    sections = config_validation.KNOWN_SECTION_KEYS
    for path, model in SECTIONS.items():
        if path == "":
            assert set(model.model_fields) <= config_validation.KNOWN_TOP_LEVEL_KEYS
        elif path == "models.demo":
            assert set(model.model_fields) <= config_validation.KNOWN_PROVIDER_KEYS
        else:
            assert sections[path] == frozenset(model.model_fields), path


@pytest.mark.parametrize(
    "extra, attribute",
    [
        ({"interrupt_sensitive_writes": "false"}, "agent.interrupt_sensitive_writes"),
        ({"expert_relevance_strict": "false"}, "expert_relevance_strict"),
        ({"expert": {"guardrail_enabled": "false"}}, "expert_guardrail_enabled"),
    ],
)
def test_string_false_switches_are_off(tmp_path, extra, attribute):
    """${VAR:-false} 展开后是字符串，以前用 bool(...) 取值会被当成 true。"""
    parsed = Config.from_yaml(_write(tmp_path, {**_base(), **extra}))
    target = parsed
    for part in attribute.split("."):
        target = getattr(target, part)
    assert target is False


@pytest.mark.parametrize("value", [None, ""])
def test_blank_switches_stay_off(tmp_path, value):
    """开关留空（null，或 ${VAR:-} 展开成空串）时与改造前一样按关闭处理。"""
    data = {**_base(), "interrupt_sensitive_writes": value, "expert_guardrail_enabled": value}

    parsed = Config.from_yaml(_write(tmp_path, data))

    assert parsed.agent.interrupt_sensitive_writes is False
    assert parsed.expert_guardrail_enabled is False


def test_flat_expert_key_wins_over_the_expert_section(tmp_path):
    data = {
        **_base(),
        "expert_relevance_strict": False,
        "expert": {"relevance_strict": True, "relevance_check_enabled": False},
    }

    parsed = Config.from_yaml(_write(tmp_path, data))

    assert parsed.expert_relevance_strict is False
    assert parsed.expert_relevance_check_enabled is False
    assert parsed.expert_guardrail_enabled is True
