"""知识库配置的环境变量覆盖（RAGFlow 接入 / rerank / 研报同步 / 研报 SQL 连接）。"""

from __future__ import annotations

from easy_agent.knowledges.core.config import KnowledgeConfig


def test_env_overrides_ragflow_source_sync_and_sql(monkeypatch):
    """入库/检索只读 YAML；RAGFlow、研报同步与研报 SQL 仍可由环境变量覆盖。"""

    monkeypatch.delenv("KNOWLEDGE_ENABLED", raising=False)
    monkeypatch.setenv("KNOWLEDGE_RETRIEVAL_PAGE_SIZE", "42")
    monkeypatch.setenv("KNOWLEDGE_RETRIEVAL_TOP_N", "12")
    monkeypatch.setenv("RAGFLOW_RERANK_MODEL", "bge-reranker@xinference")
    monkeypatch.setenv("KNOWLEDGE_MAX_FILE_SIZE_MB", "7")
    monkeypatch.setenv("KNOWLEDGE_AUDIT_ENABLED", "false")
    monkeypatch.setenv("KNOWLEDGE_SOURCE_SYNC_LOOKBACK_DAYS", "30")
    monkeypatch.setenv("COL_SQL_PORT", "3307")

    config = KnowledgeConfig.from_mapping(
        {
            "enabled": False,
            "limits": {"max_file_size_mb": 100},
            "audit": {"enabled": True},
            "retrieval": {"page_size": 30, "top_n": 8, "rerank_model": ""},
            "source_sync": {"lookback_days": 7, "mysql": {"port": 3306}},
        }
    )

    assert config.retrieval.page_size == 30
    assert config.retrieval.top_n == 8
    assert config.retrieval.rerank_model == "bge-reranker@xinference"
    assert config.limits.max_file_size_mb == 100
    assert config.audit.enabled is True
    assert config.source_sync.lookback_days == 30
    assert config.source_sync.mysql.port == 3307


def test_template_sentinel_env_is_ignored(monkeypatch):
    """模板哨兵值（EASY_AGENT_SET_ME__*）不能被当成真实配置。"""

    monkeypatch.delenv("KNOWLEDGE_ENABLED", raising=False)
    monkeypatch.setenv(
        "RAGFLOW_RERANK_MODEL", "EASY_AGENT_SET_ME__RAGFLOW_RERANK_MODEL"
    )

    config = KnowledgeConfig.from_mapping({"retrieval": {"rerank_model": ""}})

    assert config.retrieval.rerank_model == ""


def test_allowed_extensions_accepts_yaml_csv_string():
    """allowed_extensions 支持 YAML 里的逗号分隔字符串或列表。"""

    config = KnowledgeConfig.from_mapping(
        {"limits": {"allowed_extensions": "PDF, Docx ,txt"}}
    )
    assert config.limits.allowed_extensions == ["pdf", "docx", "txt"]

    config = KnowledgeConfig.from_mapping(
        {"limits": {"allowed_extensions": ["pdf", "md"]}}
    )
    assert config.limits.allowed_extensions == ["pdf", "md"]
