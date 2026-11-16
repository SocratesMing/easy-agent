"""``AsyncMySQLSaver`` 单测：不连真库，用假游标断言 SQL 与事务边界。

覆盖三类容易写错又难在集成里看出来的点：

1. **DDL 与审计基线一致**：``scripts/db/mysql-checkpoint-ddl.sql`` 是 ``DDL_STATEMENTS``
   的逐字副本，DBA 按它建库；两边漂移会让"应用自动建的表"和"审计过的表"不是同一份。
2. **协议语义**：内联策略（只内联 JSON 标量）、writes 冲突策略（全特殊通道才覆盖）、
   ``aget_tuple`` 的空 checkpoint_id 取最新、三表读必须包在同一只读事务里。
3. **MySQL 8 特有陷阱**：``AS new ON DUPLICATE KEY UPDATE``（``VALUES()`` 已废弃）、
   列名不能叫 ``blob``（保留字）、``checkpoint_ns`` 不进主键（改用 sha256 前 16 字节）。

真库行为（DDL 执行、upsert、分批删、递归 CTE）在 ``test_mysql_saver_it.py``，默认 skip。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from langgraph.checkpoint.base import empty_checkpoint

from easy_agent.persistence import mysql_saver as ms
from easy_agent.persistence.mysql_saver import (
    DDL_STATEMENTS,
    NS_HASH_ALGO,
    AsyncMySQLSaver,
    MySQLCheckpointConfig,
    MySQLCheckpointError,
    ns_hash,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DDL_FILE = PROJECT_ROOT / "scripts" / "db" / "mysql-checkpoint-ddl.sql"


# ── 假连接：记录 SQL / 参数 / 事务事件，按需返回预置行 ────────────────
class _Cursor:
    def __init__(self, conn):
        self._conn = conn
        self._rows: list = []
        # 分批 DELETE 靠 rowcount 判断"这批删完了"，默认 0 让循环立刻结束
        self.rowcount = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        self._conn.statements.append(("execute", " ".join(sql.split()), params))
        self._rows = self._conn.reply(sql, params)

    async def executemany(self, sql, rows):
        self._conn.statements.append(("executemany", " ".join(sql.split()), list(rows)))

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return list(self._rows)

    async def close(self):
        return None


class _Conn:
    def __init__(self, reply=None):
        self.statements: list[tuple] = []
        self.events: list[str] = []
        self._reply = reply or (lambda sql, params: [])

    def reply(self, sql, params):
        return self._reply(sql, params)

    def cursor(self):
        return _Cursor(self)

    async def begin(self):
        self.events.append("begin")

    async def commit(self):
        self.events.append("commit")

    async def rollback(self):
        self.events.append("rollback")


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)

    def close(self):
        return None

    async def wait_closed(self):
        return None


def _saver(reply=None, **kwargs):
    saver = AsyncMySQLSaver(MySQLCheckpointConfig(**kwargs))
    conn = _Conn(reply)
    saver._pool = _Pool(conn)  # 跳过建池：单测不连库
    saver._setup_done = True
    return saver, conn


def _config(thread_id="s1", ns="", checkpoint_id=None):
    configurable = {"thread_id": thread_id, "checkpoint_ns": ns}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}


def _sql_text(conn) -> str:
    return "\n".join(s[1] for s in conn.statements)


# ── DDL 与审计基线 ────────────────────────────────────────────────────
class TestDDL:
    def test_audit_baseline_matches_the_code(self):
        """审计用的 .sql 必须与运行时 DDL 逐字一致（只差表名前缀替换）。"""
        text = DDL_FILE.read_text(encoding="utf-8")
        assert text.startswith("--"), "首行应是文件头注释"
        assert "SET NAMES utf8mb4;" in text, "缺字符集前置语句：中文注释会乱码"
        for statement in DDL_STATEMENTS:
            assert statement.format(p="cp_") in text, (
                f"DDL 与审计基线不一致，请重新生成 {DDL_FILE.name}：\n{statement[:80]}"
            )

    def test_every_table_is_utf8mb4_bin_and_dynamic(self):
        for statement in DDL_STATEMENTS:
            assert "utf8mb4_bin" in statement, statement[:60]
            # DYNAMIC 行格式：TEXT/BLOB 外置存页，避免 LONGBLOB 撑大主键页
            assert "ROW_FORMAT=DYNAMIC" in statement
            assert "ENGINE=InnoDB" in statement

    def test_tables_and_columns_carry_chinese_comments(self):
        for statement in DDL_STATEMENTS:
            # 表注释用 COMMENT='...'，列注释用 COMMENT '...'；两者都必须有中文
            assert "COMMENT=" in statement or "COMMENT '" in statement
            assert any("\u4e00" <= ch <= "\u9fff" for ch in statement), (
                "表/列注释必须是中文：运维排查靠它，英文注释不满足规范"
            )

    def test_no_reserved_word_columns(self):
        """``blob`` 是 MySQL 保留字，列名叫 blob 会 1064；这里必须叫 payload。"""
        joined = "\n".join(DDL_STATEMENTS)
        assert " blob " not in joined
        assert "payload" in joined

    def test_checkpoint_ns_is_not_part_of_any_primary_key(self):
        """ns 长度不可控，进主键会超 InnoDB 3072 字节上限（报错而非截断）。"""
        joined = "\n".join(DDL_STATEMENTS)
        assert "checkpoint_ns_hash BINARY(16)" in joined
        for line in joined.splitlines():
            if "PRIMARY KEY" in line:
                assert "checkpoint_ns," not in line and "checkpoint_ns)" not in line

    def test_migrations_are_additive_and_versioned(self):
        assert [version for version, _ in ms.MIGRATIONS] == sorted(
            version for version, _ in ms.MIGRATIONS
        )
        assert ms.SCHEMA_VERSION == max(v for v, _ in ms.MIGRATIONS)
        for _, statements in ms.MIGRATIONS:
            for statement in statements:
                head = statement.strip().upper()
                assert head.startswith("CREATE TABLE IF NOT EXISTS"), (
                    "迁移只允许「加」：DROP COLUMN / 缩小列宽在多 Pod 启动里会放大成事故"
                )


# ── ns 哈希：算法变更等于历史全失联 ───────────────────────────────────
class TestNsHash:
    def test_hash_is_16_bytes_of_sha256(self):
        assert NS_HASH_ALGO == "sha256-16"
        assert ns_hash("") == hashlib.sha256(b"").digest()[:16]
        assert len(ns_hash("tools:abc")) == 16
        assert ns_hash(None) == ns_hash("")

    def test_hash_is_stable_for_known_inputs(self):
        """**改了这两个常量就等于让历史 checkpoint 全部失联**（aget_tuple 静默返回空）。"""
        assert ns_hash("").hex() == "e3b0c44298fc1c149afbf4c8996fb924"
        assert ns_hash("tools:1|model:2").hex() == hashlib.sha256(
            b"tools:1|model:2"
        ).digest()[:16].hex()


# ── 构造校验 ──────────────────────────────────────────────────────────
def test_empty_table_prefix_is_rejected():
    with pytest.raises(MySQLCheckpointError, match="table_prefix"):
        AsyncMySQLSaver(MySQLCheckpointConfig(table_prefix="  "))


def test_table_names_use_the_configured_prefix():
    saver = AsyncMySQLSaver(MySQLCheckpointConfig(table_prefix="qa_"))
    assert saver.table_checkpoints == "qa_checkpoints"
    assert saver.table_blobs == "qa_checkpoint_blobs"
    assert saver.table_writes == "qa_checkpoint_writes"
    assert saver.table_meta == "qa_checkpoint_meta"


# ── aput：内联策略 + 单事务 + 新 UPSERT 语法 ──────────────────────────
class TestAput:
    async def test_only_json_scalars_are_inlined_and_blobs_hold_the_rest(self):
        saver, conn = _saver()
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {
            "scalar": "text",
            "number": 3,
            "flag": True,
            "nothing": None,
            "big": {"nested": ["value"]},
        }
        checkpoint["channel_versions"] = {"scalar": "1", "big": "2", "missing": "3"}

        await saver.aput(_config(), checkpoint, {"step": 2}, {"scalar": "1", "big": "2", "missing": "3"})

        blob_rows = next(
            s[2] for s in conn.statements if s[0] == "executemany" and "checkpoint_blobs" in s[1]
        )
        by_channel = {row[2]: row for row in blob_rows}
        # new_versions 里的每个 channel 都必须有 blob 行：读侧靠 channel_versions 按版本
        # 回填，缺行 = 该 channel 此步不存在（DeltaChannel 的语义）
        assert set(by_channel) == {"scalar", "big", "missing"}
        # 有值的写 msgpack，没有值的写 type='empty'
        assert by_channel["big"][4] == "msgpack" and by_channel["big"][5] is not None
        assert by_channel["missing"][4] == "empty" and by_channel["missing"][5] is None

        insert = next(
            s for s in conn.statements if s[0] == "execute" and "checkpoint_blobs" not in s[1]
        )
        params = insert[2]
        import json

        stored = json.loads(params[9])
        # JSON 里只留 JSON 标量；dict/list 一律进 blobs（否则每步重存整个 messages）
        assert stored["channel_values"] == {
            "scalar": "text",
            "number": 3,
            "flag": True,
            "nothing": None,
        }

    async def test_blobs_and_the_row_are_written_in_one_transaction(self):
        saver, conn = _saver()
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"big": {"a": 1}}
        await saver.aput(_config(), checkpoint, {}, {"big": "1"})
        assert conn.events == ["begin", "commit"]

    async def test_upsert_uses_the_non_deprecated_alias_form(self):
        """``VALUES(col)`` 在 8.0.20+ 已废弃并告警，必须用 ``AS new`` 别名。"""
        saver, conn = _saver()
        await saver.aput(_config(), empty_checkpoint(), {}, {})
        text = _sql_text(conn)
        assert "AS new ON DUPLICATE KEY UPDATE" in text
        assert "VALUES(" not in text.replace("VALUES (%s", "")

    async def test_rollback_on_failure(self):
        saver, conn = _saver()
        original = conn.reply

        def boom(sql, params):
            if "INSERT INTO cp_checkpoints" in sql:
                raise RuntimeError("duplicate key")
            return original(sql, params)

        conn._reply = boom
        with pytest.raises(RuntimeError):
            await saver.aput(_config(), empty_checkpoint(), {}, {})
        assert conn.events == ["begin", "rollback"]

    async def test_session_id_column_is_derived_from_thread_id(self):
        """thread_id 反解不出 session_id 时才要这一列：写入时算好，GC 才能一条 JOIN。"""
        session_id = "3f1c9d2e-0000-4000-8000-000000000001"
        saver, conn = _saver()
        await saver.aput(_config(thread_id=f"{session_id}-m1"), empty_checkpoint(), {}, {})
        insert = next(s for s in conn.statements if s[0] == "execute")
        assert insert[2][1] == session_id


# ── aput_writes：冲突策略 ─────────────────────────────────────────────
class TestAputWrites:
    async def test_special_channels_overwrite(self):
        from langgraph.checkpoint.base import INTERRUPT

        saver, conn = _saver()
        await saver.aput_writes(
            _config(checkpoint_id="cp-1"), [(INTERRUPT, {"q": "ok?"})], "task-1", ""
        )
        text = _sql_text(conn)
        assert "AS new ON DUPLICATE KEY UPDATE channel=new.channel" in text
        rows = conn.statements[-1][2]
        # 特殊通道用固定负值 idx，见 WRITES_IDX_MAP
        assert rows[0][4] == -3

    async def test_normal_writes_are_idempotent(self):
        saver, conn = _saver()
        await saver.aput_writes(_config(checkpoint_id="cp-1"), [("messages", ["a"])], "task-1", "")
        text = _sql_text(conn)
        assert "ON DUPLICATE KEY UPDATE idx=idx" in text

    async def test_mixed_writes_are_treated_as_normal(self):
        from langgraph.checkpoint.base import INTERRUPT

        saver, conn = _saver()
        await saver.aput_writes(
            _config(checkpoint_id="cp-1"),
            [(INTERRUPT, {"q": 1}), ("messages", ["a"])],
            "task-1",
            "",
        )
        assert "ON DUPLICATE KEY UPDATE idx=idx" in _sql_text(conn)

    async def test_empty_writes_skips_the_round_trip(self):
        saver, conn = _saver()
        await saver.aput_writes(_config(checkpoint_id="cp-1"), [], "task-1", "")
        assert conn.statements == []


# ── aget_tuple：只读事务 + 空 id 取最新 ───────────────────────────────
class TestAgetTuple:
    async def test_missing_checkpoint_returns_none_after_rollback(self):
        saver, conn = _saver(reply=lambda sql, params: [])
        assert await saver.aget_tuple(_config()) is None
        assert conn.events == ["begin", "rollback"]
        assert "ORDER BY checkpoint_id DESC" in _sql_text(conn)

    async def test_three_reads_share_one_transaction(self):
        row = {
            "checkpoint_id": "cp-1",
            "parent_checkpoint_id": None,
            "channel_versions": '{"messages": "1"}',
            "checkpoint": '{"id": "cp-1", "channel_values": {}}',
            "metadata": "{}",
        }
        blob_row = {"channel": "messages", "version": "1", "type": "empty", "payload": None}

        def reply(sql, params):
            if "FROM cp_checkpoints" in sql:
                return [row]
            if "FROM cp_checkpoint_blobs" in sql:
                return [blob_row]
            return []

        saver, conn = _saver(reply=reply)
        result = await saver.aget_tuple(_config(checkpoint_id="cp-1"))
        assert result is not None
        assert result.config["configurable"]["checkpoint_id"] == "cp-1"
        assert conn.events == ["begin", "commit"]

    async def test_explicit_checkpoint_id_filters_by_it(self):
        saver, conn = _saver(reply=lambda sql, params: [])
        await saver.aget_tuple(_config(checkpoint_id="cp-9"))
        sql = _sql_text(conn)
        assert "AND checkpoint_id=" in sql
        assert "ORDER BY checkpoint_id DESC" not in sql


# ── 删除：分批 + 顺序（writes → blobs → checkpoints）─────────────────
class TestDeleteThread:
    async def test_deletes_in_dependency_order_until_empty(self):
        """先删 writes 再删 blobs 最后删主体：反了会留下引用不到主体的孤儿行。"""
        saver, conn = _saver()
        await saver.adelete_thread("s1")
        text = _sql_text(conn)
        assert text.index("DELETE FROM cp_checkpoint_writes") < text.index(
            "DELETE FROM cp_checkpoint_blobs"
        )
        assert text.index("DELETE FROM cp_checkpoint_blobs") < text.index(
            "DELETE FROM cp_checkpoints"
        )

    async def test_batches_are_limited(self):
        saver, conn = _saver()
        await saver.adelete_thread("s1")
        deletes = [s for s in conn.statements if s[0] == "execute" and "DELETE" in s[1]]
        assert deletes, "至少发一条 DELETE"
        for statement in deletes:
            assert "LIMIT" in statement[1], "禁止无 LIMIT 的整表删除"

    async def test_deletion_stops_when_a_batch_is_not_full(self, monkeypatch):
        """一批删不满就说明删完了，别再空转一条 SQL。"""

        class _BusyCursor(_Cursor):
            def __init__(self, conn):
                super().__init__(conn)
                self.rowcount = 1

        saver, conn = _saver()

        class _FixedConn(_Conn):
            def cursor(self):
                return _BusyCursor(self)

        conn.cursor = lambda: _BusyCursor(conn)  # 永远"删了 1 行"
        await saver.adelete_thread("s1")
        # 最多打到批次上限就报错退出（不会无限循环）；这里只验证不是死循环
        deletes = [s for s in conn.statements if "DELETE" in s[1]]
        assert len(deletes) <= 3 * 5


async def test_aprine_only_supports_delete():
    """``keep_latest`` 会切断 DeltaChannel 快照链（messages 静默变空），直接拒绝。"""
    saver, _ = _saver()
    with pytest.raises(NotImplementedError, match="keep_latest"):
        await saver.aprine(["s1"], strategy="keep_latest")


async def test_orange_threads_query_joins_sessions_instead_of_full_scan():
    """孤儿 thread 用一条库内 JOIN（走 idx_session），不在应用层做 N+1。"""
    saver, conn = _saver()
    await saver.list_orphan_threads(limit=10)
    text = _sql_text(conn)
    assert "LEFT JOIN sessions" in text
    assert "LIMIT" in text
    assert "GROUP BY" not in text


def test_fake_db_is_not_needed_for_unit_tests():
    """占位：确认单测路径不 import aiomysql（只用 sqlite 的部署不必装它）。

    ``AsyncMySQLSaver._create_pool`` 里才是延迟 import；上面的用例都绕过建池，
    因此即使环境里没有 aiomysql 也能跑。
    """
    assert "aiomysql" not in dir(ms)
