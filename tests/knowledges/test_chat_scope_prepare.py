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

    async def fake_principal(request, database):
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

    async def fake_principal(request, database):
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
