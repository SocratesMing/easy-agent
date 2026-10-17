"""部署形态校验测试（生产多 worker 强制 MySQL）。"""

from easy_agent.utils import deployment


def test_prod_multi_worker_sqlite_is_error():
    errors = deployment.deployment_errors(db_type="sqlite", agent_env="prod", workers=2)
    assert errors
    assert "MySQL" in errors[0]


def test_prod_multi_worker_mysql_is_ok():
    assert deployment.deployment_errors(db_type="mysql", agent_env="prod", workers=2) == []


def test_dev_multi_worker_sqlite_is_warning_only():
    assert deployment.deployment_errors(db_type="sqlite", agent_env="dev", workers=2) == []
    assert deployment.deployment_warnings(db_type="sqlite", agent_env="dev", workers=2)


def test_single_worker_is_ok():
    assert deployment.deployment_errors(db_type="sqlite", agent_env="prod", workers=1) == []


def test_detect_worker_count(monkeypatch):
    monkeypatch.delenv("EASY_REPLICA_COUNT", raising=False)
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    assert deployment.detect_worker_count() == 1
    monkeypatch.setenv("EASY_REPLICA_COUNT", "3")
    assert deployment.detect_worker_count() == 3
