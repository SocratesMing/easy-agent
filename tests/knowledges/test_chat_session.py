"""知识库隐藏问答会话：peek 只读探测不建记录，实际提问（默认调用）才创建。

回归背景：打开问答面板预取历史时误用创建式 prepare，导致点击知识库
即在会话列表生成空「[知识库问答]」记录，删除后重进又自动复活。
"""

from easy_agent.knowledges.api import _knowledge_chat_session_id

from .test_folder_upload import (
    _FakeRagflow,
    _create_base_row,
    _endpoint_client,
    _make_service,
    _principal,
)


def test_prepare_chat_session_peek_does_not_create(db, tmp_path):
    """peek=true 只读探测：会话不存在返回 404，不落库、不生成会话记录。"""
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)
    principal = _principal()
    client = _endpoint_client(service, principal)

    response = client.post(
        f"/agent/knowledge/v1/bases/{base['id']}/chat-session?peek=true"
    )
    assert response.status_code == 404
    assert (
        db.get_session(_knowledge_chat_session_id(principal, str(base["id"])))
        is None
    )


def test_prepare_chat_session_creates_then_peek_readable(db, tmp_path):
    """默认调用创建隐藏会话（幂等、ID 确定性）；创建后 peek 才可见。"""
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)
    principal = _principal()
    client = _endpoint_client(service, principal)
    expected_id = _knowledge_chat_session_id(principal, str(base["id"]))

    response = client.post(f"/agent/knowledge/v1/bases/{base['id']}/chat-session")
    assert response.status_code == 200
    assert response.json()["session_id"] == expected_id

    again = client.post(f"/agent/knowledge/v1/bases/{base['id']}/chat-session")
    assert again.status_code == 200
    assert again.json()["session_id"] == expected_id

    peek = client.post(
        f"/agent/knowledge/v1/bases/{base['id']}/chat-session?peek=true"
    )
    assert peek.status_code == 200
    assert peek.json()["session_id"] == expected_id


def test_rotate_keeps_old_session_and_switches_current(db, tmp_path):
    """开启新对话：旧会话保留为历史，当前轮切换到全新会话。"""
    service = _make_service(db, _FakeRagflow(), tmp_path)
    base = _create_base_row(db)
    principal = _principal()
    client = _endpoint_client(service, principal)

    first = client.post(f"/agent/knowledge/v1/bases/{base['id']}/chat-session")
    first_id = first.json()["session_id"]

    rotated = client.post(
        f"/agent/knowledge/v1/bases/{base['id']}/chat-session/new"
    )
    assert rotated.status_code == 200
    second_id = rotated.json()["session_id"]
    assert second_id != first_id

    # 旧会话保留为历史，不因轮换被删除
    assert db.get_session(first_id) is not None

    # prepare/peek 现在指向新的当前轮会话
    peek = client.post(
        f"/agent/knowledge/v1/bases/{base['id']}/chat-session?peek=true"
    )
    assert peek.status_code == 200
    assert peek.json()["session_id"] == second_id

    # 再轮换一次：第三轮，前两轮均保留
    third = client.post(
        f"/agent/knowledge/v1/bases/{base['id']}/chat-session/new"
    )
    third_id = third.json()["session_id"]
    assert third_id not in (first_id, second_id)
    assert db.get_session(first_id) is not None
    assert db.get_session(second_id) is not None


# ── SSE 适配器（C3）：首帧注入 + 终态解析 + 元数据落库 ──────────────────


class _FakeHostStream:
    """模拟宿主 ``chat_stream_generator`` 的 async generator + ``aclose``。"""

    def __init__(self, frames):
        self._frames = list(frames)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)

    async def aclose(self):
        self.closed = True


async def test_knowledge_chat_stream_injects_evidence_and_persists_metadata(
    monkeypatch,
):
    """知识适配器：宿主帧原样透传，首个事件后插入 evidence，终态后落元数据。"""
    from types import SimpleNamespace

    from easy_agent.knowledges.chat import streaming as knowledge_streaming
    from easy_agent.services.sse import format_sse, parse_sse_frame

    host_frames = [
        format_sse({"type": "start", "session_id": "kb-1"}),
        format_sse({"type": "content", "content": "答", "step": 0}),
        format_sse({"type": "done", "session_id": "kb-1"}),
    ]
    stream = _FakeHostStream(host_frames)

    def fake_host_generator(**kwargs):
        assert kwargs["parsed_content"].startswith(
            "CTX\n\n## 用户当前问题"
        )
        return stream

    monkeypatch.setattr(
        knowledge_streaming,
        "get_host_capabilities",
        lambda: SimpleNamespace(host_chat_stream_generator=fake_host_generator),
    )

    class _DB:
        def __init__(self):
            self.updates = []

        def get_session(self, session_id):
            return SimpleNamespace(
                messages=[{"role": "assistant", "content": "答"}]
            )

        def update_last_assistant_message(self, session_id, message):
            self.updates.append(("blob", message))

        def update_last_assistant_message_row(self, session_id, message):
            self.updates.append(("row", message))

    db = _DB()
    evidence = [{"snippet": "s"}]
    initial_events = [
        {"type": "knowledge_evidence", "evidence": evidence, "warnings": []}
    ]
    frames = [
        frame
        async for frame in knowledge_streaming.chat_stream_generator(
            context_prefix="CTX",
            initial_events=initial_events,
            assistant_metadata={"knowledge_evidence": evidence},
            request=SimpleNamespace(message="问"),
            db=db,
            session_id="kb-1",
            parsed_content="问",
        )
    ]

    payloads = [parse_sse_frame(frame) for frame in frames]
    assert [p["type"] for p in payloads] == [
        "start",
        "knowledge_evidence",
        "content",
        "done",
    ]
    # 知识证据帧与注册表/序列化器输出逐字节一致
    assert frames[1] == format_sse(initial_events[0])
    assert stream.closed is True
    # done 时落一次 + finally 再落一次（blob+row 双写）
    assert len(db.updates) == 4
    assert db.updates[0] == (
        "blob",
        {
            "role": "assistant",
            "content": "答",
            "knowledge_evidence": evidence,
        },
    )
