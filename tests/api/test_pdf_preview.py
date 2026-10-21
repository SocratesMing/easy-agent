"""Office 转 PDF 预览测试：会话工作区与定时任务工作目录两个预览接口共用同一套转换和错误映射。"""

import pytest

from easy_agent.bootstrap.config import Config
from easy_agent.db import get_database
from easy_agent.models.db import ScheduledTaskModel
from easy_agent.services import document_convert


def _token(client):
    registered = client.post(
        "/api/auth/register",
        json={
            "username": "testuser",
            "password": "secret123",
            "organization_id": "test-org",
            "employee_id": "E-pdf-preview",
        },
    )
    assert registered.status_code == 200
    return registered.json()["access_token"]


def _session_workspace_request(client):
    """会话工作区预览：不传 session_id 时就在用户工作区根目录下找文件。"""
    return "/agent/files/preview", Config.get_user_workspace_dir("testuser")


def _task_workspace_request(client):
    """定时任务工作目录预览：没有来源会话的任务用 ``scheduled_{task_id}`` 目录。"""
    task_id = "pdf-preview-task"
    get_database().create_scheduled_task(
        ScheduledTaskModel(
            task_id=task_id,
            username="testuser",
            name="预览测试任务",
            schedule_cron="0 0 * * *",
            task_prompt="ping",
            enabled=1,
        )
    )
    workspace = Config.get_user_workspace_dir("testuser") / "session" / f"scheduled_{task_id}"
    return f"/agent/scheduled-tasks/{task_id}/workspace/file", workspace


@pytest.fixture(params=[_session_workspace_request, _task_workspace_request], ids=["session", "task"])
def preview(request, client):
    """返回一个发起 target=pdf 预览请求的函数，文件 deck.pptx 已放进对应工作区。"""
    token = _token(client)
    url, workspace = request.param(client)
    workspace.mkdir(parents=True, exist_ok=True)
    source = workspace / "deck.pptx"
    source.write_bytes(b"pptx bytes")

    def _get():
        return client.get(url, params={"file_path": "deck.pptx", "token": token, "target": "pdf"})

    _get.source = source
    return _get


def test_office_file_is_converted_and_served_inline(preview, monkeypatch, tmp_path):
    pdf = tmp_path / "converted.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    calls = []

    def fake_convert(src, target="pdf"):
        calls.append((src, target))
        return pdf

    monkeypatch.setattr(document_convert, "convert_to_pdf", fake_convert)

    resp = preview()

    assert resp.status_code == 200
    assert resp.content == b"%PDF-1.4 fake"
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"] == 'inline; filename="deck.pdf"'
    assert [(src.resolve(), target) for src, target in calls] == [
        (preview.source.resolve(), "pdf")
    ]


def test_unsupported_file_type_is_400(preview, monkeypatch):
    def fake_convert(src, target="pdf"):
        raise ValueError("不支持转换为 PDF 的文件类型: .pptx")

    monkeypatch.setattr(document_convert, "convert_to_pdf", fake_convert)

    resp = preview()

    assert resp.status_code == 400
    assert resp.json()["detail"] == "不支持转换为 PDF 的文件类型: .pptx"


def test_conversion_failure_is_500(preview, monkeypatch):
    def fake_convert(src, target="pdf"):
        raise RuntimeError("soffice exited with 1")

    monkeypatch.setattr(document_convert, "convert_to_pdf", fake_convert)

    resp = preview()

    assert resp.status_code == 500
    assert resp.json()["detail"] == "文档转换失败: soffice exited with 1"
