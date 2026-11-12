"""``AsyncMySQLSaver`` 集成测试：真库、真 DDL、真事务。

跑法（CI 默认 skip，不设 DSN 时整个文件跳过）::

    set EASY_TEST_MYSQL_DSN=host=192.168.14.128;port=3306;user=root;password=xx
    pytest tests/persistence/test_mysql_saver_it.py -v

每次运行建一个独立库 ``cp_test_<uuid8>``，跑完 DROP；因此**不需要**在共享库里清表，
也不会碰到业务库（DSN 指向哪台机器都行，只要账号有 CREATE DATABASE 权限）。

这里只放"单测证明不了"的东西：

- ``asetup`` 真的能建出 4 张表，并且重复调用幂等（多 Pod 并发启动的前提）；
- ``aput`` / ``aget_tuple`` 的往返（内联标量 + blobs 组合还原成完整 ``channel_values``）；
- ``aput`` 的 upsert 冲突（同一 checkpoint_id 重复写不报错、不重复）；
- ``aput_writes`` 的两种冲突策略在真库上确有区别；
- ``adelete_thread`` 分批删干净（三张表都不留）；
- ``aget_delta_channel_history`` 的递归 CTE 在真库上能回溯（``cte_max_recursion_depth`` 够用）。
"""

from __future__ import annotations

import os
import uuid

import pytest

pytestmark = pytest.mark.mysql

DSN = os.environ.get("EASY_TEST_MYSQL_DSN", "").strip()
pytestmark = [pytestmark, pytest.mark.skipif(not DSN, reason="未设置 EASY_TEST_MYSQL_DSN（真库集成测试）")]


def _dsn_params() -> dict:
    """解析 ``k=v;k=v`` 形式的 DSN（分号或逗号分隔），避免把密码写进 URL 编码地狱。"""
    params = {}
    for chunk in DSN.replace(",", ";").split(";"):
        if not chunk.strip():
            continue
        key, _, value = chunk.partition("=")
        params[key.strip()] = value.strip()
    return params


@pytest.fixture(scope="module")
def mysql_dsn():
    base = _dsn_params()
    return {
        "host": base.get("host", "127.0.0.1"),
        "port": int(base.get("port", "3306")),
        "user": base.get("user", "root"),
        "password": base.get("password", ""),
    }


@pytest.fixture
async def database_name(mysql_dsn):
    """每个用例建一个独立库，跑完就 DROP。

    **不能按 module 共享**：这些用例都用同一个 thread_id（``s1``）验证语义，
    共享库会让上一个用例的 checkpoint 变成"最新的一条"，断言全线错位。
    """
    aiomysql = pytest.importorskip("aiomysql")
    name = f"cp_test_{uuid.uuid4().hex[:8]}"
    conn = await aiomysql.connect(**mysql_dsn, autocommit=True)
    try:
        async with conn.cursor() as cur:
            await cur.execute(
                f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"
            )
    finally:
        conn.close()
    yield name
    conn = await aiomysql.connect(**mysql_dsn, autocommit=True)
    try:
        async with conn.cursor() as cur:
            await cur.execute(f"DROP DATABASE IF EXISTS `{name}`")
    finally:
        conn.close()


@pytest.fixture
async def saver(mysql_dsn, database_name):
    from easy_agent.persistence.mysql_saver import AsyncMySQLSaver, MySQLCheckpointConfig

    config = MySQLCheckpointConfig(
        host=mysql_dsn["host"],
        port=mysql_dsn["port"],
        user=mysql_dsn["user"],
        password=mysql_dsn["password"],
        database=database_name,
        table_prefix="cp_",
        pool_min_size=1,
        pool_max_size=4,
        auto_setup=True,
        auto_setup_lock_wait=30,
    )
    instance = AsyncMySQLSaver(config)
    await instance.asetup()
    try:
        yield instance
    finally:
        await instance.aclose()


