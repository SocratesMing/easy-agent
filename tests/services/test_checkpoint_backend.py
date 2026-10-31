"""FW-09：checkpointer 后端选择（auto/sqlite/legacy）与 SQLite 路径的来源。

路径取 config.yaml 的 ``checkpoint.sqlite_path``（入参 ``sqlite_path``），没配时用旧环境变量
``EASY_CHECKPOINT_SQLITE_PATH``，再没有是 ``/qts/checkpoint/{pod}.db``。全部通过显式入参注入，
不依赖真实 MySQL；打开/关闭 saver 的用例只用 SQLite 临时文件。最后一个用例复用 ``client``
fixture，验证 app lifespan 真实启用了官方 SQLite saver 且落在临时目录。
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from easy_agent.persistence import checkpoints as ck

POD = "easy-agent-0"
DEFAULT = f"/qts/checkpoint/{POD}.db"


@pytest.fixture(autouse=True)
def _no_legacy_path_env(monkeypatch):
    """conftest 用旧环境变量给每个用例指定 checkpoint 文件；这里清掉，只看入参。"""
    monkeypatch.delenv("EASY_CHECKPOINT_SQLITE_PATH", raising=False)
    monkeypatch.setenv("EASY_POD_NAME", POD)


def _resolve(**kwargs):
    """显式注入环境相关入参，避免宿主机 env 干扰（默认 backend=auto、config.yaml 没配路径）。"""
    kwargs.setdefault("backend", "auto")
    kwargs.setdefault("sqlite_path", "")
    return ck.resolve_checkpoint_backend(**kwargs)


class TestAuto:
    def test_default_is_one_file_per_pod_under_qts_checkpoint(self):
        assert ck.DEFAULT_SQLITE_PATH == "/qts/checkpoint/{pod}.db"
        selection = _resolve(db_type="sqlite")
        assert selection.kind == ck.BACKEND_SQLITE
        assert selection.is_official is True
        assert selection.sqlite_path == DEFAULT
        assert selection.per_pod is True
        assert selection.instance == POD
        assert selection.sqlite_template == ck.DEFAULT_SQLITE_PATH
        assert "默认路径" in selection.reason

    def test_mysql_without_a_configured_path_uses_the_default_file_too(self):
        # 以前业务库是 MySQL、没配路径时回退进程内快照；现在总有默认路径
        selection = _resolve(db_type="mysql")
        assert selection.kind == ck.BACKEND_SQLITE
        assert selection.sqlite_path == DEFAULT
        assert selection.warning == ""

    def test_unknown_db_uses_the_default_file(self):
        selection = _resolve(db_type=None)
        assert selection.kind == ck.BACKEND_SQLITE
        assert selection.sqlite_path == DEFAULT

    def test_yaml_path_overrides_default(self):
        selection = _resolve(db_type="sqlite", sqlite_path="/tmp/ckpt.db")
        assert selection.kind == ck.BACKEND_SQLITE
        assert selection.sqlite_path == "/tmp/ckpt.db"
        assert selection.per_pod is False
        assert "checkpoint.sqlite_path" in selection.reason

    def test_shared_path_on_mysql_warns_local_only(self):
        selection = _resolve(db_type="mysql", sqlite_path="/tmp/mysql-ckpt.db")
        assert selection.kind == ck.BACKEND_SQLITE
        assert selection.sqlite_path == "/tmp/mysql-ckpt.db"
        assert "多副本" in selection.warning


class TestForced:
    def test_legacy_ignores_sqlite_path(self):
        selection = _resolve(backend="legacy", sqlite_path="x.db", db_type="sqlite")
        assert selection.kind == ck.BACKEND_LEGACY
        assert selection.sqlite_path == ""

    def test_legacy_does_not_need_a_valid_instance_name(self, monkeypatch):
        # legacy 用不到 SQLite 文件，默认路径里的 {pod} 不替换，也就不校验实例名
        monkeypatch.setenv("EASY_POD_NAME", "../escape")
        assert _resolve(backend="legacy").kind == ck.BACKEND_LEGACY

    def test_sqlite_forced_without_a_path_uses_the_default_file(self):
        selection = _resolve(backend="sqlite", db_type="mysql")
        assert selection.kind == ck.BACKEND_SQLITE
        assert selection.sqlite_path == DEFAULT
        assert selection.per_pod is True

    def test_sqlite_forced_on_mysql_with_a_shared_path_warns_local_only(self):
        selection = _resolve(backend="sqlite", sqlite_path="data/checkpoints.db", db_type="mysql")
        assert selection.kind == ck.BACKEND_SQLITE
        assert "MySQL" in selection.warning

    def test_removed_postgres_backend_is_treated_as_auto(self):
        # Postgres checkpointer 已移除：旧配置里的 postgres 按未知值处理并告警
        selection = _resolve(backend="postgres", db_type="sqlite")
        assert selection.kind == ck.BACKEND_SQLITE
        assert "'postgres'" in selection.warning
        assert "auto/sqlite/legacy" in selection.warning

    def test_unknown_backend_treated_as_auto(self):
        selection = _resolve(backend="bogus", db_type="sqlite")
        assert selection.kind == ck.BACKEND_SQLITE
        assert "bogus" in selection.warning


class TestPerPodPath:
    def test_pod_placeholder_becomes_the_instance_name(self, monkeypatch):
        monkeypatch.setenv("EASY_POD_NAME", "easy-agent-0")
        selection = _resolve(sqlite_path="data/checkpoints/{pod}.db", db_type="mysql")
        assert selection.kind == ck.BACKEND_SQLITE
        assert selection.sqlite_path == "data/checkpoints/easy-agent-0.db"
        assert selection.per_pod is True
        assert selection.instance == "easy-agent-0"
        assert selection.sqlite_template == "data/checkpoints/{pod}.db"
        # 按 pod 隔离时多副本由启动校验把关，不再提示「仅适合单机」
        assert "多副本" not in selection.warning

    def test_forced_sqlite_backend_replaces_the_placeholder_too(self, monkeypatch):
        monkeypatch.setenv("EASY_POD_NAME", "pod-1")
        selection = _resolve(backend="sqlite", sqlite_path="/data/{pod}/ckpt.db", db_type="sqlite")
        assert selection.sqlite_path == "/data/pod-1/ckpt.db"
        assert selection.per_pod is True

    def test_path_without_placeholder_is_shared(self):
        selection = _resolve(sqlite_path="data/checkpoints.db", db_type="mysql")
        assert selection.per_pod is False
        assert selection.instance == ""

    def test_unsafe_instance_name_fails_fast(self, monkeypatch):
        monkeypatch.setenv("EASY_POD_NAME", "../escape")
        with pytest.raises(ck.CheckpointDeploymentError, match="EASY_POD_NAME"):
            _resolve(sqlite_path="data/checkpoints/{pod}.db", db_type="mysql")


class TestEnvAndDetection:
    def test_reads_environment_when_args_omitted(self, monkeypatch):
        monkeypatch.setenv("EASY_CHECKPOINT_BACKEND", "legacy")
        monkeypatch.setenv("EASY_CHECKPOINT_SQLITE_PATH", "/tmp/env.db")
        selection = ck.resolve_checkpoint_backend(db_type="sqlite")
        assert selection.kind == ck.BACKEND_LEGACY

    def test_stale_postgres_uri_env_is_ignored(self, monkeypatch):
        monkeypatch.delenv("EASY_CHECKPOINT_BACKEND", raising=False)
        monkeypatch.setenv("EASY_CHECKPOINT_POSTGRES_URI", "postgresql://user:password@db:5432/agent")
        selection = ck.resolve_checkpoint_backend(db_type="mysql")
        assert selection.kind == ck.BACKEND_SQLITE
        assert selection.sqlite_path == DEFAULT
        assert "postgres" not in selection.warning.lower()

    def test_legacy_env_path_is_used_when_yaml_has_none(self, monkeypatch):
        monkeypatch.delenv("EASY_CHECKPOINT_BACKEND", raising=False)
        monkeypatch.setenv("EASY_CHECKPOINT_SQLITE_PATH", "/tmp/env-ckpt.db")
        selection = ck.resolve_checkpoint_backend(db_type="mysql")
        assert selection.kind == ck.BACKEND_SQLITE
        assert selection.sqlite_path == "/tmp/env-ckpt.db"
        assert "EASY_CHECKPOINT_SQLITE_PATH" in selection.reason

    def test_yaml_path_beats_the_legacy_env_and_says_so(self, monkeypatch):
        monkeypatch.setenv("EASY_CHECKPOINT_SQLITE_PATH", "/tmp/env-ckpt.db")
        selection = _resolve(sqlite_path="/tmp/yaml-ckpt.db", db_type="sqlite")
        assert selection.sqlite_path == "/tmp/yaml-ckpt.db"
        assert "EASY_CHECKPOINT_SQLITE_PATH" in selection.warning

    def test_detect_business_db_type_reads_loaded_instance(self, monkeypatch):
        import easy_agent.db.database as db_module

        class _FakeDb:
            db_type = "mysql"

        monkeypatch.setattr(db_module, "_db_instance", _FakeDb(), raising=False)
        assert ck.detect_business_db_type() == "mysql"
        # 只有不按 pod 分文件时才用得上业务库类型：MySQL 上共用一个文件要提示
        selection = ck.resolve_checkpoint_backend(backend="auto", sqlite_path="data/x.db")
        assert "MySQL" in selection.warning

    def test_detect_business_db_type_without_instance(self, monkeypatch):
        import easy_agent.db.database as db_module

        monkeypatch.setattr(db_module, "_db_instance", None, raising=False)
        assert ck.detect_business_db_type() is None


class TestStartBackend:
    @pytest.mark.asyncio
    async def test_legacy_selection_opens_nothing(self):
        selection = ck.CheckpointSelection(ck.BACKEND_LEGACY, "forced")
        try:
            assert await ck.start_checkpoint_backend(selection) == ck.BACKEND_LEGACY
            assert ck.active_checkpoint_backend() == ck.BACKEND_LEGACY
        finally:
            await ck.shutdown_checkpointers()

    @pytest.mark.asyncio
    async def test_sqlite_selection_opens_official_saver(self, tmp_path):
        selection = ck.resolve_checkpoint_backend(
            backend="sqlite",
            sqlite_path=str(tmp_path / "ckpt.db"),
            db_type="sqlite",
        )
        try:
            kind = await ck.start_checkpoint_backend(selection)
            assert kind == ck.BACKEND_SQLITE
            assert ck.sqlite_checkpointer_active() is True
            assert ck.active_checkpoint_backend() == ck.BACKEND_SQLITE
            assert (tmp_path / "ckpt.db").exists()
            assert ck.get_session_checkpointer("s-1") is ck.get_session_checkpointer("s-2")
        finally:
            await ck.shutdown_checkpointers()
        assert ck.sqlite_checkpointer_active() is False
        assert ck.active_checkpoint_backend() == ck.BACKEND_LEGACY

    @pytest.mark.asyncio
    async def test_sqlite_open_failure_falls_back_to_legacy(self, tmp_path, monkeypatch):
        async def _boom(path):
            raise RuntimeError("disk full")

        monkeypatch.setattr(ck, "open_sqlite_checkpointer", _boom)
        selection = ck.CheckpointSelection(
            ck.BACKEND_SQLITE, "sqlite", sqlite_path=str(tmp_path / "x.db")
        )
        try:
            assert await ck.start_checkpoint_backend(selection) == ck.BACKEND_LEGACY
            assert ck.active_checkpoint_backend() == ck.BACKEND_LEGACY
        finally:
            await ck.shutdown_checkpointers()

    @pytest.mark.asyncio
    async def test_shutdown_is_idempotent(self):
        await ck.shutdown_checkpointers()
        await ck.shutdown_checkpointers()
        assert ck.active_checkpoint_backend() == ck.BACKEND_LEGACY


def test_relative_sqlite_path_resolves_under_project_root():
    # 仓库根以本测试文件的位置为准，不能照抄被测模块的算法：checkpoints.py 挪进
    # persistence/ 后少算了一级，默认路径落到 easy_agent/data/，旧断言照着同样的
    # parent.parent 算，没能发现
    project_root = Path(__file__).resolve().parents[2]
    assert (project_root / "pyproject.toml").is_file()
    resolved = ck.resolve_sqlite_path("data/checkpoints.db")
    assert resolved.is_absolute()
    assert resolved == project_root / "data" / "checkpoints.db"
    assert ck.resolve_sqlite_path(str(project_root / "x.db")).is_absolute()
    assert ck.resolve_sqlite_path(DEFAULT) == Path(DEFAULT)
    assert ck.resolve_sqlite_path(":memory:") == Path(":memory:")


@pytest.mark.asyncio
async def test_app_startup_passes_the_yaml_checkpoint_path(monkeypatch):
    """app 启动把 config.yaml 的 checkpoint.sqlite_path 交给 checkpointer。"""
    import easy_agent.app as app_module

    seen = {}

    async def fake_start(**kwargs):
        seen.update(kwargs)
        return ck.BACKEND_SQLITE

    monkeypatch.setattr(ck, "start_checkpoint_backend", fake_start)
    monkeypatch.setattr(ck, "start_checkpoint_gc", lambda: None)
    config = SimpleNamespace(checkpoint=SimpleNamespace(sqlite_path="/qts/checkpoint/{pod}.db"))

    await app_module._start_checkpointing("prod", config)

    assert seen == {"sqlite_path": "/qts/checkpoint/{pod}.db", "agent_env": "prod"}


def test_app_lifespan_uses_official_sqlite_saver(client):
    """app lifespan 真正打开了官方 SQLite saver，且写在临时目录而非仓库 data/。"""
    import os

    configured_path = Path(os.environ["EASY_CHECKPOINT_SQLITE_PATH"])
    project_root = Path(__file__).resolve().parents[2]

    assert ck.active_checkpoint_backend() == ck.BACKEND_SQLITE
    assert ck.sqlite_checkpointer_active() is True
    shared = ck.get_session_checkpointer("lifespan-session")
    assert ck.get_session_checkpointer("lifespan-other") is shared
    assert configured_path.is_absolute()
    assert project_root not in configured_path.parents
    assert configured_path.exists()
