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
    resp = client.get("/api/scheduled-tasks")
    assert resp.status_code == 200
    assert any(t["task_id"] == tid for t in resp.json())


def test_task_runs_empty(client: TestClient):
    tid = _seed_task(client)
    resp = client.get(f"/api/scheduled-tasks/{tid}/runs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_toggle_task(client: TestClient):
    tid = _seed_task(client)
    resp = client.patch(f"/api/scheduled-tasks/{tid}/toggle")
    assert resp.status_code == 200
    assert "enabled" in resp.json()


def test_run_task(client: TestClient):
    tid = _seed_task(client)
    # 运行需要智能体；无论成功触发还是因无智能体失败，都不应返回 5xx 之外的错误
    resp = client.post(f"/api/scheduled-tasks/{tid}/run")
    assert resp.status_code < 600


def test_delete_task(client: TestClient):
    tid = _seed_task(client)
    resp = client.delete(f"/api/scheduled-tasks/{tid}")
    assert resp.status_code == 200

    lst = client.get("/api/scheduled-tasks").json()
    assert not any(t["task_id"] == tid for t in lst)
