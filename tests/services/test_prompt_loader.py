"""提示词加载器测试：目录模式、片段拼接、旧格式兼容、缺失回落、缓存与占位符。"""

from __future__ import annotations

import os

import pytest

from easy_agent.services.prompt_loader import (
    DEFAULT_SYSTEM_PROMPT,
    clear_cache,
    configure_prompts_dir,
    get_prompts_dir,
    load_prompt,
    load_system_prompt,
    render,
)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """每个用例使用独立的 prompts 目录并清空缓存。"""
    empty = tmp_path / "isolated-prompts"
    monkeypatch.setenv("EASY_PROMPTS_DIR", str(empty))
    clear_cache()
    yield
    clear_cache()


def _make_prompts(tmp_path, main="SYSTEM", fragments=None):
    prompts = tmp_path / "prompts"
    prompts.mkdir(parents=True, exist_ok=True)
    (prompts / "system.md").write_text(main, encoding="utf-8")
    if fragments:
        frag_dir = prompts / "fragments"
        frag_dir.mkdir(exist_ok=True)
        for name, content in fragments.items():
            (frag_dir / name).write_text(content, encoding="utf-8")
    return prompts


def test_directory_mode_concatenates_fragments(tmp_path, monkeypatch):
    prompts = _make_prompts(
        tmp_path, main="SYSTEM", fragments={"a_first.md": "FRAG_A", "b_second.md": "FRAG_B"}
    )
    monkeypatch.setenv("EASY_PROMPTS_DIR", str(prompts))
    clear_cache()

    result = load_system_prompt()

    assert "SYSTEM" in result
    assert "FRAG_A" in result and "FRAG_B" in result
    # 片段按文件名字典序拼接
    assert result.index("FRAG_A") < result.index("FRAG_B")


def test_config_dir_takes_priority(tmp_path, monkeypatch):
    """显式 config_dir 下的 prompts 优先于 EASY_PROMPTS_DIR。"""
    config_root = tmp_path / "config"
    prompts = _make_prompts(config_root, main="FROM_CONFIG_DIR")
    other = tmp_path / "other"
    other.mkdir()
    (other / "system.md").write_text("FROM_ENV", encoding="utf-8")
    monkeypatch.setenv("EASY_PROMPTS_DIR", str(other))
    clear_cache()

    result = load_system_prompt(config_dir=config_root)

    assert "FROM_CONFIG_DIR" in result
    assert "FROM_ENV" not in result


def test_legacy_single_file_mode(tmp_path, monkeypatch):
    """提示词目录不存在时，兼容旧的 system_prompt_path 单文件。"""
    legacy = tmp_path / "system_prompt.md"
    legacy.write_text("LEGACY PROMPT", encoding="utf-8")
    monkeypatch.setenv("EASY_PROMPTS_DIR", str(tmp_path / "missing"))
    clear_cache()

    assert load_system_prompt(prompt_path=str(legacy)) == "LEGACY PROMPT"


def test_relative_legacy_path_resolved_against_config_dir(tmp_path, monkeypatch):
    config_root = tmp_path / "config"
    config_root.mkdir()
    (config_root / "system_prompt.md").write_text("RELATIVE", encoding="utf-8")
    monkeypatch.setenv("EASY_PROMPTS_DIR", str(tmp_path / "missing"))
    clear_cache()

    result = load_system_prompt(
        config_dir=config_root, prompt_path="system_prompt.md"
    )

    assert result == "RELATIVE"


def test_falls_back_to_builtin_when_nothing_found(tmp_path, monkeypatch):
    monkeypatch.setenv("EASY_PROMPTS_DIR", str(tmp_path / "missing"))
    clear_cache()

    assert load_system_prompt() == DEFAULT_SYSTEM_PROMPT
    assert load_system_prompt(prompt_path=str(tmp_path / "nope.md")) == (
        DEFAULT_SYSTEM_PROMPT
    )


def test_cache_refreshes_after_file_change(tmp_path, monkeypatch):
    prompts = _make_prompts(tmp_path, main="V1")
    main_file = prompts / "system.md"
    monkeypatch.setenv("EASY_PROMPTS_DIR", str(prompts))
    clear_cache()

    assert load_system_prompt().strip() == "V1"

    main_file.write_text("V2", encoding="utf-8")
    # 确保 mtime 与上一次不同（避免同时间戳导致缓存未失效）
    st = main_file.stat()
    os.utime(main_file, (st.st_atime, st.st_mtime + 10))

    assert load_system_prompt().strip() == "V2"


def test_load_prompt_returns_none_when_missing(monkeypatch):
    assert load_prompt("definitely_not_exists") is None


def test_load_prompt_reads_file(tmp_path, monkeypatch):
    prompts = _make_prompts(tmp_path)
    (prompts / "memory_update.md").write_text("UPDATE $target_chars", encoding="utf-8")
    monkeypatch.setenv("EASY_PROMPTS_DIR", str(prompts))
    clear_cache()

    assert load_prompt("memory_update") == "UPDATE $target_chars"


def test_prompt_path_points_to_custom_directory(tmp_path, monkeypatch):
    """配置项 agent.prompt_path 指向任意目录名时也能读取。"""
    custom = tmp_path / "my-prompts"
    custom.mkdir()
    (custom / "system.md").write_text("CUSTOM SYSTEM", encoding="utf-8")
    (custom / "memory_update.md").write_text("CUSTOM MEMORY", encoding="utf-8")
    monkeypatch.setenv("EASY_PROMPTS_DIR", str(tmp_path / "missing"))
    clear_cache()

    assert "CUSTOM SYSTEM" in load_system_prompt(prompt_path=str(custom))
    # 相对路径基于 config_dir 解析
    assert "CUSTOM SYSTEM" in load_system_prompt(
        prompt_path="my-prompts", config_dir=tmp_path
    )


def test_configure_prompts_dir_affects_memory_prompts(tmp_path, monkeypatch):
    """设置提示词目录后，记忆类提示词也从该目录读取。"""
    custom = tmp_path / "prompts"
    custom.mkdir()
    (custom / "memory_update.md").write_text("FROM CONFIGURED", encoding="utf-8")
    monkeypatch.setenv("EASY_PROMPTS_DIR", str(tmp_path / "missing"))
    clear_cache()

    configure_prompts_dir(str(custom))

    assert get_prompts_dir() == custom
    assert load_prompt("memory_update") == "FROM CONFIGURED"


def test_render_substitutes_placeholders():
    out = render("限制 $target_chars 字符", target_chars=600)
    assert out == "限制 600 字符"


def test_render_is_immune_to_json_braces():
    """提示词正文含 JSON 示例时不应报错（str.format 会抛 KeyError）。"""
    template = '示例: {"servers": {"a": 1}}，上限 $target_chars'
    out = render(template, target_chars=800)
    assert '{"servers": {"a": 1}}' in out
    assert "800" in out


def test_render_leaves_unknown_placeholder_intact():
    """safe_substitute 不会因未知占位符抛异常。"""
    assert render("$known", known="v") == "v"
