"""E2：配置未知键检测 / strict fail-fast / 模板键集合测试。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

from easy_agent.config import (
    Config,
    find_deprecated_config_keys,
    find_unknown_config_keys,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.quality import check_config_schema  # noqa: E402


def _write_config(path: Path, extra: str = "") -> Path:
    path.write_text(
        """
model: test-model
models:
  test-model:
    provider: test
    api_key: "sk-test"
"""
        + extra,
        encoding="utf-8",
    )
    return path


def test_unknown_top_level_key_warns_and_still_loads(tmp_path, caplog):
    config_path = _write_config(tmp_path / "config.yaml", "unknown_key: 1\n")

    with caplog.at_level("WARNING", logger="easy_agent.config"):
        config = Config.from_yaml(config_path)

    assert config.llm.api_key == "sk-test"
    assert "unknown_key" in caplog.text


def test_unknown_section_key_is_detected(tmp_path):
    config_path = _write_config(
        tmp_path / "config.yaml",
        "web_search:\n  enabled: true\n  typo_search_depth: deep\n",
    )

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert find_unknown_config_keys(data) == ["web_search.typo_search_depth"]


def test_unknown_model_provider_key_is_detected(tmp_path):
    config_path = _write_config(
        tmp_path / "config.yaml",
        "",
    )
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["models"]["test-model"]["typo_api_key"] = "x"

    assert find_unknown_config_keys(data) == ["models.test-model.typo_api_key"]


def test_strict_env_makes_unknown_keys_fail(tmp_path, monkeypatch):
    config_path = _write_config(tmp_path / "config.yaml", "unknown_key: 1\n")
    monkeypatch.setenv("EASY_CONFIG_STRICT", "1")

    with pytest.raises(ValueError, match="unknown_key"):
        Config.from_yaml(config_path)


def test_legacy_and_deprecated_keys_do_not_fail(tmp_path, caplog):
    config_path = _write_config(
        tmp_path / "config.yaml",
        "system_prompt_path: system.md\n"
        "knowledge:\n"
        "  implementation: v2\n"
        "  enabled: false\n",
    )

    with caplog.at_level("WARNING", logger="easy_agent.config"):
        config = Config.from_yaml(config_path)

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert find_unknown_config_keys(data) == []
    assert "knowledge.implementation" in find_deprecated_config_keys(data)
    assert "已废弃" in caplog.text
    assert config.agent.prompt_path == "system.md"


def test_config_example_has_no_unknown_keys():
    example = PROJECT_ROOT / "easy_agent" / "config" / "config-example.yaml"
    data = yaml.safe_load(example.read_text(encoding="utf-8"))

    assert find_unknown_config_keys(data) == []
    assert "audit" in data["knowledge"]
    assert "limits" in data["knowledge"]
    assert "implementation" not in data["knowledge"]


def test_config_example_schema_and_local_drift_helpers(tmp_path):
    example = PROJECT_ROOT / "easy_agent" / "config" / "config-example.yaml"

    errors, _infos = check_config_schema.check(example, None)
    assert errors == []

    # 本地配置使用了模板未记录的键 -> 漂移错误（模板需要同步）。
    actual = tmp_path / "config.yaml"
    actual.write_text(
        "model: deepseek\nmodels:\n  deepseek:\n    api_key: sk-test\n"
        "brand_new_section:\n  enabled: true\n",
        encoding="utf-8",
    )
    errors, _infos = check_config_schema.check(example, actual)
    assert any("brand_new_section" in error for error in errors)


def test_config_example_documents_local_runtime_keys():
    """本地 config.yaml（若存在）使用到的键必须都能在模板中找到。

    CI 中 config.yaml 被 gitignore，不存在时该断言自然跳过。
    """
    example = PROJECT_ROOT / "easy_agent" / "config" / "config-example.yaml"
    actual = PROJECT_ROOT / "easy_agent" / "config" / "config.yaml"
    errors, _infos = check_config_schema.check(example, actual)
    assert errors == []
