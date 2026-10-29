"""LangGraph Store：业务库 SqlStore 的缓存与重置（官方 PostgresStore 已移除）。"""

from easy_agent.persistence import stores


class _FakeDb:
    pass


def test_get_agent_store_caches_sql_store():
    stores.reset_store()
    try:
        created = stores.get_agent_store(db=_FakeDb())
        assert isinstance(created, stores.SqlStore)
        assert stores.get_agent_store(db=object()) is created
    finally:
        stores.reset_store()


def test_reset_store_rebinds_the_business_db_on_next_use():
    stores.reset_store()
    try:
        first = stores.get_agent_store(db=_FakeDb())
        stores.reset_store()
        second = stores.get_agent_store(db=_FakeDb())
        assert second is not first
        assert isinstance(second, stores.SqlStore)
    finally:
        stores.reset_store()
