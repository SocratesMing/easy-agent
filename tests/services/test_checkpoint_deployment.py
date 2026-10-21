"""B1：生产多副本 checkpointer fail-fast / 逃生门 / K8s 清单（单 pod + SQLite）。

Postgres checkpointer 已移除，两种后端都不能跨副本共享：生产多副本一律 fail-fast。
"""

from pathlib import Path

import pytest
import yaml

from easy_agent.persistence import checkpoints as ck

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TestDeploymentValidation:
    def test_prod_multi_replica_rejects_legacy(self):
        with pytest.raises(ck.CheckpointDeploymentError) as err:
            ck.validate_checkpoint_deployment(
                ck.BACKEND_LEGACY,
                agent_env="prod",
                workers=2,
                allow_legacy=False,
            )
        assert "EASY_ALLOW_LEGACY_CHECKPOINTER=1" in str(err.value)

    def test_prod_multi_replica_rejects_sqlite(self):
        with pytest.raises(ck.CheckpointDeploymentError) as err:
            ck.validate_checkpoint_deployment(
                ck.BACKEND_SQLITE,
                agent_env="prod",
                workers=4,
                allow_legacy=False,
            )
        assert "单 pod" in str(err.value)

    def test_prod_single_worker_allows_sqlite(self):
        ck.validate_checkpoint_deployment(
            ck.BACKEND_SQLITE,
            agent_env="prod",
            workers=1,
            allow_legacy=False,
        )

    def test_dev_multi_worker_allows_legacy(self):
        ck.validate_checkpoint_deployment(
            ck.BACKEND_LEGACY,
            agent_env="dev",
            workers=8,
            allow_legacy=False,
        )

    def test_escape_hatch_explicit_true(self):
        ck.validate_checkpoint_deployment(
            ck.BACKEND_LEGACY,
            agent_env="prod",
            workers=2,
            allow_legacy=True,
        )

    def test_escape_hatch_defaults_off(self, monkeypatch):
        monkeypatch.delenv("EASY_ALLOW_LEGACY_CHECKPOINTER", raising=False)
        with pytest.raises(ck.CheckpointDeploymentError):
            ck.validate_checkpoint_deployment(
                ck.BACKEND_LEGACY,
                agent_env="prod",
                workers=2,
            )

    def test_escape_hatch_reads_env(self, monkeypatch):
        monkeypatch.setenv("EASY_ALLOW_LEGACY_CHECKPOINTER", "1")
        ck.validate_checkpoint_deployment(
            ck.BACKEND_LEGACY,
            agent_env="prod",
            workers=2,
        )

    def test_escape_hatch_env_false_still_fails(self, monkeypatch):
        monkeypatch.setenv("EASY_ALLOW_LEGACY_CHECKPOINTER", "0")
        with pytest.raises(ck.CheckpointDeploymentError):
            ck.validate_checkpoint_deployment(
                ck.BACKEND_LEGACY,
                agent_env="prod",
                workers=2,
            )

    def test_agent_env_read_from_environment(self, monkeypatch):
        monkeypatch.setenv("AGENT_ENV", "prod")
        monkeypatch.setenv("EASY_REPLICA_COUNT", "2")
        monkeypatch.delenv("EASY_ALLOW_LEGACY_CHECKPOINTER", raising=False)
        with pytest.raises(ck.CheckpointDeploymentError):
            ck.validate_checkpoint_deployment(ck.BACKEND_LEGACY)


class TestStartBackendGuard:
    async def test_start_fails_before_opening_any_backend(self, monkeypatch, tmp_path):
        async def _must_not_open(selection):
            raise AssertionError("prod 多副本不应尝试打开任何后端")

        monkeypatch.setattr(ck, "_open_with_fallback", _must_not_open)
        selection = ck.CheckpointSelection(ck.BACKEND_SQLITE, "sqlite", sqlite_path=str(tmp_path / "x.db"))
        with pytest.raises(ck.CheckpointDeploymentError):
            await ck.start_checkpoint_backend(
                selection,
                agent_env="prod",
                workers=2,
                allow_legacy=False,
            )

    async def test_start_allows_legacy_with_escape_hatch(self):
        selection = ck.CheckpointSelection(ck.BACKEND_LEGACY, "forced legacy")
        try:
            kind = await ck.start_checkpoint_backend(
                selection,
                agent_env="prod",
                workers=2,
                allow_legacy=True,
            )
            assert kind == ck.BACKEND_LEGACY
        finally:
            await ck.shutdown_checkpointers()


def test_k8s_manifest_is_single_pod_with_local_sqlite_checkpoint():
    manifest = PROJECT_ROOT / "deploy" / "k8s" / "agent-deployment.yaml"
    docs = [doc for doc in yaml.safe_load_all(manifest.read_text(encoding="utf-8")) if doc]

    config = next(doc for doc in docs if doc.get("kind") == "ConfigMap")["data"]
    deployment = next(doc for doc in docs if doc.get("kind") == "Deployment")
    pod = deployment["spec"]["template"]["spec"]
    container = pod["containers"][0]

    # detect_worker_count() 读 EASY_REPLICA_COUNT：必须与 replicas 一致，且是单 pod，启动校验才会放行
    assert deployment["spec"]["replicas"] == 1
    assert config["EASY_REPLICA_COUNT"] == "1"
    assert config["EASY_ALLOW_LEGACY_CHECKPOINTER"] == "0"
    ck.validate_checkpoint_deployment(
        ck.BACKEND_SQLITE,
        agent_env=config["AGENT_ENV"],
        workers=int(config["EASY_REPLICA_COUNT"]),
        allow_legacy=False,
    )

    # checkpoint 是本地 SQLite 文件：data/ 卷不能是共享的 NAS（WAL 不能用在网络文件系统上）
    assert config["EASY_CHECKPOINT_SQLITE_PATH"] == "data/checkpoints.db"
    data_mount = next(m for m in container["volumeMounts"] if m["mountPath"] == "/app/data")
    data_volume = next(v for v in pod["volumes"] if v["name"] == data_mount["name"])
    assert "emptyDir" in data_volume

    env_names = {item["name"] for item in container.get("env", [])} | set(config)
    assert not {name for name in env_names if "POSTGRES" in name}
