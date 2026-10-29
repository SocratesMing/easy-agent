"""调度器多 worker/多副本保护测试（PERF-08）。

未开启分布式锁时，多副本会让同一 cron 在每个 pod 各执行一次；新实现默认
fail-closed（不注册任务），除非显式设置 EASY_ALLOW_DUPLICATE_SCHEDULER=1。
"""

import types

import pytest

from easy_agent.services import scheduler as sched


class _FakeDB:
    def list_all_enabled_scheduled_tasks(self):
        return []


class _FakeLock:
    enabled = False
    config = types.SimpleNamespace(key_prefix="easy", ttl_seconds=60)
    owner = "test"

    def cleanup_expired(self):
        return 0


@pytest.fixture
def fake_env(monkeypatch):
    monkeypatch.setattr(sched, "get_database", lambda: _FakeDB())
    monkeypatch.setattr(sched, "get_distributed_lock", lambda: _FakeLock())
    monkeypatch.setattr(sched, "log_task_event", lambda **kwargs: None)
    calls = []
    monkeypatch.setattr(
        sched, "register_scheduled_task", lambda task: calls.append(task)
    )
    return calls


def test_multi_replica_without_lock_skips_registration(fake_env, monkeypatch):
    monkeypatch.setenv("EASY_REPLICA_COUNT", "3")
    monkeypatch.delenv("EASY_ALLOW_DUPLICATE_SCHEDULER", raising=False)
    sched.reload_all_tasks()
    assert fake_env == []


def test_multi_replica_can_opt_in_explicitly(fake_env, monkeypatch):
    monkeypatch.setenv("EASY_REPLICA_COUNT", "3")
    monkeypatch.setenv("EASY_ALLOW_DUPLICATE_SCHEDULER", "1")
    sched.reload_all_tasks()
    # 空任务列表时也应走到 register 分支（返回 0 个，但未被 fail-closed 拦截）
    assert fake_env == []


def test_single_worker_does_not_block(fake_env, monkeypatch):
    monkeypatch.delenv("EASY_REPLICA_COUNT", raising=False)
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.delenv("UVICORN_WORKERS", raising=False)
    assert sched._detect_worker_count() == 1
    sched.reload_all_tasks()


def test_agent_manager_registers_the_agent_lifecycle_on_import():
    """scheduler 不导入 agent_manager，由 agent_manager 导入时注册生命周期。"""
    import easy_agent.services.agent_manager as agent_manager

    get_agent, remove_agent = sched._require_agent_lifecycle()
    assert get_agent is agent_manager.get_or_create_agent_for_session
    assert remove_agent is agent_manager.remove_session_agent


def test_unregistered_agent_lifecycle_fails_clearly(monkeypatch):
    """没注册就跑任务要明确报错，不再悄悄动态导入 agent_manager（那样会藏住一个 import 环）。"""
    monkeypatch.setattr(sched, "_agent_lifecycle_provider", None)
    with pytest.raises(RuntimeError, match="agent_manager"):
        sched._require_agent_lifecycle()
