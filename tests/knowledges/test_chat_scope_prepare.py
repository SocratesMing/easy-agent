"""新建会话首条消息携带 knowledge_base_ids：先落 scope 再检索。"""

from types import SimpleNamespace

from easy_agent.knowledges.chat import chat_bridge
from easy_agent.knowledges.core.config import KnowledgeConfig
from easy_agent.knowledges.core.models import RetrieveResponse
from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.knowledges.service import KnowledgeService
from easy_agent.models.db import SessionModel

from .test_folder_upload import _FakeRagflow, _create_base_row, _principal


def _request() -> SimpleNamespace:
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                knowledge_config=KnowledgeConfig(
                    enabled=True, base_url="http://ragflow", api_key="rk"
                ),
                ragflow_client=_FakeRagflow(),
            )
        )
    )


async def test_prepare_applies_requested_scope(monkeypatch, db):
    """knowledge_base_ids 只负责写入范围；没有实际 scope 时不触发检索。"""

    calls = {}

    class FakeService:
        def __init__(self, repository, ragflow, config):
            self.repository = repository

        async def replace_session_scope(self, **kwargs):
            calls["replace"] = kwargs

        async def retrieve_session_evidence(self, **kwargs):
            raise AssertionError("scope 未落库时不应检索")

        async def get_session_document_catalog(self, **kwargs):
            raise AssertionError("scope 未落库时不应检索")

    principal = _principal()

    def fake_principal(request, database):
        return principal

    monkeypatch.setattr(chat_bridge, "KnowledgeService", FakeService)
    monkeypatch.setattr(chat_bridge, "get_knowledge_principal", fake_principal)

    context = await chat_bridge.prepare_knowledge_chat(
        request=_request(),
        db=db,
        session_id="session-1",
        question="看看研报",
        knowledge_base_ids=["base-1", "base-2"],
    )

    assert calls["replace"]["session_id"] == "session-1"
    assert calls["replace"]["principal"] == principal
    assert calls["replace"]["base_ids"] == ["base-1", "base-2"]
    assert context.context is None
    assert context.evidence == []


async def test_prepare_persists_scope_with_real_service(monkeypatch, db):
    """真实服务链路：首条消息提交的范围会写进 session_knowledge_scopes。"""

    base = _create_base_row(db)
    principal = _principal()
    session_id = "session-scope"
    db.create_session(
        SessionModel(
            session_id=session_id,
            title="新会话",
            messages=[],
            created_at="2026-10-27T00:00:00",
            updated_at="2026-10-27T00:00:00",
            username=principal.username,
            workspace_name=f"ws-{session_id}",
        )
    )

    def fake_principal(request, database):
        return principal

    async def fake_retrieve(self, **kwargs):
        return RetrieveResponse(
            evidence=[], request_id=str(kwargs.get("request_id") or "test")
        )

    async def fake_catalog(self, **kwargs):
        return []

    monkeypatch.setattr(chat_bridge, "get_knowledge_principal", fake_principal)
    monkeypatch.setattr(
        KnowledgeService, "retrieve_session_evidence", fake_retrieve
    )
    monkeypatch.setattr(
        KnowledgeService, "get_session_document_catalog", fake_catalog
    )

    context = await chat_bridge.prepare_knowledge_chat(
        request=_request(),
        db=db,
        session_id=session_id,
        question="看看研报",
        knowledge_base_ids=[str(base["id"])],
    )

    scope = KnowledgeRepository(db).get_session_scope(
        session_id=session_id, user_id=principal.user_id
    )
    assert scope == [str(base["id"])]
    assert context.evidence == []


def test_is_metadata_summary_question_matches_only_the_exact_question():
    assert chat_bridge.is_metadata_summary_question("概括这批资料的情况信息")
    assert chat_bridge.is_metadata_summary_question(" 概括这批资料的情况信息？ ")
    assert not chat_bridge.is_metadata_summary_question("概括这批资料的核心结论")
    assert not chat_bridge.is_metadata_summary_question(
        "概括这批资料的情况信息并引用原文"
    )


async def test_metadata_summary_question_uses_catalog_without_retrieval(monkeypatch, db):
    """资料概况问题只读二级来源元数据：不走向量检索，也不产出知识依据。"""

    base = _create_base_row(db)
    principal = _principal()
    session_id = "session-summary"
    db.create_session(
        SessionModel(
            session_id=session_id,
            title="新会话",
            messages=[],
            created_at="2026-10-27T00:00:00",
            updated_at="2026-10-27T00:00:00",
            username=principal.username,
            workspace_name=f"ws-{session_id}",
        )
    )
    calls = {}

    async def fake_retrieve(self, **kwargs):
        raise AssertionError("资料概况问题不应检索原文")

    async def fake_catalog(self, **kwargs):
        calls["include_metadata"] = kwargs.get("include_metadata")
        return [
            {
                "base_id": str(base["id"]),
                "base_name": "研报库",
                "document_name": "宏观周报.pdf",
                "status": "ready",
                "report_second_source": "券商",
            },
            {
                "base_id": str(base["id"]),
                "base_name": "研报库",
                "document_name": "临时笔记.docx",
                "status": "ready",
                "report_second_source": None,
            },
        ]

    monkeypatch.setattr(
        chat_bridge, "get_knowledge_principal", lambda request, database: principal
    )
    monkeypatch.setattr(KnowledgeService, "retrieve_session_evidence", fake_retrieve)
    monkeypatch.setattr(KnowledgeService, "get_session_document_catalog", fake_catalog)

    context = await chat_bridge.prepare_knowledge_chat(
        request=_request(),
        db=db,
        session_id=session_id,
        question="概括这批资料的情况信息",
        knowledge_base_ids=[str(base["id"])],
    )

    assert calls["include_metadata"] is True
    assert context.evidence == []
    assert context.warnings == []
    assert "二级来源=券商" in context.context
    assert "临时笔记.docx；无二级来源元数据，仅依据文档名称概括" in context.context
    assert "不得出现 [知识依据N]" in context.context
    assert "不要提及同步渠道" in context.context
    assert "source_sync" not in context.context
    assert "报告标题" not in context.context