def _config(thread_id="s1", ns="", checkpoint_id=None):
    configurable = {"thread_id": thread_id, "checkpoint_ns": ns}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def _checkpoint(checkpoint_id, *, parent=None, values=None, versions=None):
    from langgraph.checkpoint.base import empty_checkpoint

    checkpoint = empty_checkpoint()
    checkpoint["id"] = checkpoint_id
    checkpoint["channel_values"] = dict(values or {})
    checkpoint["channel_versions"] = dict(versions or {})
    return checkpoint


async def _table_names(saver) -> set[str]:
    async with saver._pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                "SELECT table_name AS name FROM information_schema.tables "
                "WHERE table_schema = DATABASE()"
            )
            rows = await cur.fetchall()
    return {row["name"] for row in rows}


class TestSetup:
    async def test_creates_the_four_protocol_tables(self, saver):
        assert await _table_names(saver) == {
            "cp_checkpoints",
            "cp_checkpoint_blobs",
            "cp_checkpoint_writes",
            "cp_checkpoint_meta",
        }

    async def test_setup_is_idempotent(self, saver):
        """多 Pod 并发启动时每个 Pod 都会跑一次 asetup，必须幂等。"""
        await saver.asetup()
        await saver.asetup()
        assert await _table_names(saver) == {
            "cp_checkpoints",
            "cp_checkpoint_blobs",
            "cp_checkpoint_writes",
            "cp_checkpoint_meta",
        }

    async def test_meta_records_schema_version_and_ns_algo(self, saver):
        from easy_agent.persistence.mysql_saver import NS_HASH_ALGO, SCHEMA_VERSION

        assert (await saver.read_meta("schema_version")) == str(SCHEMA_VERSION)
        assert (await saver.read_meta("ns_hash_algo")) == NS_HASH_ALGO


class TestRoundTrip:
    async def test_inline_scalars_and_blobs_reassemble(self, saver):
        await saver.aput(
            _config(),
            _checkpoint(
                "cp-1",
                values={"text": "hi", "big": {"nested": [1, 2, 3]}, "n": 7},
                versions={"text": "1", "big": "1", "n": "1"},
            ),
            {"source": "input", "step": 0},
            {"big": "1"},
        )
        loaded = await saver.aget_tuple(_config())
        assert loaded is not None
        assert loaded.checkpoint["channel_values"]["text"] == "hi"
        assert loaded.checkpoint["channel_values"]["big"] == {"nested": [1, 2, 3]}
        assert loaded.checkpoint["channel_values"]["n"] == 7
        assert loaded.metadata["source"] == "input"

    async def test_parent_chain_is_readable(self, saver):
        await saver.aput(_config(), _checkpoint("cp-1", values={"m": "a"}), {}, {"m": "1"})
        await saver.aput(
            _config(checkpoint_id="cp-1"),
            _checkpoint("cp-2", parent="cp-1", values={"m": "b"}),
            {},
            {"m": "2"},
        )
        latest = await saver.aget_tuple(_config())
        assert latest is not None
        assert latest.config["configurable"]["checkpoint_id"] == "cp-2"
        assert latest.parent_config["configurable"]["checkpoint_id"] == "cp-1"

    async def test_repeated_aput_of_the_same_id_does_not_duplicate(self, saver):
        checkpoint = _checkpoint("cp-1", values={"m": "a"}, versions={"m": "1"})
        await saver.aput(_config(), checkpoint, {"step": 1}, {"m": "1"})
        await saver.aput(_config(), checkpoint, {"step": 1}, {"m": "1"})
        async with saver._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) AS n FROM cp_checkpoints WHERE thread_id=%s", ("s1",)
                )
                row = await cur.fetchone()
        assert row["n"] == 1

    async def test_alist_returns_the_chain_newest_first(self, saver):
        for index in range(3):
            await saver.aput(
                _config(),
                _checkpoint(f"cp-{index}", values={"m": str(index)}),
                {},
                {"m": str(index)},
            )
        ids = [
            item.config["configurable"]["checkpoint_id"] async for item in saver.alist(_config())
        ]
        # 协议规定 alist 由新到旧
        assert ids == ["cp-2", "cp-1", "cp-0"]

    async def test_chinese_values_survive_the_utf8mb4_round_trip(self, saver):
        """业务库与保表都是 utf8mb4：中文不能变成问号或乱码。"""
        await saver.aput(
            _config(),
            _checkpoint("cp-1", values={"m": "你好，世界"}, versions={"m": "1"}),
            {},
            {"m": "1"},
        )
        loaded = await saver.aget_tuple(_config())
        assert loaded.checkpoint["channel_values"]["m"] == "你好，世界"


