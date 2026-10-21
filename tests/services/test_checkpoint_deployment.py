"""生产多副本的 checkpointer 校验、逃生门与 K8s 清单（每个 pod 一个 SQLite 文件 + 粘性会话）。

只放行一种多副本组合：官方 SQLite saver、路径含 ``{pod}``、``EASY_STICKY_SESSIONS=1``、
每个 pod 一个 worker（``docs/design/2026-10-21-per-pod-sqlite-checkpoints.md`` §4.4）。
"""

from pathlib import Path

import pytest
import yaml

from easy_agent.persistence import checkpoints as ck

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _per_pod(tmp_path=None, instance="easy-agent-0"):
    base = str(tmp_path) if tmp_path else "data/checkpoints"
    return ck.CheckpointSelection(
        ck.BACKEND_SQLITE,
        "per pod",
        sqlite_path=f"{base}/{instance}.db",
        per_pod=True,
        instance=instance,
        sqlite_template=f"{base}/{{pod}}.db",
    )


SHARED = ck.CheckpointSelection(ck.BACKEND_SQLITE, "shared", sqlite_path="data/checkpoints.db")
LEGACY = ck.CheckpointSelection(ck.BACKEND_LEGACY, "legacy")


def _validate(selection, **kwargs):
    kwargs.setdefault("agent_env", "prod")
    kwargs.setdefault("replicas", 3)
    kwargs.setdefault("workers_per_pod", 1)
    kwargs.setdefault("sticky", True)
    kwargs.setdefault("allow_legacy", False)
    return ck.validate_checkpoint_deployment(selection, **kwargs)


class TestDeploymentValidation:
    def test_per_pod_sqlite_with_sticky_sessions_is_accepted(self):
        assert _validate(_per_pod()) is True

    def test_per_pod_without_sticky_sessions_fails(self):
        with pytest.raises(ck.CheckpointDeploymentError, match="EASY_STICKY_SESSIONS=1"):
            _validate(_per_pod(), sticky=False)

    def test_shared_sqlite_file_fails(self):
        with pytest.raises(ck.CheckpointDeploymentError, match=r"\{pod\}"):
            _validate(SHARED)

    def test_legacy_snapshot_fails(self):
        with pytest.raises(ck.CheckpointDeploymentError) as err:
            _validate(LEGACY)
        assert "EASY_ALLOW_LEGACY_CHECKPOINTER=1" in str(err.value)
        assert "data/checkpoints/{pod}.db" in str(err.value)

    def test_several_workers_in_one_pod_fail(self):
        with pytest.raises(ck.CheckpointDeploymentError, match="WEB_CONCURRENCY"):
            _validate(_per_pod(), workers_per_pod=2)
        with pytest.raises(ck.CheckpointDeploymentError, match="WEB_CONCURRENCY"):
            _validate(SHARED, replicas=1, workers_per_pod=2)

    def test_single_pod_single_worker_needs_nothing(self):
        assert _validate(SHARED, replicas=1, sticky=False) is False
        assert _validate(LEGACY, replicas=1, sticky=False) is False

    def test_dev_multi_replica_is_not_checked(self):
        assert _validate(LEGACY, agent_env="dev", replicas=8, workers_per_pod=4) is False

    def test_escape_hatch_accepts_anything(self):
        assert _validate(LEGACY, allow_legacy=True) is False
        assert _validate(SHARED, workers_per_pod=4, allow_legacy=True) is False

    def test_reads_environment_when_arguments_are_omitted(self, monkeypatch):
        monkeypatch.setenv("AGENT_ENV", "prod")
        monkeypatch.setenv("EASY_REPLICA_COUNT", "2")
        for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "EASY_ALLOW_LEGACY_CHECKPOINTER"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv("EASY_STICKY_SESSIONS", raising=False)
        with pytest.raises(ck.CheckpointDeploymentError, match="EASY_STICKY_SESSIONS"):
            ck.validate_checkpoint_deployment(_per_pod())
        monkeypatch.setenv("EASY_STICKY_SESSIONS", "1")
        assert ck.validate_checkpoint_deployment(_per_pod()) is True
        monkeypatch.setenv("WEB_CONCURRENCY", "2")
        with pytest.raises(ck.CheckpointDeploymentError):
            ck.validate_checkpoint_deployment(_per_pod())

    def test_escape_hatch_reads_env(self, monkeypatch):
        monkeypatch.setenv("EASY_ALLOW_LEGACY_CHECKPOINTER", "1")
        assert ck.validate_checkpoint_deployment(
            LEGACY, agent_env="prod", replicas=2, workers_per_pod=1, sticky=False
        ) is False
        monkeypatch.setenv("EASY_ALLOW_LEGACY_CHECKPOINTER", "0")
        with pytest.raises(ck.CheckpointDeploymentError):
            ck.validate_checkpoint_deployment(
                LEGACY, agent_env="prod", replicas=2, workers_per_pod=1, sticky=False
            )


