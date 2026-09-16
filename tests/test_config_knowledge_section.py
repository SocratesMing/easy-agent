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
