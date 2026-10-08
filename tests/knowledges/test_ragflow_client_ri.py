"""RAG 平台应用接入接口（RI001–RI011）客户端契约测试。

不依赖真实服务：用 httpx.MockTransport 捕获请求并回放固定信封响应，
验证《RAG平台应用接入接口文档 v1.1.1》要求的路径、方法、参数、请求头
（Authorization + stdsysserialnum）与统一响应编码（code=200 成功、
401/404/429/500/503 业务码）行为。
"""

import json
import re

import httpx
import pytest

from easy_agent.knowledges.ragflow.client import RagflowClient
from easy_agent.knowledges.ragflow.errors import (
    RagflowAuthenticationError,
    RagflowNotFoundError,
    RagflowRateLimitError,
    RagflowUnavailableError,
)

_API_KEY = "rk-test"
# stdsysserialnum = 令牌 + yyyyMMddHHmmss(14 位) + 六位随机数
_TRACE_RE = re.compile(rf"^{re.escape(_API_KEY)}\d{{14}}\d{{6}}$")


class _Capture:
    """记录全部请求，并按顺序回放预置响应（缺省 code=0 成功信封）。"""

    def __init__(self, responses: list[tuple[int, dict]] | None = None):
        self.requests: list[httpx.Request] = []
        self.responses = list(responses or [])

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.responses:
            status, payload = self.responses.pop(0)
        else:
            status, payload = 200, {"code": 0, "data": None}
        return httpx.Response(status, json=payload)

    @property
    def last(self) -> httpx.Request:
        assert self.requests, "未捕获到任何请求"
        return self.requests[-1]


def _client(capture: _Capture) -> RagflowClient:
    return RagflowClient(
        "http://ragflow.test", _API_KEY, transport=httpx.MockTransport(capture)
    )


# ─────────────────────────── 统一约定 ───────────────────────────


async def test_stdsysserialnum_header_on_every_request():
    capture = _Capture(
        [
            (200, {"code": 0, "data": {"datasets": [], "total": 0}}),
            (200, {"code": 0, "data": {"models": []}}),
        ]
    )
    async with _client(capture) as client:
        await client.list_datasets(page=1, page_size=1)
        await client.list_models()
    assert len(capture.requests) == 2
    for request in capture.requests:
        assert request.headers["authorization"] == f"Bearer {_API_KEY}"
        assert _TRACE_RE.match(request.headers["stdsysserialnum"])


async def test_envelope_code_200_is_success():
    capture = _Capture(
        [(200, {"code": 200, "data": {"datasets": [{"id": "d1"}], "total": 1}, "message": "success"})]
    )
    async with _client(capture) as client:
        result = await client.list_datasets()
    assert result == {"datasets": [{"id": "d1"}], "total": 1}


async def test_envelope_code_0_still_success():
    capture = _Capture([(200, {"code": 0, "data": {"doc_num": 3}, "message": "success"})])
    async with _client(capture) as client:
        result = await client.list_datasets()
    assert result == {"doc_num": 3}


@pytest.mark.parametrize(
    "code,exc_cls",
    [
        (401, RagflowAuthenticationError),
        (404, RagflowNotFoundError),
        (429, RagflowRateLimitError),
        (500, RagflowUnavailableError),
        (503, RagflowUnavailableError),
    ],
)
async def test_envelope_platform_error_codes(code, exc_cls):
    capture = _Capture([(200, {"code": code, "data": None, "message": "失败原因:xxx"})])
    async with _client(capture) as client:
        with pytest.raises(exc_cls):
            await client.list_datasets()


# ─────────────────────────── RI001 按资源目录上传文档 ───────────────────────────


async def test_upload_files_by_tree_ri001():
    capture = _Capture(
        [(
            200,
            {"code": 200, "data": [{"name": "a.pdf", "status": "ok", "error": "", "file": {"id": "f1"}}], "message": "success"},
        )]
    )
    async with _client(capture) as client:
        result = await client.upload_files_by_tree(
            files=[
                ("a.pdf", b"content-a", "application/pdf"),
                ("b.jpg", b"content-b", "image/jpeg"),
            ],
            tree_code="AIGCKP001001",
            metadata={"source": "AIGCKP"},
            datasets=["ds-1", "ds-2"],
        )
    request = capture.last
    assert request.method == "POST"
    assert request.url.path == "/api/v1/abac/files"
    body = request.read()
    # files 字段多文件重复提交
    assert body.count(b'name="files"') == 2
    assert b'name="tree_code"' in body and b"AIGCKP001001" in body
    assert b'name="metadata"' in body and b'"source": "AIGCKP"' in body
    # datasets 以重复表单字段提交
    assert body.count(b'name="datasets"') == 2
    assert result[0]["status"] == "ok"


