"""原文访问工具的 base_id 解析回归测试。

模型经常把知识库名称当成 base_id 传入。工具应默认使用会话已绑定的
知识范围，并在会话范围内按名称精确解析，而不是返回笼统的
「知识资源不存在」。
"""

from __future__ import annotations

import pytest

from easy_agent.knowledges.chat import original_tool as original_tool_module
from easy_agent.knowledges.chat.original_tool import KnowledgeOriginalTool
from easy_agent.knowledges.core.config import KnowledgeConfig
from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.models.db import SessionModel, UserModel


USER_ID = "u-original-1"
USERNAME = "alice"
SESSION_ID = "s-original-1"


def _config() -> KnowledgeConfig:
    return KnowledgeConfig(enabled=True, base_url="http://ragflow", api_key="rk")


def _create_user(db) -> None:
    db.create_user(
        UserModel(
            user_id=USER_ID,
            username=USERNAME,
            password_hash="",
            organization_id="",
            created_at="2026-10-27T00:00:00",
            updated_at="2026-10-27T00:00:00",
        )
    )


def _create_session(db) -> None:
    db.create_session(
        SessionModel(
            session_id=SESSION_ID,
            title="[知识库问答] 研报",
            messages=[],
            created_at="2026-10-27T00:00:00",
            updated_at="2026-10-27T00:00:00",
            username=USERNAME,
            workspace_name=f"ws-{SESSION_ID}",
        )
    )


def _create_base_with_original(db, *, name: str = "研报") -> tuple[str, str]:
    repository = KnowledgeRepository(db)
    base = repository.create_base(
        name=name,
        description="",
        space_type="personal",
        owner_user_id=USER_ID,
        department_id=None,
        embedding_model="",
    )
    base = repository.update_base(str(base["id"]), status="active")
    document = repository.create_document(
        base_id=str(base["id"]),
        folder_id=None,
        name=f"{name}.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        created_by=USER_ID,
        status="ready",
    )
    repository.create_original_object(
        document_id=str(document["id"]),
        provider="local",
        storage_key=f"originals/{document['id']}",
        original_name=f"{name}.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        created_by=USER_ID,
    )
    repository.update_original_object(str(document["id"]), status="available")
    return str(base["id"]), str(document["id"])


def _bind_scope(db, base_id: str) -> None:
    KnowledgeRepository(db).replace_session_scope(
        session_id=SESSION_ID,
        user_id=USER_ID,
        base_ids=[base_id],
    )


def _make_tool(
    db, tmp_path, monkeypatch, *, ragflow_client=None
) -> KnowledgeOriginalTool:
    monkeypatch.setattr(original_tool_module, "get_database", lambda: db)
    return KnowledgeOriginalTool(
        username=USERNAME,
        session_id=SESSION_ID,
        workspace_dir=str(tmp_path),
        knowledge_config=_config(),
        original_store=None,
        ragflow_client=ragflow_client,
    )


@pytest.fixture()
def scoped_tool(db, tmp_path, monkeypatch):
    _create_user(db)
    _create_session(db)
    base_id, document_id = _create_base_with_original(db)
    _bind_scope(db, base_id)
    return _make_tool(db, tmp_path, monkeypatch), base_id, document_id


def test_list_without_base_id_uses_session_scope(scoped_tool):
    tool, base_id, document_id = scoped_tool

    result = tool._run(action="list")

    assert "可用原文 1 份" in result
    assert "知识库：研报" in result
    assert f"base_id={base_id}" in result
    assert f"document_id={document_id}" in result
    assert "知识资源不存在" not in result


def test_list_accepts_base_name_from_session_scope(scoped_tool):
    """复现线上调用：模型把知识库名称「研报」当成 base_id。"""

    tool, base_id, document_id = scoped_tool

    result = tool._run(action="list", base_id="研报")

    assert f"base_id={base_id}" in result
    assert f"document_id={document_id}" in result
    assert "知识资源不存在" not in result


def test_list_unknown_name_returns_candidates(scoped_tool):
    tool, base_id, _ = scoped_tool

    result = tool._run(action="list", base_id="不存在的库")

    assert "未找到知识库" in result
    assert f"base_id={base_id}" in result
    assert "不要继续猜测" in result


def test_list_without_scope_returns_actionable_message(db, tmp_path, monkeypatch):
    _create_user(db)
    _create_session(db)
    tool = _make_tool(db, tmp_path, monkeypatch)

    result = tool._run(action="list")

    assert "当前会话未绑定知识库" in result
    assert "选择器" in result
    assert "知识资源不存在" not in result


class _FakeRagflowChunks:
    def __init__(self):
        self.list_datasets_calls: list[dict] = []
        self.list_chunks_calls: list[dict] = []

    async def list_datasets(self, **kwargs):
        self.list_datasets_calls.append(kwargs)
        return {"datasets": [{"id": "shared-1", "name": "EA_SHARED"}], "total": 1}

    async def list_chunks(
        self, dataset_id, document_id, *, page=1, page_size=100
    ):
        self.list_chunks_calls.append(
            {
                "dataset_id": dataset_id,
                "document_id": document_id,
                "page": page,
                "page_size": page_size,
            }
        )
        return {
            "chunks": [
                {
                    "chunk_order_int": 2,
                    "content_with_weight": "第二段",
                },
                {
                    "chunk_order_int": 1,
                    "content_with_weight": "第一段",
                },
            ],
            "total": 2,
        }


async def test_extract_returns_parsed_text_in_chunk_order(db, tmp_path, monkeypatch):
    _create_user(db)
    _create_session(db)
    base_id, document_id = _create_base_with_original(db)
    _bind_scope(db, base_id)
    KnowledgeRepository(db).update_document(document_id, remote_document_id="rd-1")
    ragflow = _FakeRagflowChunks()
    tool = _make_tool(db, tmp_path, monkeypatch, ragflow_client=ragflow)

    result = await tool._arun(action="extract", document_id=document_id)

    assert "文档：研报.pdf" in result
    assert result.index("第一段") < result.index("第二段")
    assert ragflow.list_datasets_calls
    assert ragflow.list_chunks_calls[0]["dataset_id"] == "shared-1"
    assert ragflow.list_chunks_calls[0]["document_id"] == "rd-1"
