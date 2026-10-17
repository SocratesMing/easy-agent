"""FW-09：checkpointer 后端选择（auto/postgres/sqlite/legacy/MySQL 回退）。

全部通过显式入参注入，不依赖真实 Postgres/MySQL；打开/关闭 saver 的用例只用
SQLite 临时文件。最后一个用例复用 ``client`` fixture，验证 app lifespan 真实
启用了官方 SQLite saver 且落在临时目录。
"""

from pathlib import Path

import pytest

from easy_agent import checkpoints as ck

DEFAULT = ck.DEFAULT_SQLITE_PATH
PG_URI = "postgresql://user:password@db:5432/agent"


def _resolve(**kwargs):
    """显式注入环境相关入参，避免宿主机 env 干扰（默认 backend=auto、无 URI）。"""
    kwargs.setdefault("backend", "auto")
    kwargs.setdefault("postgres_uri", "")
    kwargs.setdefault("sqlite_path", "")
    return ck.resolve_checkpoint_backend(**kwargs)


class TestAuto:
    def test_postgres_uri_wins(self):
        selection = _resolve(postgres_uri=PG_URI, sqlite_path="x.db", db_type="sqlite")
        assert selection.kind == ck.BACKEND_POSTGRES
        assert selection.postgres_uri == PG_URI
        assert selection.sqlite_path == ""
        assert selection.is_official is True

    def test_sqlite_business_db_uses_official_sqlite_by_default(self):
        selection = _resolve(db_type="sqlite")
        assert selection.kind == ck.BACKEND_SQLITE
        assert selection.sqlite_path == DEFAULT
        assert selection.postgres_uri == ""
        assert selection.is_official is True

    def test_sqlite_path_overrides_default(self):
        selection = _resolve(db_type="sqlite", sqlite_path="/tmp/ckpt.db")
        assert selection.kind == ck.BACKEND_SQLITE
        assert selection.sqlite_path == "/tmp/ckpt.db"

    def test_mysql_without_postgres_falls_back_to_legacy(self):
        selection = _resolve(db_type="mysql")
        assert selection.kind == ck.BACKEND_LEGACY
        assert selection.is_official is False
        assert "MySQL" in selection.warning
        assert "EASY_CHECKPOINT_POSTGRES_URI" in selection.warning

    def test_mysql_with_postgres_uri_uses_postgres(self):
        selection = _resolve(db_type="mysql", postgres_uri=PG_URI)
        assert selection.kind == ck.BACKEND_POSTGRES

    def test_unknown_db_without_paths_is_legacy_with_warning(self):
        selection = _resolve(db_type=None)
        assert selection.kind == ck.BACKEND_LEGACY
        assert selection.warning

    def test_explicit_sqlite_path_beats_mysql_fallback(self):
        # 兼容 FW-09 部分落地时的行为：显式配置 sqlite 路径即使业务库是 MySQL 也生效
        selection = _resolve(db_type="mysql", sqlite_path="/tmp/mysql-ckpt.db")
        assert selection.kind == ck.BACKEND_SQLITE
        assert selection.sqlite_path == "/tmp/mysql-ckpt.db"
        assert "多副本" in selection.warning


class TestForced:
    def test_legacy_ignores_postgres_uri(self):
        selection = _resolve(backend="legacy", postgres_uri=PG_URI, db_type="sqlite")
        assert selection.kind == ck.BACKEND_LEGACY
        assert selection.postgres_uri == ""
        assert selection.sqlite_path == ""

    def test_sqlite_forced_on_mysql_warns_local_only(self):
        selection = _resolve(backend="sqlite", db_type="mysql")
        assert selection.kind == ck.BACKEND_SQLITE
        assert selection.sqlite_path == DEFAULT
        assert "MySQL" in selection.warning

    def test_postgres_forced_without_uri_falls_back(self):
        selection = _resolve(backend="postgres", db_type="sqlite")
        assert selection.kind == ck.BACKEND_SQLITE
        assert "EASY_CHECKPOINT_POSTGRES_URI" in selection.warning

    def test_unknown_backend_treated_as_auto(self):
        selection = _resolve(backend="bogus", db_type="sqlite")
        assert selection.kind == ck.BACKEND_SQLITE
        assert "bogus" in selection.warning


