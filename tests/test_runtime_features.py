"""Phase 0 多副本严格门禁测试。"""

import pytest

from easy_agent import runtime_features as rf


def test_default_features_are_local_and_not_strict(monkeypatch):
    for key in (
        "EASY_STREAM_BACKEND",
        "EASY_RUN_QUEUE",
        "EASY_FILE_BACKEND",
        "EASY_SANDBOX",
        "EASY_MULTI_REPLICA_STRICT",
    ):
        monkeypatch.delenv(key, raising=False)
    f = rf.load_runtime_features()
    assert f.stream_backend == "db"
    assert f.run_queue == "none"
    assert f.file_backend == "local"
    assert f.sandbox == "local"
    assert f.strict is False
    assert rf.multi_replica_errors(agent_env="prod", workers=3, features=f) == []


def test_strict_prod_multi_replica_blocks_unimplemented(monkeypatch):
    monkeypatch.setenv("EASY_MULTI_REPLICA_STRICT", "1")
    monkeypatch.setenv("EASY_CHECKPOINT_BACKEND", "postgres")
    monkeypatch.setenv("EASY_CHECKPOINT_POSTGRES_URI", "postgresql://x")
    monkeypatch.setenv("EASY_STORE_BACKEND", "postgres")
    monkeypatch.setenv("EASY_CHECKPOINT_POSTGRES_URI", "postgresql://x")
    f = rf.load_runtime_features()
    errors = rf.multi_replica_errors(agent_env="prod", workers=3, features=f)
    assert errors
    assert not any("EASY_STREAM_BACKEND" in e for e in errors)
    assert any("EASY_RUN_QUEUE" in e for e in errors)
    assert any("EASY_FILE_BACKEND" in e for e in errors)
    assert any("EASY_SANDBOX" in e for e in errors)


def test_strict_single_replica_and_non_prod_are_allowed(monkeypatch):
    monkeypatch.setenv("EASY_MULTI_REPLICA_STRICT", "1")
    f = rf.load_runtime_features()
    assert rf.multi_replica_errors(agent_env="prod", workers=1, features=f) == []
    assert rf.multi_replica_errors(agent_env="dev", workers=3, features=f) == []


def test_startup_check_raises_in_strict_bad_case(monkeypatch):
    monkeypatch.setenv("EASY_MULTI_REPLICA_STRICT", "1")
    with pytest.raises(RuntimeError):
        rf.check_multi_replica_startup("prod", workers=3)