class TestStartBackendGuard:
    async def test_start_fails_before_opening_any_backend(self, monkeypatch, tmp_path):
        async def _must_not_open(selection):
            raise AssertionError("校验没过时不应尝试打开任何后端")

        monkeypatch.setattr(ck, "_open_with_fallback", _must_not_open)
        with pytest.raises(ck.CheckpointDeploymentError):
            await ck.start_checkpoint_backend(
                SHARED, agent_env="prod", replicas=2, workers_per_pod=1, sticky=True, allow_legacy=False
            )

    async def test_per_pod_cluster_opens_its_own_file(self, tmp_path):
        selection = _per_pod(tmp_path)
        try:
            kind = await ck.start_checkpoint_backend(
                selection, agent_env="prod", replicas=2, workers_per_pod=1, sticky=True, allow_legacy=False
            )
            assert kind == ck.BACKEND_SQLITE
            assert (tmp_path / "easy-agent-0.db").exists()
            assert ck.per_pod_checkpoints_active() is True
            assert ck.checkpoint_instance() == "easy-agent-0"
        finally:
            await ck.shutdown_checkpointers()
        assert ck.per_pod_checkpoints_active() is False

    async def test_per_pod_cluster_does_not_fall_back_when_sqlite_cannot_open(self, monkeypatch, tmp_path):
        async def _broken(path):
            raise OSError("disk full")

        monkeypatch.setattr(ck, "open_sqlite_checkpointer", _broken)
        with pytest.raises(ck.CheckpointDeploymentError, match="不回退业务库快照"):
            await ck.start_checkpoint_backend(
                _per_pod(tmp_path), agent_env="prod", replicas=2, workers_per_pod=1, sticky=True, allow_legacy=False
            )
        assert ck.active_checkpoint_backend() == ck.BACKEND_LEGACY

    async def test_single_pod_still_falls_back_when_sqlite_cannot_open(self, monkeypatch, tmp_path):
        async def _broken(path):
            raise OSError("disk full")

        monkeypatch.setattr(ck, "open_sqlite_checkpointer", _broken)
        try:
            kind = await ck.start_checkpoint_backend(
                _per_pod(tmp_path), agent_env="prod", replicas=1, workers_per_pod=1, sticky=False
            )
            assert kind == ck.BACKEND_LEGACY
        finally:
            await ck.shutdown_checkpointers()

    async def test_start_allows_legacy_with_escape_hatch(self):
        try:
            kind = await ck.start_checkpoint_backend(
                LEGACY, agent_env="prod", replicas=2, workers_per_pod=1, allow_legacy=True
            )
            assert kind == ck.BACKEND_LEGACY
        finally:
            await ck.shutdown_checkpointers()


def _load(name):
    return [doc for doc in yaml.safe_load_all((PROJECT_ROOT / "deploy" / "k8s" / name).read_text(encoding="utf-8")) if doc]


def test_k8s_manifest_runs_one_sqlite_file_per_pod(monkeypatch):
    docs = _load("agent-deployment.yaml")
    config = next(doc for doc in docs if doc.get("kind") == "ConfigMap")["data"]
    deployment = next(doc for doc in docs if doc.get("kind") == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]
    env = {item["name"]: item for item in container.get("env", [])}

    # 启动校验靠 EASY_REPLICA_COUNT 判断多副本：必须与 replicas 一致
    replicas = deployment["spec"]["replicas"]
    assert replicas > 1
    assert config["EASY_REPLICA_COUNT"] == str(replicas)
    assert env["EASY_POD_NAME"]["valueFrom"]["fieldRef"]["fieldPath"] == "metadata.name"
    assert "{pod}" in config["EASY_CHECKPOINT_SQLITE_PATH"]
    assert config["EASY_STICKY_SESSIONS"] == "1"
    assert config["EASY_ALLOW_LEGACY_CHECKPOINTER"] == "0"
    assert config["DISTRIBUTED_LOCK_ENABLED"] == "true"
    assert config["EASY_UPLOAD_BACKEND"] == "store"

    # 按清单的配置解析并校验，能通过
    monkeypatch.setenv("EASY_POD_NAME", "easy-agent-6d4b9c-x7k2p")
    selection = ck.resolve_checkpoint_backend(
        backend=config["EASY_CHECKPOINT_BACKEND"],
        sqlite_path=config["EASY_CHECKPOINT_SQLITE_PATH"],
        db_type="mysql",
    )
    assert selection.per_pod is True
    assert ck.validate_checkpoint_deployment(
        selection,
        agent_env=config["AGENT_ENV"],
        replicas=int(config["EASY_REPLICA_COUNT"]),
        workers_per_pod=1,
        sticky=config["EASY_STICKY_SESSIONS"] == "1",
        allow_legacy=False,
    ) is True

    # 每个 pod 的文件放在 pod 本地卷，不放共享 NAS（WAL 不能用在网络文件系统上）
    data_mount = next(m for m in container["volumeMounts"] if m["mountPath"] == "/app/data")
    data_volume = next(v for v in pod["volumes"] if v["name"] == data_mount["name"])
    assert "emptyDir" in data_volume

    assert not {name for name in {*env, *config} if "POSTGRES" in name}


def test_k8s_ingress_pins_each_browser_to_one_pod():
    ingress = _load("agent-ingress.yaml")[0]
    annotations = ingress["metadata"]["annotations"]
    assert annotations["nginx.ingress.kubernetes.io/affinity"] == "cookie"
    assert annotations["nginx.ingress.kubernetes.io/affinity-mode"] == "persistent"
    # SSE 回复不能被缓冲
    assert annotations["nginx.ingress.kubernetes.io/proxy-buffering"] == "off"
    backend = ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"]
    assert backend == {"name": "easy-agent", "port": {"number": 80}}