class TestEnvAndDetection:
    def test_reads_environment_when_args_omitted(self, monkeypatch):
        monkeypatch.setenv("EASY_CHECKPOINT_BACKEND", "legacy")
        monkeypatch.setenv("EASY_CHECKPOINT_POSTGRES_URI", PG_URI)
        monkeypatch.setenv("EASY_CHECKPOINT_SQLITE_PATH", "/tmp/env.db")
        selection = ck.resolve_checkpoint_backend(db_type="sqlite")
        assert selection.kind == ck.BACKEND_LEGACY

    def test_auto_reads_env_postgres_uri(self, monkeypatch):
        monkeypatch.delenv("EASY_CHECKPOINT_BACKEND", raising=False)
        monkeypatch.setenv("EASY_CHECKPOINT_POSTGRES_URI", PG_URI)
        monkeypatch.delenv("EASY_CHECKPOINT_SQLITE_PATH", raising=False)
        selection = ck.resolve_checkpoint_backend(db_type="mysql")
        assert selection.kind == ck.BACKEND_POSTGRES
        assert selection.postgres_uri == PG_URI

    def test_auto_reads_env_sqlite_path(self, monkeypatch):
        monkeypatch.delenv("EASY_CHECKPOINT_BACKEND", raising=False)
        monkeypatch.delenv("EASY_CHECKPOINT_POSTGRES_URI", raising=False)
        monkeypatch.setenv("EASY_CHECKPOINT_SQLITE_PATH", "/tmp/env-ckpt.db")
        selection = ck.resolve_checkpoint_backend(db_type="mysql")
        assert selection.kind == ck.BACKEND_SQLITE
        assert selection.sqlite_path == "/tmp/env-ckpt.db"

    def test_detect_business_db_type_reads_loaded_instance(self, monkeypatch):
        import easy_agent.db.database as db_module

        class _FakeDb:
            db_type = "mysql"

        monkeypatch.setattr(db_module, "_db_instance", _FakeDb(), raising=False)
        assert ck.detect_business_db_type() == "mysql"
        selection = ck.resolve_checkpoint_backend(
            backend="auto", postgres_uri="", sqlite_path=""
        )
        assert selection.kind == ck.BACKEND_LEGACY

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
            postgres_uri="",
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
    async def test_postgres_open_failure_falls_back_to_sqlite(self, tmp_path, monkeypatch):
        async def _boom(conn_string):
            raise RuntimeError("postgres down")

        def _sqlite_fallback(**kwargs):
            return ck.CheckpointSelection(
                ck.BACKEND_SQLITE,
                "fallback sqlite",
                sqlite_path=str(tmp_path / "fallback.db"),
            )

        monkeypatch.setattr(ck, "open_postgres_checkpointer", _boom)
        monkeypatch.setattr(ck, "resolve_checkpoint_backend", _sqlite_fallback)
        selection = ck.CheckpointSelection(
            ck.BACKEND_POSTGRES, "postgres", postgres_uri=PG_URI
        )
        try:
            kind = await ck.start_checkpoint_backend(selection)
            assert kind == ck.BACKEND_SQLITE
            assert ck.sqlite_checkpointer_active() is True
            assert (tmp_path / "fallback.db").exists()
        finally:
            await ck.shutdown_checkpointers()

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
    project_root = Path(ck.__file__).resolve().parent.parent
    resolved = ck.resolve_sqlite_path(ck.DEFAULT_SQLITE_PATH)
    assert resolved.is_absolute()
    assert resolved == project_root / "data" / "checkpoints.db"
    assert ck.resolve_sqlite_path(str(project_root / "x.db")).is_absolute()
    assert ck.resolve_sqlite_path(":memory:") == Path(":memory:")


def test_app_lifespan_uses_official_sqlite_saver(client):
    """app lifespan 真正打开了官方 SQLite saver，且写在临时目录而非仓库 data/。"""
    import os

    configured_path = Path(os.environ["EASY_CHECKPOINT_SQLITE_PATH"])
    project_root = Path(ck.__file__).resolve().parent.parent

    assert ck.active_checkpoint_backend() == ck.BACKEND_SQLITE
    assert ck.sqlite_checkpointer_active() is True
    shared = ck.get_session_checkpointer("lifespan-session")
    assert ck.get_session_checkpointer("lifespan-other") is shared
    assert configured_path.is_absolute()
    assert project_root not in configured_path.parents
    assert configured_path.exists()
