"""对象存储 A：``/agent/files`` 在 ``EASY_UPLOAD_BACKEND=store`` 下的全链路。

覆盖：上传写 Store URI 且删本地临时文件 → download → parse → preview → delete
清理 Store 对象；Store 不可用时上传 503 且不留本地文件/DB 记录。

注意：``client`` fixture 负责启动 lifespan（此时仍是默认 local，启动自检为空
操作），store 模式由本文件 fixture 在 lifespan 之后打开 —— 与"运行时切开关"的
真实语义一致。
"""

import os
from pathlib import Path

import pytest
from langgraph.store.memory import InMemoryStore

from easy_agent.services import upload_storage as us


@pytest.fixture()
def store_client(monkeypatch, client, tmp_path):
    """store 模式的 TestClient：InMemoryStore 替身 + 独立缓存目录。"""
    store = InMemoryStore()
    monkeypatch.setattr(us, "get_agent_store", lambda *args, **kwargs: store)
    monkeypatch.setattr(us, "BASE_CACHE_DIR", tmp_path / "uploads_cache")
    monkeypatch.setenv(us.ENV_UPLOAD_BACKEND, us.BACKEND_STORE)
    return client, store


def _upload(client, name="data.txt", data=b"hello store", session_id=None):
    params = {"session_id": session_id} if session_id else {}
    return client.post(
        "/agent/files/upload",
        files={"file": (name, data, "text/plain")},
        params=params,
    )


def _upload_dir() -> Path:
    return Path(os.environ["TEST_UPLOADS_DIR"]) / "testuser"


def _items(store):
    return [item.key for item in store.search(("uploads", "testuser"))]