class TestWrites:
    async def test_interrupt_write_is_visible_and_overwritable(self, saver):
        from langgraph.checkpoint.base import INTERRUPT

        config = _config(checkpoint_id="cp-1")
        await saver.aput(config, _checkpoint("cp-1"), {}, {})
        await saver.aput_writes(config, [(INTERRUPT, {"q": "第一次"})], "task-1", "")
        await saver.aput_writes(config, [(INTERRUPT, {"q": "第二次"})], "task-1", "")
        loaded = await saver.aget_tuple(_config(checkpoint_id="cp-1"))
        assert [w[2] for w in loaded.pending_writes] == [{"q": "第二次"}]

    async def test_normal_write_is_not_overwritten(self, saver):
        config = _config(checkpoint_id="cp-1")
        await saver.aput(config, _checkpoint("cp-1"), {}, {})
        await saver.aput_writes(config, [("messages", ["first"])], "task-1", "")
        await saver.aput_writes(config, [("messages", ["second"])], "task-1", "")
        loaded = await saver.aget_tuple(_config(checkpoint_id="cp-1"))
        assert [w[2] for w in loaded.pending_writes] == [["first"]]


class TestDelete:
    async def test_delete_thread_removes_every_table(self, saver):
        config = _config()
        await saver.aput(config, _checkpoint("cp-1", values={"m": "a"}), {}, {"m": "1"})
        await saver.aput_writes(
            _config(checkpoint_id="cp-1"), [("messages", ["a"])], "task-1", ""
        )
        await saver.adelete_thread("s1")
        assert await saver.aget_tuple(config) is None
        for table in ("cp_checkpoints", "cp_checkpoint_blobs", "cp_checkpoint_writes"):
            async with saver._pool.acquire() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
                    assert (await cur.fetchone())["n"] == 0, table


class TestGCQueries:
    async def test_orphan_threads_only_returns_threads_of_missing_sessions(self, saver):
        """``sessions`` 表不存在时 LEFT JOIN 仍能用（本库没有业务表，全部算孤儿）。"""
        await saver.aput(_config(thread_id="s1"), _checkpoint("cp-1"), {}, {})
        async with saver._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "CREATE TABLE IF NOT EXISTS sessions (session_id VARCHAR(64) PRIMARY KEY)"
                )
        async with saver._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("INSERT INTO sessions (session_id) VALUES ('s1')")
        assert await saver.list_orphan_threads() == []

    async def test_delta_history_walks_the_ancestor_chain(self, saver):
        """递归 CTE 要够深：``cte_max_recursion_depth`` 已在连接上设置。"""
        await saver.aput(
            _config(),
            _checkpoint("cp-1", values={"messages": [1, 2]}, versions={"messages": "1"}),
            {},
            {"messages": "1"},
        )
        await saver.aput(
            _config(checkpoint_id="cp-1"),
            _checkpoint("cp-2", values={"messages": [3]}, versions={"messages": "2"}),
            {},
            {"messages": "2"},
        )
        history = await saver.aget_delta_channel_history(
            config=_config(checkpoint_id="cp-2"), channels=["messages"]
        )
        assert "messages" in history
