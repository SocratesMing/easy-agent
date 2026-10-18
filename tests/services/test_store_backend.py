"""B1：LangGraph Store 后端选择（auto/postgres/sql）与生命周期。"""

import pytest

from easy_agent.persistence import stores

PG_URI = "postgresql://user:password@db:5432/agent"


def _resolve(**kwargs):
    kwargs.setdefault("backend", "auto")
    kwargs.setdefault("postgres_uri", "")
    kwargs.setdefault("official_available", True)
    return stores.resolve_store_backend(**kwargs)


class TestResolve:
    def test_auto_with_uri_uses_official_postgres(self):
        selection = _resolve(postgres_uri=PG_URI)
        assert selection.kind == stores.BACKEND_POSTGRES
        assert selection.is_official is True
        assert selection.postgres_uri == PG_URI
        assert selection.warning == ""

    def test_auto_without_uri_keeps_sql(self):
        selection = _resolve()
        assert selection.kind == stores.BACKEND_SQL
        assert selection.is_official is False
        assert selection.postgres_uri == ""

    def test_auto_without_official_package_falls_back_to_sql(self):
        selection = _resolve(postgres_uri=PG_URI, official_available=False)
        assert selection.kind == stores.BACKEND_SQL
        assert "langgraph.store.postgres" in selection.warning

    def test_forced_sql_ignores_uri(self):
        selection = _resolve(backend="sql", postgres_uri=PG_URI)
        assert selection.kind == stores.BACKEND_SQL
        assert selection.postgres_uri == ""

    def test_forced_postgres_without_uri_falls_back(self):
        selection = _resolve(backend="postgres")
        assert selection.kind == stores.BACKEND_SQL
        assert "EASY_STORE_POSTGRES_URI" in selection.warning

    def test_forced_postgres_without_package_falls_back(self):
        selection = _resolve(
            backend="postgres", postgres_uri=PG_URI, official_available=False
        )
        assert selection.kind == stores.BACKEND_SQL
        assert "不可用" in selection.warning

    def test_unknown_backend_treated_as_auto(self):
        selection = _resolve(backend="bogus", postgres_uri=PG_URI)
        assert selection.kind == stores.BACKEND_POSTGRES
        assert "bogus" in selection.warning


class TestEnvAndFallback:
    def test_reads_store_uri_from_environment(self, monkeypatch):
        monkeypatch.delenv("EASY_STORE_BACKEND", raising=False)
        monkeypatch.setenv("EASY_STORE_POSTGRES_URI", PG_URI)
        monkeypatch.delenv("EASY_CHECKPOINT_POSTGRES_URI", raising=False)
        selection = stores.resolve_store_backend(official_available=True)
        assert selection.kind == stores.BACKEND_POSTGRES
        assert selection.postgres_uri == PG_URI

    def test_falls_back_to_checkpoint_uri(self, monkeypatch):
        monkeypatch.delenv("EASY_STORE_BACKEND", raising=False)
        monkeypatch.delenv("EASY_STORE_POSTGRES_URI", raising=False)
        monkeypatch.setenv("EASY_CHECKPOINT_POSTGRES_URI", PG_URI)
        selection = stores.resolve_store_backend(official_available=True)
        assert selection.kind == stores.BACKEND_POSTGRES
        assert selection.postgres_uri == PG_URI

    def test_store_uri_wins_over_checkpoint_uri(self, monkeypatch):
        monkeypatch.setenv("EASY_STORE_POSTGRES_URI", PG_URI)
        monkeypatch.setenv(
            "EASY_CHECKPOINT_POSTGRES_URI",
            "postgresql://other@db:5432/other",
        )
        selection = stores.resolve_store_backend(official_available=True)
        assert selection.postgres_uri == PG_URI

    def test_invalid_env_backend_falls_back_to_auto(self, monkeypatch):
        monkeypatch.setenv("EASY_STORE_BACKEND", "bogus")
        monkeypatch.setenv("EASY_STORE_POSTGRES_URI", PG_URI)
        selection = stores.resolve_store_backend(official_available=True)
        assert selection.kind == stores.BACKEND_POSTGRES
        assert "bogus" in selection.warning


class TestLifecycle:
    async def test_start_with_injected_opener_activates_postgres(self):
        fake = object()
        seen = []

        async def _opener(uri):
            seen.append(uri)
            return fake

        selection = stores.StoreSelection(
            stores.BACKEND_POSTGRES, "fake postgres", postgres_uri=PG_URI
        )
        try:
            kind = await stores.start_store_backend(selection, opener=_opener)
            assert kind == stores.BACKEND_POSTGRES
            assert stores.active_store_backend() == stores.BACKEND_POSTGRES
            assert stores.get_agent_store() is fake
            assert seen == [PG_URI]
        finally:
            await stores.shutdown_store()
        assert stores.active_store_backend() == stores.BACKEND_SQL

    async def test_opener_failure_falls_back_to_sql(self):
        async def _boom(uri):
            raise RuntimeError("postgres down")

        selection = stores.StoreSelection(
            stores.BACKEND_POSTGRES, "fake postgres", postgres_uri=PG_URI
        )
        try:
            kind = await stores.start_store_backend(selection, opener=_boom)
            assert kind == stores.BACKEND_SQL
            assert stores.active_store_backend() == stores.BACKEND_SQL
        finally:
            await stores.shutdown_store()

    async def test_sql_selection_never_calls_opener(self):
        async def _must_not_open(uri):
            raise AssertionError("sql 后端不应打开 Postgres")

        selection = stores.StoreSelection(stores.BACKEND_SQL, "sql")
        try:
            assert (
                await stores.start_store_backend(selection, opener=_must_not_open)
                == stores.BACKEND_SQL
            )
        finally:
            await stores.shutdown_store()

    async def test_postgres_selection_without_uri_falls_back(self):
        async def _must_not_open(uri):
            raise AssertionError("缺少 URI 不应打开 Postgres")

        selection = stores.StoreSelection(stores.BACKEND_POSTGRES, "postgres")
        try:
            assert (
                await stores.start_store_backend(selection, opener=_must_not_open)
                == stores.BACKEND_SQL
            )
        finally:
            await stores.shutdown_store()

    async def test_get_agent_store_caches_sql_store(self):
        class _FakeDb:
            pass

        await stores.shutdown_store()
        try:
            created = stores.get_agent_store(db=_FakeDb())
            assert isinstance(created, stores.SqlStore)
            assert stores.active_store_backend() == stores.BACKEND_SQL
            assert stores.get_agent_store(db=object()) is created
        finally:
            await stores.shutdown_store()

    async def test_shutdown_is_idempotent(self):
        await stores.shutdown_store()
        await stores.shutdown_store()
        assert stores.active_store_backend() == stores.BACKEND_SQL


def test_official_postgres_store_is_importable():
    # 依赖已在 pyproject 中声明；若未来拆包，选择逻辑会安全回退 SqlStore。
    assert stores.official_postgres_store_available() is True
