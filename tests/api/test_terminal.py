"""SEC-01：Web 终端默认关闭；开启后首帧必须是 admin 的 JWT，鉴权通过前不创建 shell。

改造前 ``/agent/terminal/ws`` 无需认证，任何能访问服务的人都能拿到宿主机 bash。
测试用替身替换 ``_run_shell_session``，不 fork 真实 shell。
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from starlette.websockets import WebSocketDisconnect

import easy_agent.api.terminal as terminal_mod
from easy_agent.utils.auth import create_access_token

WS_PATH = "/agent/terminal/ws"


@pytest.fixture()
def shell_calls(monkeypatch):
    calls: list[str] = []

    async def fake_shell(websocket, web_user):
        calls.append(web_user)
        await websocket.send_text("shell-ready")
        await websocket.close()

    monkeypatch.setattr(terminal_mod, "_run_shell_session", fake_shell)
    return calls


@pytest.fixture()
def enabled(monkeypatch):
    monkeypatch.setenv("EASY_WEB_TERMINAL_ENABLED", "1")


def _token(db, username: str) -> str:
    if db.get_user_by_username(username) is None:
        db.register_user(username, "secret123")
    version = db.increment_user_token_version(username)
    return create_access_token(
        data={"sub": username, "v": version}, expires_delta=timedelta(minutes=30)
    )


def _auth_frame(token: str) -> str:
    return json.dumps({"type": "auth", "token": token})


def _assert_rejected(client, first_frame: str, *, code: int, text: str) -> None:
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_text(first_frame)
        assert text in ws.receive_text()
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
    assert closed.value.code == code


def test_disabled_by_default(client, monkeypatch, shell_calls):
    monkeypatch.delenv("EASY_WEB_TERMINAL_ENABLED", raising=False)
    assert client.get("/terminal").status_code == 404
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(WS_PATH):
            pass
    assert shell_calls == []


def test_page_served_when_enabled(client, enabled):
    resp = client.get("/terminal")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"].lower()
    assert "type: 'auth'" in resp.text


@pytest.mark.parametrize(
    "first_frame",
    ["", "ls -la\n", json.dumps({"type": "auth"}), _auth_frame("not-a-jwt")],
)
def test_rejects_missing_or_invalid_token(client, enabled, shell_calls, first_frame):
    _assert_rejected(client, first_frame, code=4401, text="未登录")
    assert shell_calls == []


def test_rejects_non_admin(client, db, enabled, shell_calls):
    _assert_rejected(client, _auth_frame(_token(db, "alice")), code=4403, text="仅管理员")
    assert shell_calls == []


def test_rejects_superseded_admin_token(client, db, enabled, shell_calls):
    """单点登录：admin 在别处重新登录后，旧 token 不能再开终端。"""
    stale = _token(db, "admin")
    db.increment_user_token_version("admin")
    _assert_rejected(client, _auth_frame(stale), code=4401, text="未登录")
    assert shell_calls == []


def test_admin_gets_shell(client, db, enabled, shell_calls):
    with client.websocket_connect(WS_PATH) as ws:
        ws.send_text(_auth_frame(_token(db, "admin")))
        assert ws.receive_text() == "shell-ready"
    assert shell_calls == ["admin"]
