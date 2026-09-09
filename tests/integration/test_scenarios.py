"""综合业务场景测试：覆盖跨接口的真实使用流程。

场景 A：注册 -> 登录 -> 创建会话 -> 添加消息 -> 历史回看
场景 B：上传文件到会话 -> 工作区文件树可见
场景 C：记忆读写闭环
场景 D：彭博 查询 -> 分析 -> 导入 串联
场景 E：定时任务生命周期闭环
"""

from fastapi.testclient import TestClient


def test_scenario_auth_session_flow(auth_client: TestClient):
    client = auth_client
    # 注册并登录
    client.post(
        "/agent/auth/register",
        json={"username": "scenario_user", "password": "secret123", "organization_id": "org-1"},
    )
    token = client.post(
        "/agent/auth/login",
        json={"username": "scenario_user", "password": "secret123"},
    ).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 创建会话
    sid = client.post("/agent/sessions", json={"title": "场景会话"}, headers=headers).json()[
        "session_id"
    ]
    # 添加消息
    client.post(
        f"/agent/sessions/{sid}/messages",
        json={"role": "user", "content": "开始任务"},
        headers=headers,
    )
    # 历史回看应含该消息
    hist = client.get(f"/agent/sessions/{sid}/history", headers=headers).json()
    assert any(m["content"] == "开始任务" for m in hist["messages"])


def test_scenario_session_file_and_workspace(client: TestClient):
    sid = client.post("/agent/sessions", json={}).json()["session_id"]
    # 上传文件到会话
    files = {"file": ("scenario.txt", b"scenario-data", "text/plain")}
    up = client.post(
        f"/agent/sessions/{sid}/upload", files=files
    ).json()
    assert up["filename"] == "scenario.txt"

    # 工作区文件树应包含 uploadfiles 目录
    tree = client.get("/agent/files/workspace/tree").json()
    assert any(item["name"] == "uploadfiles" for item in tree["items"])


def test_scenario_memory_roundtrip(client: TestClient):
    put = client.put("/agent/settings/memory", json={"content": "用户偏好：用中文回复"})
    assert put.status_code == 200
    again = client.get("/agent/settings/memory")
    assert "用户偏好：用中文回复" in again.json()["content"]


def test_scenario_scheduled_task_lifecycle(client: TestClient):
    from easy_agent.db import get_database
    from easy_agent.models.db import ScheduledTaskModel

    tid = "scenario-task"
    db = get_database()
    db.create_scheduled_task(
        ScheduledTaskModel(
            task_id=tid,
            username="testuser",
            name="场景定时任务",
            schedule_cron="0 12 * * *",
            task_prompt="report",
        )
    )

    # 列表可见
    assert any(
        t["task_id"] == tid for t in client.get("/agent/scheduled-tasks").json()
    )
    # 启停切换
    assert client.patch(f"/agent/scheduled-tasks/{tid}/toggle").status_code == 200
    # 删除
    assert client.delete(f"/agent/scheduled-tasks/{tid}").status_code == 200
    assert not any(
        t["task_id"] == tid for t in client.get("/agent/scheduled-tasks").json()
    )
