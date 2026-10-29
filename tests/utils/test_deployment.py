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


def test_instance_name_order(monkeypatch):
    import socket

    monkeypatch.setenv("EASY_POD_NAME", "easy-agent-0")
    monkeypatch.setenv("POD_NAME", "other")
    monkeypatch.setenv("HOSTNAME", "host")
    assert deployment.instance_name() == "easy-agent-0"
    monkeypatch.delenv("EASY_POD_NAME")
    assert deployment.instance_name() == "other"
    monkeypatch.delenv("POD_NAME")
    assert deployment.instance_name() == "host"
    monkeypatch.delenv("HOSTNAME")
    assert deployment.instance_name() == socket.gethostname().strip()


def test_instance_names_that_can_go_into_a_path():
    for name in ("easy-agent-0", "easy-agent-6d4b9c-x7k2p", "DESKTOP-ABC", "a.b_c-1", "x" * 253):
        assert deployment.is_valid_instance_name(name), name
    for name in ("", ".hidden", "../x", "a/b", "a b", "x" * 254):
        assert not deployment.is_valid_instance_name(name), name


def test_replicas_and_workers_per_pod_are_counted_separately(monkeypatch):
    for name in ("EASY_REPLICA_COUNT", "WEB_CONCURRENCY", "UVICORN_WORKERS"):
        monkeypatch.delenv(name, raising=False)
    assert deployment.detect_replica_count() == 1
    assert deployment.detect_workers_per_pod() == 1
    monkeypatch.setenv("EASY_REPLICA_COUNT", "3")
    monkeypatch.setenv("UVICORN_WORKERS", "2")
    assert deployment.detect_replica_count() == 3
    assert deployment.detect_workers_per_pod() == 2
    # 合并口径保持不变（限流、调度器还在用）
    assert deployment.detect_worker_count() == 3
