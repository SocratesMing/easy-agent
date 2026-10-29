"""E4：文档关键事实断言（以 docs/architecture-current.md 为准）。"""

from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (PROJECT_ROOT / rel).read_text(encoding="utf-8")


def test_architecture_current_is_the_fact_source():
    text = _read("docs/architecture-current.md")

    assert "config-example.yaml" in text
    assert "frontend-vue2/" in text
    assert "frontend/" in text
    assert "7 个运行时技能" in text
    assert "/user-skills/" in text


def test_agents_and_claude_dual_frontend_and_runtime_skills():
    for rel in ("AGENTS.md", "CLAUDE.md"):
        text = _read(rel)

        assert "frontend-vue2" in text
        assert "Vue 3.5" in text
        assert "运行时技能 7 个" in text
        assert "17 skills" not in text
        assert "17 个运行时技能" not in text
        assert "/skills/pptx" not in text
        assert "then `X-Username` header" not in text
        assert "JWT secret is ephemeral" not in text
        assert "No CI/CD or Docker" not in text
        assert "data/.jwt_secret" in text


def test_claude_configuration_points_to_existing_template():
    text = _read("CLAUDE.md")

    assert "config.yaml.example" not in text
    assert "config-example.yaml" in text
    assert "EASY_CONFIG_STRICT" in text
    assert "prompt_path" in text
    assert "/agent/chat/stream" in text


def test_readme_frontend_and_skills_facts():
    text = _read("README.md")

    assert "frontend-vue2/" in text
    assert "frontend/" in text
    assert "Vue 3.5" in text
    assert "7 个" in text
    assert "config-example.yaml" in text
    assert "17 skills" not in text
    assert "frontend/scripts/run-vite.mjs`，实际调用 Vue CLI" not in text


def test_config_example_documents_knowledge_runtime_keys():
    data = yaml.safe_load(
        _read("easy_agent/config/config-example.yaml")
    )
    knowledge = data["knowledge"]

    assert "audit" in knowledge
    assert "limits" in knowledge
    assert "implementation" not in knowledge
    assert knowledge["limits"]["max_documents_per_query"] == 500


def test_api_docs_generator_has_agent_prefix_and_check_mode():
    text = _read("scripts/docs/generate_api_docs.py")

    assert '("/agent/chat", "聊天")' in text
    assert '"--check"' in text
    assert "/api/chat" not in text


def test_config_schema_check_script_exists():
    assert (PROJECT_ROOT / "scripts" / "quality" / "check_config_schema.py").is_file()
    assert (PROJECT_ROOT / "scripts" / "quality" / "check_env_usage.py").is_file()


def test_framework_integration_doc_points_to_existing_code():
    """DA-17：框架适配清单引用的模块与符号必须存在（模块搬家后旧路径会悄悄失效）。"""
    import re

    text = _read("docs/framework-integration.md")
    paths = set(re.findall(r"easy_agent/[A-Za-z0-9_/]+\.py", text))
    assert paths
    assert sorted(p for p in paths if not (PROJECT_ROOT / p).is_file()) == []

    # 适配表「位置」列：`模块路径` `函数或类名`
    rows = re.findall(r"`(easy_agent/[A-Za-z0-9_/]+\.py)` `([A-Za-z_][A-Za-z0-9_]*)`", text)
    assert rows
    for path, symbol in rows:
        assert re.search(rf"^(?:async )?(?:def|class) {symbol}\b", _read(path), re.M), (path, symbol)