async def test_upload_files_by_tree_ri001_validation():
    capture = _Capture()
    async with _client(capture) as client:
        with pytest.raises(ValueError):
            await client.upload_files_by_tree(files=[], tree_code="T1")
        with pytest.raises(ValueError):
            await client.upload_files_by_tree(files=[("a.pdf", b"x", "application/pdf")], tree_code="")


# ─────────────────────────── RI002 按资源目录上传 CBCM 文档 ───────────────────────────


async def test_upload_cbcm_files_by_tree_ri002():
    capture = _Capture(
        [(
            200,
            {"code": 200, "data": [{"name": "贷款管理办法.pdf", "status": "ok", "error": "", "file": {"id": "f2"}}], "message": "success"},
        )]
    )
    async with _client(capture) as client:
        result = await client.upload_cbcm_files_by_tree(
            tree_code="AIGCKP001001",
            service_id="SVC001",
            batch_id="20260916001",
            file_part="part-1",
            object_name="kb-docs",
            file_list=["10001", "10002"],
            metadata={"source": "CBCM"},
        )
    request = capture.last
    assert request.method == "POST"
    assert request.url.path == "/api/v1/abac/files/cbcm"
    payload = json.loads(request.read())
    assert payload == {
        "tree_code": "AIGCKP001001",
        "serviceId": "SVC001",
        "batchId": "20260916001",
        "filePart": "part-1",
        "objectName": "kb-docs",
        "FileList": ["10001", "10002"],
        "metadata": '{"source": "CBCM"}',
    }
    assert result[0]["file"]["id"] == "f2"


async def test_upload_cbcm_files_by_tree_ri002_required_fields():
    capture = _Capture()
    async with _client(capture) as client:
        with pytest.raises(ValueError):
            await client.upload_cbcm_files_by_tree(
                tree_code="T1",
                service_id="",
                batch_id="B1",
                file_part="P1",
                object_name="O1",
            )


# ─────────────────────────── RI003 按应用场景语义检索 ───────────────────────────


async def test_search_by_scene_ri003():
    capture = _Capture(
        [(
            200,
            {"code": 200, "data": {"chunks": [], "doc_aggs": [], "labels": None, "total": 0}, "message": "success"},
        )]
    )
    async with _client(capture) as client:
        result = await client.search_by_scene(
            scene_codes=["ADMIN_CHAT"],
            question="请介绍一下该场景的核心能力",
            top_k=5,
            meta_data_filter={
                "method": "manual",
                "logic": "and",
                "manual": [{"key": "label", "value": "RAG", "op": "="}],
            },
            rerank="Y",
        )
    request = capture.last
    assert request.method == "POST"
    assert request.url.path == "/api/v1/abac/search"
    payload = json.loads(request.read())
    assert payload["scene_code"] == ["ADMIN_CHAT"]
    assert payload["top_k"] == 5
    assert payload["rerank"] == "Y"
    assert payload["meta_data_filter"]["manual"][0]["key"] == "label"
    assert result["total"] == 0


async def test_search_by_scene_ri003_validation():
    capture = _Capture()
    async with _client(capture) as client:
        with pytest.raises(ValueError):
            await client.search_by_scene(scene_codes=[], question="q", top_k=5)
        with pytest.raises(ValueError):
            await client.search_by_scene(scene_codes=["S1"], question="q", top_k=0)
        with pytest.raises(ValueError):
            await client.search_by_scene(scene_codes=["S1"], question="q", top_k=5, rerank="X")


# ─────────────────────────── RI004 按应用场景查询可用知识库 ───────────────────────────


async def test_list_scene_knowledge_bases_ri004():
    capture = _Capture(
        [(
            200,
            {"code": 200, "data": {"items": [{"id": "kb1", "name": "RAGFlow升级方案"}], "total": 1, "page": 1, "page_size": 100}, "message": "success"},
        )]
    )
    async with _client(capture) as client:
        result = await client.list_scene_knowledge_bases(scene_code="ADMIN_CHAT", page=2)
    request = capture.last
    assert request.method == "GET"
    assert request.url.path == "/api/v1/abac/kb"
    assert request.url.params["scene_code"] == "ADMIN_CHAT"
    assert request.url.params["page"] == "2"
    assert result["total"] == 1


