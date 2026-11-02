"""原文工具 extract 路径不占事件循环：重建 principal、查文档、权限校验和审计写入都在线程里执行。"""

from __future__ import annotations

import threading

from easy_agent.knowledges.chat import original_tool as original_tool_module
from easy_agent.knowledges.core.repository import KnowledgeRepository

from .test_original_tool import (
    _FakeRagflowChunks,
    _bind_scope,
    _create_base_with_original,
    _create_session,
    _create_user,
    _make_tool,
)


async def test_extract_runs_database_work_off_the_event_loop(db, tmp_path, monkeypatch):
    _create_user(db)
    _create_session(db)
    base_id, document_id = _create_base_with_original(db)
    _bind_scope(db, base_id)
    KnowledgeRepository(db).update_document(document_id, remote_document_id="rd-1")
    tool = _make_tool(db, tmp_path, monkeypatch, ragflow_client=_FakeRagflowChunks())

    loop_thread = threading.get_ident()
    calls: dict[str, list[int]] = {"user": [], "document": [], "audit": []}

    def _spy(name, func):
        def wrapper(*args, **kwargs):
            calls[name].append(threading.get_ident())
            return func(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(db, "get_user_by_username", _spy("user", db.get_user_by_username))
    monkeypatch.setattr(
        KnowledgeRepository, "get_document", _spy("document", KnowledgeRepository.get_document)
    )
    audit_repo = original_tool_module.KnowledgeOperationsRepository
    monkeypatch.setattr(audit_repo, "record_audit", _spy("audit", audit_repo.record_audit))

    result = await tool._arun(action="extract", document_id=document_id)

    assert "第一段" in result
    for name, threads in calls.items():
        assert threads, f"{name} 没有被调用"
        assert loop_thread not in threads, f"{name} 在事件循环线程上执行"
