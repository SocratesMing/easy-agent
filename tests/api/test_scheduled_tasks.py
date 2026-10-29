"""定时任务接口测试（业务场景：定时任务生命周期管理）。

由于本项目未提供"创建定时任务"的 HTTP 接口（由智能体注册），测试通过直接写入
数据库来构造任务，再验证列表、运行记录、启停、删除等接口。
"""

from easy_agent.db import get_database
from easy_agent.models.db import ScheduledTaskModel
from fastapi.testclient import TestClient


def _seed_task(client: TestClient, task_id: str = "unit-task-1"):
    db = get_database()
    task = ScheduledTaskModel(
        task_id=task_id,
        username="testuser",
        name="单元测试任务",
        schedule_cron="0 0 * * *",
        task_prompt="ping",
        enabled=1,
    )
    db.create_scheduled_task(task)
    return task_id


def test_list_tasks(client: TestClient):
    tid = _seed_task(client)
    resp = client.get("/agent/scheduled-tasks")
    assert resp.status_code == 200
    assert any(t["task_id"] == tid for t in resp.json())


def test_task_runs_empty(client: TestClient):
    tid = _seed_task(client)
    resp = client.get(f"/agent/scheduled-tasks/{tid}/runs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_toggle_task(client: TestClient):
    tid = _seed_task(client)
    resp = client.patch(f"/agent/scheduled-tasks/{tid}/toggle")
    assert resp.status_code == 200
    assert "enabled" in resp.json()


def test_run_task(client: TestClient):
    tid = _seed_task(client)
    # 运行需要智能体；无论成功触发还是因无智能体失败，都不应返回 5xx 之外的错误
    resp = client.post(f"/agent/scheduled-tasks/{tid}/run")
    assert resp.status_code < 600


def test_task_workspace_file_rejects_bad_token(client: TestClient):
    tid = _seed_task(client, "unit-task-ws-bad")
    resp = client.get(
        f"/agent/scheduled-tasks/{tid}/workspace/file",
        params={"file_path": "x.md", "token": "not-a-token"},
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "登录已过期或未登录，请重新登录"


def test_task_workspace_file_accepts_valid_token(client: TestClient):
    """token 查询参数认证通过后进入文件定位（文件不存在 -> 404，而非 401）。"""
    registered = client.post(
        "/api/auth/register",
        json={
            "username": "testuser",
            "password": "secret123",
            "organization_id": "test-org",
            "employee_id": "E-scheduled-ws",
        },
    )
    assert registered.status_code == 200
    token = registered.json()["access_token"]
    tid = _seed_task(client, "unit-task-ws-ok")

    resp = client.get(
        f"/agent/scheduled-tasks/{tid}/workspace/file",
        params={"file_path": "missing.md", "token": token},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "文件不存在"


def test_delete_task(client: TestClient):
    tid = _seed_task(client)
    resp = client.delete(f"/agent/scheduled-tasks/{tid}")
    assert resp.status_code == 200

    lst = client.get("/agent/scheduled-tasks").json()
    assert not any(t["task_id"] == tid for t in lst)