# ─────────────────────────── RI006 知识库文档上传（可选 metadata） ───────────────────────────


async def test_upload_documents_ri006_optional_metadata():
    capture = _Capture([(200, {"code": 200, "data": [{"id": "doc1", "run": "UNSTART"}], "message": "success"})])
    async with _client(capture) as client:
        result = await client.upload_documents(
            "ds-1",
            [("a.pdf", b"content", "application/pdf")],
            metadata={"department": "技术部"},
        )
    request = capture.last
    assert request.url.path == "/api/v1/datasets/ds-1/documents"
    body = request.read()
    assert b'name="file"' in body
    assert b'name="metadata"' in body and b'"department":' in body
    assert result[0]["id"] == "doc1"


async def test_upload_documents_ri006_without_metadata_unchanged():
    capture = _Capture([(200, {"code": 0, "data": [{"id": "doc1"}]})])
    async with _client(capture) as client:
        await client.upload_documents("ds-1", [("a.pdf", b"content", "application/pdf")])
    assert b"metadata" not in capture.last.read()


# ─────────────────────────── RI007 知识库文档列表查询 ───────────────────────────


async def test_list_documents_ri007_document_id_query_param():
    capture = _Capture([(200, {"code": 200, "data": {"docs": [{"id": "doc-9"}], "total": 1}, "message": "success"})])
    async with _client(capture) as client:
        result = await client.list_documents("ds-1", page=1, keywords="报告", document_id="doc-9")
    request = capture.last
    assert request.url.path == "/api/v1/datasets/ds-1/documents"
    # 文档规定按文档 id 精确过滤的查询参数名为 document_id
    assert request.url.params["document_id"] == "doc-9"
    assert request.url.params["keywords"] == "报告"
    assert result["docs"][0]["id"] == "doc-9"


# ─────────────────────────── RI009 知识库语义检索 ───────────────────────────


async def test_search_datasets_ri009():
    capture = _Capture(
        [(
            200,
            {"code": 200, "data": {"chunks": [{"chunk_id": "c1", "doc_id": "d1"}], "doc_aggs": [], "total": 1}, "message": "success"},
        )]
    )
    async with _client(capture) as client:
        result = await client.search_datasets(
            question="术语与缩写表",
            dataset_ids=["32d790eea67311f18328f3f161433efc"],
            top_k=5,
            page=1,
            size=10,
            similarity_threshold=0.2,
            vector_similarity_weight=0.3,
            highlight=True,
            use_kg=True,
        )
    request = capture.last
    assert request.method == "POST"
    assert request.url.path == "/api/v1/datasets/search"
    payload = json.loads(request.read())
    assert payload == {
        "question": "术语与缩写表",
        "dataset_ids": ["32d790eea67311f18328f3f161433efc"],
        "top_k": 5,
        "page": 1,
        "size": 10,
        "similarity_threshold": 0.2,
        "vector_similarity_weight": 0.3,
        "highlight": True,
        "use_kg": True,
    }
    assert result["total"] == 1


async def test_search_datasets_ri009_dataset_ids_required():
    capture = _Capture()
    async with _client(capture) as client:
        with pytest.raises(ValueError):
            await client.search_datasets(question="q", dataset_ids=[])


# ─────────────────────────── RI010 按资源目录删除文档 ───────────────────────────


async def test_delete_file_by_tree_ri010():
    capture = _Capture([(200, {"code": 200, "data": None, "message": "success"})])
    async with _client(capture) as client:
        await client.delete_file_by_tree("eb496ed8b0c611f1")
    request = capture.last
    assert request.method == "DELETE"
    assert request.url.path == "/api/v1/abac/files/eb496ed8b0c611f1"


async def test_delete_file_by_tree_ri010_validation():
    capture = _Capture()
    async with _client(capture) as client:
        with pytest.raises(ValueError):
            await client.delete_file_by_tree("")


# ─────────────────────────── RI011 知识库文档删除（原状确认） ───────────────────────────


async def test_delete_documents_ri011_contract_unchanged():
    capture = _Capture([(200, {"code": 200, "data": None, "message": "success"})])
    async with _client(capture) as client:
        await client.delete_documents("ds-1", ["doc-1", "doc-2"])
    request = capture.last
    assert request.method == "DELETE"
    assert request.url.path == "/api/v1/datasets/ds-1/documents"
    assert json.loads(request.read()) == {"ids": ["doc-1", "doc-2"]}
