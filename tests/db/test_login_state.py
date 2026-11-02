"""登录态校验（``easy_agent.db.login_state``）：拒绝原因、拒绝日志、登录强制写活跃时间。

主鉴权、query token 和知识库鉴权共用 ``verify_login_token``；这里按原因逐条断言状态码、
返回给前端的文案和服务端 WARNING 日志，并复现「登出后在另一个进程重新登录，新 token
第一个请求就 401」的节流竞态。
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from jose import jwt
from starlette.requests import Request

from easy_agent.db import login_state
from easy_agent.db.repositories.user_repository import UserRepository
from easy_agent.middleware import auth as auth_middleware
from easy_agent.utils.auth import SECRET_FINGERPRINT, create_access_token

REQUEST_SUMMARY = "GET /agent/auth/config | IP: 10.0.0.5 | request_id=rid-test"


def _config(idle_minutes):
    return {"config": SimpleNamespace(agent=SimpleNamespace(idle_logout_minutes=idle_minutes))}


def _user(**overrides):
    fields = {
        "username": "alice",
        "user_id": "u-alice",
        "token_version": 2,
        "account_status": "active",
        "organization_id": "",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


class _Store:
    """用户存储端口的替身：一个用户 + 一条活跃时间。"""

    def __init__(self, user=None, activity=None):
        self.user = user if user is not None else _user()
        self.activity = activity

    def get_user_by_username(self, username):
        return self.user if username == self.user.username else None

    def get_user_activity_time(self, username):
        return self.activity

    def touch_user_activity(self, username, timestamp):
        self.activity = timestamp
        return True

    def clear_user_activity(self, username):
        self.activity = None
        return True


def _request(token=None):
    headers = [] if token is None else [(b"authorization", f"Bearer {token}".encode())]
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/agent/auth/config",
            "headers": headers,
            "client": ("10.0.0.5", 50000),
            "query_string": b"",
        }
    )
    request.state.request_id = "rid-test"
    return request


def _token(sub="alice", v=2, **kwargs):
    data = {"v": v} if sub is None else {"sub": sub, "v": v}
    return create_access_token(data=data, **kwargs)


@pytest.fixture(autouse=True)
def _clear_throttle():
    login_state._activity_write_throttle.clear()
    yield
    login_state._activity_write_throttle.clear()


@pytest.fixture()
def idle_minutes(monkeypatch):
    def _set(minutes):
        monkeypatch.setattr(login_state, "get_runtime_agent_config", lambda: _config(minutes))

    _set(0)
    return _set


_OTHER_KEY_TOKEN = jwt.encode(
    {"sub": "alice", "v": 2, "iat": 1762000000},
    "another-replica-secret",
    algorithm="HS256",
    headers={"kid": "other-key"},
)

REJECTIONS = [
    pytest.param(None, None, 0, 401, login_state.EXPIRED_DETAIL, "请求没有携带 Bearer token", id="missing"),
    pytest.param("null", None, 0, 401, login_state.EXPIRED_DETAIL, "token 是字面量 'null'", id="literal-null"),
    pytest.param("abc.def", None, 0, 401, login_state.EXPIRED_DETAIL, "token 不是有效的 JWT", id="not-jwt"),
    pytest.param(
        _OTHER_KEY_TOKEN, None, 0, 401, login_state.EXPIRED_DETAIL,
        f"token 由另一把密钥签发（kid='other-key'，本进程 {SECRET_FINGERPRINT}）", id="other-key",
    ),
    pytest.param(_token(sub=None), None, 0, 401, login_state.EXPIRED_DETAIL, "token 里没有用户名", id="no-sub"),
    pytest.param(_token(sub="bob"), None, 0, 401, login_state.EXPIRED_DETAIL, "用户不存在 | 用户: bob", id="unknown-user"),
    pytest.param(
        _token(), _Store(_user(account_status="disabled")), 0, 403, login_state.DISABLED_DETAIL,
        "账号已停用 | 用户: alice", id="disabled",
    ),
    pytest.param(
        _token(v=1), None, 0, 401, login_state.KICKED_DETAIL,
        "token 版本 1 与当前版本 2 不符", id="kicked",
    ),
    pytest.param(
        _token(), _Store(activity=None), 5, 401, login_state.EXPIRED_DETAIL,
        "空闲登出已开启（5 分钟），库里没有该用户的活跃记录", id="idle-no-record",
    ),
    pytest.param(
        _token(), _Store(activity=time.time() - 61), 1, 401, login_state.EXPIRED_DETAIL,
        "超过空闲登出上限 60 秒", id="idle-timeout",
    ),
]


@pytest.mark.parametrize("token, store, minutes, status, detail, reason", REJECTIONS)
def test_rejection_status_detail_and_reason_log(
    token, store, minutes, status, detail, reason, idle_minutes, caplog
):
    idle_minutes(minutes)
    request = _request(token)
    caplog.set_level(logging.WARNING, logger=login_state.__name__)

    with pytest.raises(login_state.LoginRejected) as exc_info:
        login_state.verify_login_token(
            login_state.bearer_token(request), store or _Store(), request=request
        )

    assert exc_info.value.status_code == status
    assert exc_info.value.detail == detail
    assert exc_info.value.headers == ({"WWW-Authenticate": "Bearer"} if status == 401 else None)
    assert f"[鉴权] 拒绝 {status} | 原因: " in caplog.text
    assert reason in caplog.text
    assert REQUEST_SUMMARY in caplog.text
    if token and len(token) > 20:
        assert token not in caplog.text


def test_undecodable_token_logs_claimed_user_as_unverified(caplog):
    caplog.set_level(logging.WARNING, logger=login_state.__name__)

    with pytest.raises(login_state.LoginRejected):
        login_state.verify_login_token(_OTHER_KEY_TOKEN, _Store())

    assert "用户: 'alice'（未验签，签发于 " in caplog.text
    assert "| -" in caplog.text  # 没传 request 时请求摘要写 -


def test_forged_claims_cannot_inject_log_lines(caplog):
    forged = jwt.encode(
        {"sub": "eve\n[鉴权] 伪造的一行", "iat": 10**20}, "x", algorithm="HS256",
        headers={"kid": "k\nFAKE"},
    )
    caplog.set_level(logging.WARNING, logger=login_state.__name__)

    with pytest.raises(login_state.LoginRejected):
        login_state.verify_login_token(forged, _Store())

    assert len(caplog.records) == 1
    assert "\n" not in caplog.records[0].getMessage()


def test_token_past_exp_is_accepted_and_renews_activity(idle_minutes):
    idle_minutes(5)
    store = _Store(activity=time.time() - 30)
    expired = _token(expires_delta=timedelta(minutes=-5))

    user = login_state.verify_login_token(expired, store)

    assert user.username == "alice"
    assert store.activity > time.time() - 2


def test_tokens_carry_the_key_fingerprint_as_kid():
    assert jwt.get_unverified_header(_token())["kid"] == SECRET_FINGERPRINT


@pytest.mark.parametrize(
    "header, expected",
    [
        ("Bearer abc", "abc"),
        ("bearer  abc ", "abc"),
        ("Basic abc", None),
        ("Bearer ", None),
        (None, None),
    ],
)
def test_bearer_token_parsing(header, expected):
    headers = {} if header is None else {"Authorization": header}
    assert login_state.bearer_token(SimpleNamespace(headers=headers)) == expected


def test_main_auth_maps_rejection_to_http_error(idle_minutes):
    with pytest.raises(HTTPException) as exc_info:
        auth_middleware.get_current_username(_request(_token(v=1)), _Store())

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == login_state.KICKED_DETAIL
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


def test_query_token_check_returns_none_and_logs(caplog):
    caplog.set_level(logging.WARNING, logger=login_state.__name__)

    assert auth_middleware.verify_token_sso(_token(v=1), _Store(), _request()) is None
    assert "token 版本 1 与当前版本 2 不符" in caplog.text
    assert REQUEST_SUMMARY in caplog.text


def test_forced_touch_bypasses_the_write_throttle():
    store = _Store(activity=None)
    login_state._activity_write_throttle["alice"] = time.time()

    login_state.touch_user_activity(store, "alice")
    assert store.activity is None

    login_state.touch_user_activity(store, "alice", force=True)
    assert store.activity is not None


def test_relogin_on_another_process_after_logout_keeps_new_token_valid(
    auth_client, db, idle_minutes
):
    """登出清空了库里的活跃时间；重新登录落在 30 秒内写过该用户的进程上，也必须落库。"""
    idle_minutes(5)
    db.register_user(username="alice", password="secret-pass")

    def login():
        resp = auth_client.post(
            "/agent/auth/login", json={"username": "alice", "password": "secret-pass"}
        )
        assert resp.status_code == 200, resp.text
        return {"Authorization": f"Bearer {resp.json()['access_token']}"}

    first = login()
    assert auth_client.get("/agent/auth/config", headers=first).status_code == 200
    assert auth_client.post("/agent/auth/logout", headers=first).status_code == 200
    assert UserRepository(db).get_user_activity_time("alice") is None

    # 登出只清掉了处理登出的进程的节流记录；另一个进程 30 秒内刚写过 alice
    login_state._activity_write_throttle["alice"] = time.time()
    second = login()

    assert UserRepository(db).get_user_activity_time("alice") is not None
    resp = auth_client.get("/agent/auth/config", headers=second)
    assert resp.status_code == 200, resp.text
