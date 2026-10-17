"""B1：生产多副本 checkpointer fail-fast / 逃生门 / K8s 注入。"""

from pathlib import Path

import pytest
import yaml

from easy_agent import checkpoints as ck

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PG_URI = "postgresql://user:password@db:5432/agent"


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
        with pytest.raises(ck.CheckpointDeploymentError):
            ck.validate_checkpoint_deployment(
                ck.BACKEND_SQLITE,
                agent_env="prod",
                workers=4,
                allow_legacy=False,
            )

    def test_prod_multi_replica_accepts_postgres(self):
        ck.validate_checkpoint_deployment(
            ck.BACKEND_POSTGRES,
            agent_env="prod",
            workers=3,
            allow_legacy=False,
        )

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
    async def test_start_fails_before_opening_non_postgres(self, monkeypatch):
        async def _must_not_open(selection):
            raise AssertionError("prod 多副本不应尝试打开非 Postgres 后端")

        monkeypatch.setattr(ck, "_open_with_fallback", _must_not_open)
        selection = ck.CheckpointSelection(ck.BACKEND_LEGACY, "forced legacy")
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

    async def test_postgres_failure_falls_back_then_fails_fast(
        self, tmp_path, monkeypatch
    ):
        async def _pg_down(conn_string):
            raise RuntimeError("postgres down")

        def _sqlite_fallback(**kwargs):
            return ck.CheckpointSelection(
                ck.BACKEND_SQLITE,
                "fallback sqlite",
                sqlite_path=str(tmp_path / "fallback.db"),
            )

        monkeypatch.setattr(ck, "open_postgres_checkpointer", _pg_down)
        monkeypatch.setattr(ck, "resolve_checkpoint_backend", _sqlite_fallback)
        selection = ck.CheckpointSelection(
            ck.BACKEND_POSTGRES,
            "postgres",
            postgres_uri=PG_URI,
        )
        with pytest.raises(ck.CheckpointDeploymentError):
            await ck.start_checkpoint_backend(
                selection,
                agent_env="prod",
                workers=2,
                allow_legacy=False,
            )
        assert ck.active_checkpoint_backend() == ck.BACKEND_LEGACY
        assert ck.sqlite_checkpointer_active() is False


def test_k8s_manifest_injects_checkpoint_postgres_secret():
    manifest = PROJECT_ROOT / "deploy" / "k8s" / "agent-deployment.yaml"
    text = manifest.read_text(encoding="utf-8")
    docs = [doc for doc in yaml.safe_load_all(text) if doc]

    configmap = next(doc for doc in docs if doc.get("kind") == "ConfigMap")
    assert configmap["data"]["AGENT_ENV"] == "prod"
    # detect_worker_count() 依赖显式副本数：manifest 必须声明，否则 fail-fast 不触发。
    assert configmap["data"]["EASY_REPLICA_COUNT"] == "2"
    assert configmap["data"]["EASY_ALLOW_LEGACY_CHECKPOINTER"] == "0"

    deployment = next(doc for doc in docs if doc.get("kind") == "Deployment")
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item for item in container.get("env", [])}
    secret_ref = env["EASY_CHECKPOINT_POSTGRES_URI"]["valueFrom"]["secretKeyRef"]
    assert secret_ref["name"] == "easy-agent-secrets"
    assert secret_ref["key"] == "checkpoint-postgres-uri"

    # 注释必须说明：应用库仍是 MySQL，只有 HITL/Store 在 Postgres。
    assert "MySQL" in text
    assert "Postgres" in text
