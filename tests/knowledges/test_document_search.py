"""关键字搜索 / 状态筛选覆盖整个知识库的回归测试。

搜索框文案是「搜索当前知识库」：传了关键字或解析状态时必须跨全部目录检索，
folder_id / direct_only 不能把结果限制在当前目录。
"""

from easy_agent.knowledges.core.repository import KnowledgeRepository

from .test_folder_upload import (
    _FakeRagflow,
    _create_base_row,
    _endpoint_client,
    _make_service,
    _principal,
    _upload,
)


async def test_search_query_ignores_folder_scope(db, tmp_path):
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)
    await _upload(
        service, str(base["id"]),
        filename="deep-q1.txt", relative_path="资料/报告/deep-q1.txt",
    )
    await service.ensure_folders(
        base_id=str(base["id"]),
        folder_id=None,
        paths=["空目录"],
        principal=_principal(),
    )

    repository = KnowledgeRepository(db)
    folders = {f["name"]: f for f in repository.list_folders(str(base["id"]))}

    # 模拟旧前端/外部调用仍带当前目录参数：搜索关键字应忽略目录范围
    response = await service.list_documents(
        base_id=str(base["id"]),
        principal=_principal(),
        page=1,
        page_size=50,
        folder_id=folders["空目录"]["id"],
        direct_only=True,
        status=None,
        query="  q1  ",
        request_id="req-search",
    )

    assert [item.name for item in response.items] == ["deep-q1.txt"]
    assert response.page.total == 1

    # 无关键字时仍按目录浏览
    scoped = await service.list_documents(
        base_id=str(base["id"]),
        principal=_principal(),
        page=1,
        page_size=50,
        folder_id=folders["空目录"]["id"],
        direct_only=True,
        status=None,
        query=None,
        request_id="req-browse",
    )
    assert scoped.items == []


async def test_status_filter_ignores_folder_scope(db, tmp_path):
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)
    await _upload(
        service, str(base["id"]),
        filename="broken.txt", relative_path="资料/报告/broken.txt",
    )
    await service.ensure_folders(
        base_id=str(base["id"]),
        folder_id=None,
        paths=["空目录"],
        principal=_principal(),
    )

    repository = KnowledgeRepository(db)
    document = repository.list_documents(str(base["id"]))[0]
    repository.update_document(
        document["id"],
        status="failed",
        error_code="TEST_FAILURE",
        error_message="mock parse failure",
    )
    folders = {f["name"]: f for f in repository.list_folders(str(base["id"]))}

    # 模拟旧前端/外部调用仍带当前目录参数：状态筛选应忽略目录范围
    response = await service.list_documents(
        base_id=str(base["id"]),
        principal=_principal(),
        page=1,
        page_size=50,
        folder_id=folders["空目录"]["id"],
        direct_only=True,
        status="failed",
        query=None,
        request_id="req-status",
    )

    assert [item.name for item in response.items] == ["broken.txt"]
    assert response.page.total == 1


def test_search_endpoint_ignores_folder_scope(db, tmp_path):
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)
    client = _endpoint_client(service, _principal())

    upload = client.post(
        f"/agent/knowledge/v1/bases/{base['id']}/documents",
        files={"file": ("deep-q1.txt", b"endpoint content", "text/plain")},
        data={"folder_id": "", "relative_path": "资料/报告/deep-q1.txt"},
    )
    assert upload.status_code == 202
    ensured = client.post(
        f"/agent/knowledge/v1/bases/{base['id']}/folders/ensure",
        json={"folder_id": None, "paths": ["空目录"]},
    )
    assert ensured.status_code == 200

    repository = KnowledgeRepository(db)
    folders = {f["name"]: f for f in repository.list_folders(str(base["id"]))}
    response = client.get(
        f"/agent/knowledge/v1/bases/{base['id']}/documents",
        params={
            "folder_id": folders["空目录"]["id"],
            "direct_only": "true",
            "q": "q1",
        },
    )

    assert response.status_code == 200
    assert [item["name"] for item in response.json()["items"]] == ["deep-q1.txt"]


def test_status_filter_endpoint_ignores_folder_scope(db, tmp_path):
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)
    client = _endpoint_client(service, _principal())

    upload = client.post(
        f"/agent/knowledge/v1/bases/{base['id']}/documents",
        files={"file": ("broken.txt", b"endpoint content", "text/plain")},
        data={"folder_id": "", "relative_path": "资料/报告/broken.txt"},
    )
    assert upload.status_code == 202
    ensured = client.post(
        f"/agent/knowledge/v1/bases/{base['id']}/folders/ensure",
        json={"folder_id": None, "paths": ["空目录"]},
    )
    assert ensured.status_code == 200

    repository = KnowledgeRepository(db)
    document = repository.list_documents(str(base["id"]))[0]
    repository.update_document(
        document["id"],
        status="failed",
        error_code="TEST_FAILURE",
        error_message="mock parse failure",
    )
    folders = {f["name"]: f for f in repository.list_folders(str(base["id"]))}
    response = client.get(
        f"/agent/knowledge/v1/bases/{base['id']}/documents",
        params={
            "folder_id": folders["空目录"]["id"],
            "direct_only": "true",
            "status": "failed",
        },
    )

    assert response.status_code == 200
    assert [item["name"] for item in response.json()["items"]] == ["broken.txt"]
