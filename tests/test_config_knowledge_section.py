"""The shipped config example must contain a strict-valid ``knowledge:`` section.

Validating the example with no environment variables set is what proves a fresh
checkout can boot without any RAGFlow secrets.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_example_config_knowledge_section_valid(monkeypatch):
    from easy_agent.knowledge.config import KnowledgeConfig

    monkeypatch.delenv("RAGFLOW_BASE_URL", raising=False)
    monkeypatch.delenv("KNOWLEDGE_ENABLED", raising=False)
    cfg = KnowledgeConfig.from_yaml(REPO_ROOT / "easy_agent/config/config-example.yaml")
    assert cfg.enabled is False
    assert cfg.original_storage.enabled is False


async def test_startup_skips_original_store_when_disabled(monkeypatch):
    """Disabled knowledge must have zero side effects: no store, no mkdir, no probe."""

    from types import SimpleNamespace

    from easy_agent.knowledge import lifecycle
    from easy_agent.knowledge.config import KnowledgeConfig

    monkeypatch.delenv("KNOWLEDGE_ENABLED", raising=False)
    cfg = KnowledgeConfig.from_yaml(REPO_ROOT / "easy_agent/config/config-example.yaml")
    cfg.enabled = False
    cfg.original_storage.enabled = True

    calls = []
    monkeypatch.setattr(
        lifecycle.KnowledgeConfig, "load", classmethod(lambda cls, path=None: cfg)
    )
    monkeypatch.setattr(
        lifecycle, "create_original_store", lambda config: calls.append(config)
    )

    app = SimpleNamespace(state=SimpleNamespace())
    await lifecycle.startup_knowledge(app, "unused")

    assert calls == []
    assert app.state.original_store is None
