"""非空文件夹删除的单元/端点测试。

覆盖：删除文件夹子树时同步删除远端文档、软删除本地文档、保留同级目录；
远端失败时本地保持原状；端点返回 204。
"""

import pytest

from easy_agent.knowledges.core.repository import KnowledgeRepository
from easy_agent.knowledges.ragflow.errors import RagflowError
from easy_agent.knowledges.service import KnowledgeServiceError

from .test_folder_upload import (
    _FakeRagflow,
    _create_base_row,
    _endpoint_client,
    _make_service,
    _principal,
    _upload,
)


class _DeleteRecordingRagflow(_FakeRagflow):
    """记录远端删除调用的假客户端。"""

    def __init__(self):
        super().__init__()
        self.deleted: list[list[str]] = []

    async def delete_documents(self, dataset_id, document_ids):
        self.deleted.append(list(document_ids))


class _DeleteFailingRagflow(_DeleteRecordingRagflow):
    async def delete_documents(self, dataset_id, document_ids):
        raise RagflowError("upstream failure")


async def test_delete_folder_with_content_removes_subtree_and_soft_deletes_documents(
    db, tmp_path,
):
    ragflow = _DeleteRecordingRagflow()
    service = _make_service(db, ragflow, tmp_path)
    base = _create_base_row(db)

    await _upload(
        service, str(base["id"]),
        filename="q1.txt", relative_path="资料/报告/q1.txt",
    )
    await service.ensure_folders(
        base_id=str(base["id"]),
        folder_id=None,
        paths=["保留"],
        principal=_principal(),
    )

    repository = KnowledgeRepository(db)
    folders = {f["name"]: f for f in repository.list_folders(str(base["id"]))}
    document = repository.list_documents(str(base["id"]))[0]
    remote_id = document["remote_document_id"]
    assert remote_id

    await service.delete_folder(
        folder_id=folders["资料"]["id"], principal=_principal()
    )

    assert {f["name"] for f in repository.list_folders(str(base["id"]))} == {"保留"}
    assert repository.list_documents(str(base["id"])) == []
    deleted = repository.get_document(document["id"])
    assert deleted["status"] == "deleted"
    assert deleted["deleted_by"] == "u-1"
    assert deleted["folder_id"] is None
    assert deleted["remote_document_id"] is None
    assert ragflow.deleted and remote_id in ragflow.deleted[0]


async def test_delete_folder_remote_failure_keeps_local_tree(db, tmp_path):
    service = _make_service(db, _DeleteFailingRagflow(), tmp_path)
    base = _create_base_row(db)
    await _upload(
        service, str(base["id"]),
        filename="q1.txt", relative_path="资料/q1.txt",
    )

    repository = KnowledgeRepository(db)
    folder = repository.list_folders(str(base["id"]))[0]
    with pytest.raises(KnowledgeServiceError) as exc_info:
        await service.delete_folder(folder_id=folder["id"], principal=_principal())

    assert exc_info.value.code == "KNOWLEDGE_UPSTREAM_ERROR"
    assert len(repository.list_folders(str(base["id"]))) == 1
    assert len(repository.list_documents(str(base["id"]))) == 1


def test_delete_non_empty_folder_endpoint_returns_204(db, tmp_path):
    service = _make_service(db, _DeleteRecordingRagflow(), tmp_path)
    base = _create_base_row(db)
    client = _endpoint_client(service, _principal())

    response = client.post(
        f"/agent/knowledge/v1/bases/{base['id']}/documents",
        files={"file": ("q1.txt", b"endpoint content", "text/plain")},
        data={"folder_id": "", "relative_path": "资料/q1.txt"},
    )
    assert response.status_code == 202

    repository = KnowledgeRepository(db)
    folder = repository.list_folders(str(base["id"]))[0]
    response = client.delete(f"/agent/knowledge/v1/folders/{folder['id']}")

    assert response.status_code == 204
    assert repository.list_folders(str(base["id"])) == []
    assert repository.list_documents(str(base["id"])) == []
