"""接口 /agent/prompts 的测试：读取、更新、查询业务提示词。

SEC-08：提示词接口（query/read/update）必须登录且仅管理员可用；``read`` /
``update`` 的 ``path`` 解析后必须落在 prompts 目录内（绝对路径 / ``..`` 穿越 →
400「非法的文件路径」），不再能读写目录外的 ``.md`` 文件。
"""

import os
from pathlib import Path

import pytest

from easy_agent.app import app
from easy_agent.middleware import get_current_username


@pytest.fixture()
def admin_client(client):
    """把鉴权依赖换成 admin；用例结束恢复成 conftest 默认的 testuser。"""
    app.dependency_overrides[get_current_username] = lambda: "admin"
    try:
        yield client
    finally:
        app.dependency_overrides[get_current_username] = lambda: "testuser"


def _prompts_dir() -> Path:
    return Path(os.environ["TEST_PROMPTS_DIR"])


def _read(client, path=None):
    params = {"path": path} if path is not None else {}
    return client.post("/agent/prompts/read", params=params)


def _update(client, path, content="内容"):
    return client.post("/agent/prompts/update", json={"path": path, "content": content})


PROMPT_ROUTES = [
    ("query", lambda c: c.post("/agent/prompts/query")),
    ("read", lambda c: _read(c)),
    ("update", lambda c: _update(c, "unit_test_prompt", "x")),
]


@pytest.mark.parametrize("call", [r[1] for r in PROMPT_ROUTES], ids=[r[0] for r in PROMPT_ROUTES])
def test_prompts_require_auth(auth_client, call):
    """未携带有效凭证 → 401（SEC-08）。"""
    resp = call(auth_client)
    assert resp.status_code == 401


@pytest.mark.parametrize("call", [r[1] for r in PROMPT_ROUTES], ids=[r[0] for r in PROMPT_ROUTES])
def test_prompts_forbid_non_admin(client, call):
    """普通登录用户 → 403（SEC-08）。"""
    resp = call(client)
    assert resp.status_code == 403
    assert resp.json()["detail"] == "仅管理员可以操作提示词"


def test_admin_queries_prompt_list(admin_client):
    resp = admin_client.post("/agent/prompts/query")
    assert resp.status_code == 200
    data = resp.json()
    assert "data" in data
    assert isinstance(data["data"], list)


def test_admin_reads_default_prompt(admin_client):
    # read 接受 path 作为查询参数，缺省时使用默认路径
    resp = _read(admin_client)
    assert resp.status_code == 200
    body = resp.json()
    assert "path" in body
    assert "content" in body


def test_admin_updates_and_reads_prompt(admin_client):
    name = "unit_test_prompt"
    content = "你好，这是单元测试写入的提示词。"
    up = _update(admin_client, name, content)
    assert up.status_code == 200  # 返回字符串消息
    assert "写入完成" in up.json()

    resp = _read(admin_client, name)
    assert resp.status_code == 200
    assert content in resp.json()["content"]


def test_admin_can_write_nested_prompt(admin_client):
    """正常的多级子目录（如 expert/create_agent_guide）不受穿越校验影响。"""
    resp = _update(admin_client, "expert/unit_test_nested", "嵌套内容")
    assert resp.status_code == 200

    read = _read(admin_client, "expert/unit_test_nested")
    assert read.status_code == 200
    assert read.json()["content"] == "嵌套内容"


@pytest.mark.parametrize(
    "bad_path",
    [
        "../../etc/passwd",
        "../../../../tmp/t5_prompts_abs_write_probe",
        "/tmp/t5_prompts_abs_write_probe",
        "/etc/passwd",
    ],
)
def test_admin_read_rejects_paths_outside_prompts_dir(admin_client, bad_path):
    """绝对路径 / ``..`` 穿越 → 400「非法的文件路径」（SEC-08）。"""
    resp = _read(admin_client, bad_path)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "非法的文件路径"


@pytest.mark.parametrize(
    "bad_path",
    [
        "../sec08_outside_probe",
        "../../tmp/sec08_abs_probe",
        "/tmp/sec08_abs_probe",
    ],
)
def test_admin_update_rejects_paths_outside_prompts_dir(admin_client, bad_path):
    """update 的穿越 / 绝对路径不落盘（审计 PoC：任意目录写 .md）→ 400（SEC-08）。"""
    resp = _update(admin_client, bad_path, "越界写入")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "非法的文件路径"
    assert not (_prompts_dir() / ".." / "sec08_outside_probe.md").exists()


def test_admin_update_rejects_traversal_before_writing(admin_client, tmp_path):
    """相对 prompts 目录向上穿越到任意可写目录时，目标文件不应被创建（SEC-08）。"""
    outside = tmp_path / "sec08_traversal_probe.md"
    relative = os.path.relpath(outside.with_suffix(""), _prompts_dir())
    assert relative.startswith("..")

    resp = _update(admin_client, relative, "越界写入")
    assert resp.status_code == 400
    assert resp.json()["detail"] == "非法的文件路径"
    assert not outside.exists()