def test_upload_returns_store_uri_and_cleans_local_file(store_client):
    client, store = store_client
    before = {p for p in _upload_dir().rglob("*") if p.is_file()}

    resp = _upload(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["file_path"].startswith("store://uploads/testuser/")
    assert body["filename"] == "data.txt"
    assert body["size"] == len(b"hello store")

    # Store 里有一份原始字节，本地 uploads 目录不留**本次新增**副本（不做双份存储）。
    # 注意 TEST_UPLOADS_DIR 是 session 级共享目录，其它用例可能已有文件。
    assert len(_items(store)) == 1
    after = {p for p in _upload_dir().rglob("*") if p.is_file()}
    assert after == before


def test_download_streams_materialized_bytes(store_client):
    client, _ = store_client
    up = _upload(client).json()

    resp = client.get(f"/agent/files/download/{up['file_path']}")
    assert resp.status_code == 200
    assert resp.content == b"hello store"


def test_parse_materializes_before_parsing(store_client):
    client, _ = store_client
    up = _upload(client).json()

    resp = client.post("/agent/files/parse", params={"file_path": up["file_path"]})
    assert resp.status_code == 200
    assert "hello store" in resp.json()["content"]
    assert resp.json()["file_path"] == up["file_path"]


def test_preview_serves_store_object_inline(store_client):
    client, _ = store_client
    up = _upload(client).json()

    # 预览接口走自己的 token 鉴权（不使用 get_current_username override），
    # 因此按既有用例的方式注册 testuser 并取真实 token。
    registered = client.post(
        "/api/auth/register",
        json={
            "username": "testuser",
            "password": "secret123",
            "organization_id": "test-org",
            "employee_id": "E-store-preview-testuser",
        },
    )
    assert registered.status_code == 200
    token = registered.json()["access_token"]

    resp = client.get(
        "/agent/files/preview",
        params={"file_path": up["file_path"], "token": token},
    )
    assert resp.status_code == 200
    assert resp.content == b"hello store"


def test_preview_download_flag_forces_attachment(store_client):
    client, _ = store_client
    up = _upload(client).json()

    registered = client.post(
        "/api/auth/register",
        json={
            "username": "testuser",
            "password": "secret123",
            "organization_id": "test-org",
            "employee_id": "E-store-preview-attach",
        },
    )
    token = registered.json()["access_token"]

    resp = client.get(
        "/agent/files/preview",
        params={"file_path": up["file_path"], "token": token, "download": True},
    )
    assert resp.status_code == 200
    assert "attachment" in resp.headers["content-disposition"]


def test_delete_removes_store_object(store_client):
    client, store = store_client
    sid = client.post("/agent/sessions", json={}).json()["session_id"]
    up = _upload(client, session_id=sid).json()
    assert up["file_path"].startswith("store://")
    assert len(_items(store)) == 1

    from easy_agent.db import get_database

    rows = get_database().get_session_files(sid)
    assert rows and rows[0]["file_path"] == up["file_path"]

    resp = client.delete(f"/agent/files/{rows[0]['id']}")
    assert resp.status_code == 200

    # DB 行与 Store 对象都清掉（缓存也清）
    assert get_database().get_session_files(sid) == []
    assert _items(store) == []
    assert not [p for p in Path(us.BASE_CACHE_DIR).rglob("*") if p.is_file()]


def test_upload_fails_closed_when_store_unavailable(store_client, monkeypatch):
    client, _ = store_client
    before = {p for p in _upload_dir().rglob("*") if p.is_file()}

    def _boom(*args, **kwargs):
        raise RuntimeError("store down")

    monkeypatch.setattr(us, "get_agent_store", _boom)

    resp = _upload(client)
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail["code"] == "UPLOAD_STORE_UNAVAILABLE"
    assert "store down" in detail["message"]

    # 不静默丢文件：本地临时文件已回滚（session 级共享目录中不新增文件），DB 无记录
    after = {p for p in _upload_dir().rglob("*") if p.is_file()}
    assert after == before


def test_download_missing_store_object_404(store_client):
    client, store = store_client
    up = _upload(client).json()

    # 直接删掉 Store 对象，模拟对象丢失（DB 记录仍在）
    us.upload_backend("testuser").delete(us.store_key(up["file_path"].rsplit("/", 1)[-1]))
    assert _items(store) == []

    resp = client.get(f"/agent/files/download/{up['file_path']}")
    assert resp.status_code == 404


def test_download_rejects_other_users_store_uri(store_client):
    client, _ = store_client
    resp = client.get("/agent/files/download/store://uploads/bob/abc12345_x.txt")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "UPLOAD_STORE_ACCESS_DENIED"


def test_real_sql_store_backend_roundtrip(client, monkeypatch, tmp_path):
    """真实业务库 SqlStore（sqlite）+ StoreBackend 的全链路（不是 fake store）。

    生产默认走 ``SqlStore``（langgraph_store 表，item_key VARCHAR(191)、
    value TEXT/LONGTEXT），仅用 InMemoryStore 覆盖不到「JSON 序列化 + base64 +
    namespace 分隔符」这条真实路径。
    """
    from easy_agent.persistence import stores

    monkeypatch.setattr(us, "BASE_CACHE_DIR", tmp_path / "uploads_cache")
    monkeypatch.setenv(us.ENV_UPLOAD_BACKEND, us.BACKEND_STORE)
    # 强制重建 Store，确保指向本次测试的临时库（不沿用其它用例缓存）
    monkeypatch.setattr(stores, "_store", None, raising=False)

    resp = _upload(client, name="sql.txt", data=b"sql-store-body")
    assert resp.status_code == 200, resp.text
    uri = resp.json()["file_path"]
    assert uri.startswith("store://uploads/testuser/")

    assert client.get(f"/agent/files/download/{uri}").content == b"sql-store-body"
    parsed = client.post("/agent/files/parse", params={"file_path": uri}).json()
    assert parsed["content"].strip() == "sql-store-body"


def test_local_mode_still_writes_local_file(client, monkeypatch, tmp_path):
    """回归：默认 local 模式下 file_path 仍是本地绝对路径，且文件真切落盘。"""
    monkeypatch.delenv(us.ENV_UPLOAD_BACKEND, raising=False)
    up = _upload(client, data=b"local-body").json()

    assert up["file_path"].startswith(str(_upload_dir()))
    assert Path(up["file_path"]).read_bytes() == b"local-body"
    assert client.get(f"/agent/files/download/{up['file_path']}").content == b"local-body"
