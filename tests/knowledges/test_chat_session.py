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
