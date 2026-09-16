"""问数业务测试：SQL 网关、元数据可见性、XBOND 领域查询、鉴权复用与端到端。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

import easy_mcp_server.businesses.dataqa.metadata as dataqa_metadata
import easy_mcp_server.businesses.dataqa.server as dataqa_server
import easy_mcp_server.businesses.dataqa.xbond as xbond
from easy_mcp_server.app import create_app
from easy_mcp_server.businesses.dataqa.guard import (
    assert_identifier,
    assert_readonly,
    json_safe,
    max_rows,
)
from easy_mcp_server.businesses.dataqa.server import build


# ── SQL 安全网关 ────────────────────────────────────────────────────────


def test_select_is_allowed_and_limit_appended(monkeypatch):
    monkeypatch.setenv("DATAQA_MAX_ROWS", "100")
    sql = assert_readonly("SELECT region, SUM(amount) FROM dataqa_sales_demo GROUP BY region")
    assert sql.endswith("LIMIT 100")


def test_with_cte_is_allowed():
    sql = assert_readonly("WITH t AS (SELECT 1 AS n) SELECT n FROM t")
    assert sql.startswith("WITH")


def test_xbond_style_query_passes_gateway():
    """XBOND 常用语句含 UPDATE_TIME2 等列名，不能被网关的关键字检查误伤。"""
    sql = assert_readonly(
        "SELECT bond_code, BID_PRICE1, OFFER_YIELD1 FROM fmut_mkt_bonddpanyhis "
        "WHERE UPDATE_TIME2 >= 20260801000000 ORDER BY UPDATE_TIME2 DESC"
    )
    assert sql.startswith("SELECT bond_code")
    assert "LIMIT" in sql


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO t VALUES (1)",
        "UPDATE t SET a=1",
        "DELETE FROM t",
        "DROP TABLE t",
        "ALTER TABLE t ADD COLUMN c INT",
        "CREATE TABLE t (id INT)",
        "TRUNCATE TABLE t",
        "SELECT * FROM t FOR UPDATE",
        "SELECT SLEEP(10)",
        "SELECT * FROM t WHERE a=1 INTO OUTFILE '/tmp/x'",
    ],
)
def test_write_and_dangerous_statements_are_rejected(sql):
    with pytest.raises(ValueError):
        assert_readonly(sql)


def test_stacked_statements_are_rejected():
    with pytest.raises(ValueError):
        assert_readonly("SELECT 1; DROP TABLE t")


def test_write_hidden_after_line_comment_is_rejected():
    """先剥注释再判断：注释里的内容无害，但注释后的堆叠语句必须拦下。"""
    with pytest.raises(ValueError):
        assert_readonly("SELECT 1 -- 查询\n; DROP TABLE t")


def test_system_schema_access_is_rejected():
    with pytest.raises(ValueError):
        assert_readonly("SELECT * FROM information_schema.TABLES")


def test_large_limit_is_capped(monkeypatch):
    monkeypatch.setenv("DATAQA_MAX_ROWS", "50")
    assert assert_readonly("SELECT * FROM t LIMIT 99999").endswith("LIMIT 50")


def test_small_limit_is_preserved(monkeypatch):
    monkeypatch.setenv("DATAQA_MAX_ROWS", "500")
    assert assert_readonly("SELECT * FROM t LIMIT 10").endswith("LIMIT 10")


def test_offset_limit_form_is_left_untouched(monkeypatch):
    """带 OFFSET 的 LIMIT 不改写语义，交由结果截断兜底。"""
    monkeypatch.setenv("DATAQA_MAX_ROWS", "10")
    assert assert_readonly("SELECT * FROM t LIMIT 100, 200").endswith("LIMIT 100, 200")


def test_empty_sql_is_rejected():
    with pytest.raises(ValueError):
        assert_readonly("   ")


@pytest.mark.parametrize("name", ["t;drop", "1abc", "a b", "", "t`x", "t.x", "表"])
def test_invalid_identifier_is_rejected(name):
    with pytest.raises(ValueError):
        assert_identifier(name)


def test_valid_identifier_accepts_backticked_name():
    assert assert_identifier("`dataqa_sales_demo`") == "dataqa_sales_demo"


def test_max_rows_falls_back_to_default(monkeypatch):
    # 不能用 delenv：删掉后 load_env() 会把本地 .env 的值重新注入
    monkeypatch.setenv("DATAQA_MAX_ROWS", "")
    assert max_rows() == 500


def test_json_safe_converts_decimal_and_date():
    from datetime import date

    converted = json_safe({"amount": Decimal("1.50"), "d": date(2026, 9, 4)})
    assert converted == {"amount": 1.5, "d": "2026-09-04"}


# ── 元数据可见性 ────────────────────────────────────────────────────────


def test_list_tables_hides_denied_tables(monkeypatch):
    monkeypatch.setattr(
        dataqa_metadata,
        "readonly_query",
        lambda sql, params=(): [
            {"name": "mcp_api_keys", "object_type": "BASE TABLE", "comment": "",
             "approx_rows": 3, "updated_at": None},
            {"name": "_internal", "object_type": "BASE TABLE", "comment": "",
             "approx_rows": 1, "updated_at": None},
            {"name": "fmut_mkt_bonddpanyhis", "object_type": "BASE TABLE",
             "comment": "xbond市场深度行情表", "approx_rows": 12, "updated_at": None},
        ],
    )
    tables = [t["table"] for t in dataqa_metadata.list_tables()]
    assert "fmut_mkt_bonddpanyhis" in tables
    assert "mcp_api_keys" not in tables
    assert "_internal" not in tables


def test_list_tables_includes_views_with_type(monkeypatch):
    """XBOND 长表视图是 VIEW，必须能被模型看到，否则只能去啃 58 列宽表。"""
    monkeypatch.setattr(
        dataqa_metadata,
        "readonly_query",
        lambda sql, params=(): [
            {"name": "v_xbond_depth", "object_type": "VIEW", "comment": "",
             "approx_rows": None, "updated_at": None},
            {"name": "fmut_mkt_bonddpanyhis", "object_type": "BASE TABLE",
             "comment": "xbond市场深度行情表", "approx_rows": 1, "updated_at": None},
        ],
    )
    items = {t["table"]: t["type"] for t in dataqa_metadata.list_tables()}
    assert items == {"v_xbond_depth": "view", "fmut_mkt_bonddpanyhis": "table"}


def test_list_tables_respects_allowlist(monkeypatch):
    monkeypatch.setenv("DATAQA_ALLOWED_TABLES", "v_xbond_depth")
    monkeypatch.setattr(
        dataqa_metadata,
        "readonly_query",
        lambda sql, params=(): [
            {"name": "v_xbond_depth", "object_type": "VIEW", "comment": "x",
             "approx_rows": 1, "updated_at": None},
            {"name": "market_quotes", "object_type": "BASE TABLE", "comment": "y",
             "approx_rows": 1, "updated_at": None},
        ],
    )
    assert [t["table"] for t in dataqa_metadata.list_tables()] == ["v_xbond_depth"]


def test_describe_table_rejects_invisible_table(monkeypatch):
    monkeypatch.setattr(dataqa_metadata, "readonly_query", lambda sql, params=(): [])
    with pytest.raises(ValueError):
        dataqa_metadata.describe_table("mcp_api_keys")


def test_describe_table_parses_enum_values(monkeypatch):
    def fake(sql, params=()):
        if "COLUMNS" in sql:
            return [
                {"name": "channel", "column_type": "enum('线上','线下')", "nullable": "NO",
                 "default_value": None, "column_key": "", "comment": "销售渠道", "pos": 1},
                {"name": "amount", "column_type": "decimal(18,2)", "nullable": "NO",
                 "default_value": None, "column_key": "", "comment": "成交金额（元）", "pos": 2},
            ]
        return [{"comment": "问数演示-销售明细"}]

    monkeypatch.setattr(dataqa_metadata, "readonly_query", fake)
    info = dataqa_metadata.describe_table("dataqa_sales_demo")
    assert info["comment"] == "问数演示-销售明细"
    assert info["columns"][0]["enum_values"] == ["线上", "线下"]
    assert info["columns"][1]["enum_values"] == []


def test_sample_rows_uses_backticked_identifier(monkeypatch):
    captured = {}

    def fake(sql, params=()):
        captured["sql"] = sql
        return []

    monkeypatch.setattr(dataqa_metadata, "readonly_query", fake)
    dataqa_metadata.sample_rows("dataqa_sales_demo", limit=999)
    assert captured["sql"] == "SELECT * FROM `dataqa_sales_demo` LIMIT 20"


# ── XBOND 领域层 ────────────────────────────────────────────────────────


def test_create_view_sql_expands_all_five_levels():
    sql = xbond.create_view_sql()
    assert sql.startswith("CREATE OR REPLACE VIEW v_xbond_depth AS")
    assert sql.count("UNION ALL") == xbond.MAX_LEVEL - 1
    for level in range(1, xbond.MAX_LEVEL + 1):
        assert f"BID_PRICE{level} AS bid_price" in sql
        assert f"OFFER_YIELD{level} AS offer_yield" in sql
        assert f"{level} AS level_no" in sql


def test_view_uses_unnumbered_bid_settletype_for_level_one():
    sql = xbond.create_view_sql()
    assert "BID_SETTLTYPE AS bid_settletype" in sql  # 第 1 档列名没有数字后缀
    assert "BID_SETTLTYPE1 AS bid_settletype" not in sql
    assert "BID_SETTLTYPE5 AS bid_settletype" in sql


def test_create_view_sql_respects_env_names(monkeypatch):
    monkeypatch.setenv("XBOND_TABLE", "t_bond")
    monkeypatch.setenv("XBOND_VIEW", "v_bond")
    sql = xbond.create_view_sql()
    assert "FROM t_bond" in sql
    assert "CREATE OR REPLACE VIEW v_bond AS" in sql


def test_cutoff_time_is_start_of_day_yyyymmdd():
    cutoff = xbond.cutoff_time(30)
    text = str(cutoff)
    assert len(text) == 14 and text.endswith("000000")
    expected = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")
    assert text.startswith(expected)


def test_cutoff_time_defaults_to_configured_days(monkeypatch):
    monkeypatch.setenv("XBOND_DEFAULT_DAYS", "7")
    assert str(xbond.cutoff_time()).startswith(
        (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")
    )


def test_format_update_time_readable():
    assert xbond.format_update_time(20260908143000) == "2026-09-08 14:30:00"
    assert xbond.format_update_time(None) == ""


def test_list_bonds_sql_filters_by_keyword_and_window():
    sql, params = xbond.list_bonds_sql(keyword="国债", days=30, limit=20)
    assert "bond_code LIKE %s OR bond_name LIKE %s" in sql
    assert "GROUP BY bond_code" in sql
    # 前 5 个参数是 5 个档位分支各自的时间下界，最后两个是关键词
    assert str(params[0]).endswith("000000")
    assert params[5] == "%国债%" and params[6] == "%国债%"


def test_list_bonds_sql_without_keyword():
    sql, params = xbond.list_bonds_sql()
    assert "LIKE" not in sql
    assert len(params) == xbond.MAX_LEVEL  # 每档分支一个时间参数


def test_long_format_sql_pushes_time_filter_into_every_branch():
    """时间条件必须下推到每个档位分支，否则 UNION ALL 会全表物化。"""
    sql, params = xbond.long_format_sql(days=30)
    assert sql.count("UPDATE_TIME2 >= %s") == xbond.MAX_LEVEL
    assert len(params) == xbond.MAX_LEVEL


def test_level_filter_emits_only_that_branch():
    sql, params = xbond.long_format_sql(days=7, level=3)
    assert "3 AS level_no" in sql
    assert "2 AS level_no" not in sql
    assert sql.count("UPDATE_TIME2 >= %s") == 1
    assert len(params) == 1


def test_depth_sql_daily_joins_last_row_per_day():
    sql, params = xbond.depth_sql(days=30, level=1, granularity="daily", limit=100)
    assert "GROUP BY bond_code, update_date" in sql
    assert "t.update_date <=> d.update_date" in sql
    assert "UPDATE_TIME2 >= %s" in sql  # 条件下推到基表列
    # 长表 SQL 出现两次（明细 + 聚合），参数也传两遍
    assert len(params) == 2 and params[0] == params[1]
    assert sql.rstrip().endswith("LIMIT 100")


def test_depth_sql_latest_joins_newest_row_per_bond():
    sql, params = xbond.depth_sql(granularity="latest")
    assert "MAX(update_time) AS mt" in sql
    assert "GROUP BY bond_code, update_date" not in sql
    assert "1 AS level_no" in sql and "5 AS level_no" in sql  # 未指定档位时全档
    assert len(params) == xbond.MAX_LEVEL * 2  # 5 个分支参数 × 2（明细 + 聚合）


def test_depth_sql_raw_has_no_join():
    sql, _ = xbond.depth_sql(granularity="raw")
    assert "JOIN" not in sql
    assert "ORDER BY update_time DESC" in sql


def test_depth_sql_filters_by_bond_codes():
    sql, params = xbond.depth_sql(bond_codes=["240010", "220205"], granularity="raw")
    assert "BOND_CODE = %s" in sql
    assert "240010" in params and "220205" in params


def test_bare_bond_code_also_matches_suffixed_code():
    """裸代码必须能匹配带后缀的代码，否则会静默返回空。"""
    sql, params = xbond.depth_sql(bond_codes=["2105005"], granularity="raw")
    assert "(BOND_CODE = %s OR BOND_CODE LIKE %s)" in sql
    assert "2105005" in params and "2105005.%" in params


def test_suffixed_bond_code_uses_exact_match():
    sql, params = xbond.depth_sql(bond_codes=["2105005.IB"], granularity="raw")
    assert "LIKE" not in sql
    # 每个档位分支各带一份参数
    assert params.count("2105005.IB") == xbond.MAX_LEVEL


@pytest.mark.parametrize("level", [0, 6, 99])
def test_invalid_level_is_rejected(level):
    with pytest.raises(ValueError):
        xbond.depth_sql(level=level)


def test_invalid_granularity_is_rejected():
    with pytest.raises(ValueError):
        xbond.depth_sql(granularity="weekly")


def test_row_limit_is_capped_by_max_rows(monkeypatch):
    monkeypatch.setenv("DATAQA_MAX_ROWS", "50")
    assert xbond.row_limit(1000) == 50
    assert xbond.row_limit(None) == 50


def test_format_quotes_dedupes_and_adds_readable_time():
    rows = [
        {"bond_code": "2400006", "update_time": 20260908143000, "level_no": 1,
         "price_id": "A", "bid_price": Decimal("100.25")},
        {"bond_code": "2400006", "update_time": 20260908143000, "level_no": 1,
         "price_id": "A", "bid_price": Decimal("100.25")},  # join 产生的重复
    ]
    quotes = xbond.format_quotes(rows)
    assert len(quotes) == 1
    assert quotes[0]["update_time_readable"] == "2026-09-08 14:30:00"
    assert quotes[0]["bid_price"] == Decimal("100.25")


def test_summarize_quotes_reports_bond_count_and_range():
    rows = [
        {"bond_code": "2400006", "update_time": 20260801093000},
        {"bond_code": "2400007", "update_time": 20260908143000},
    ]
    summary = xbond.summarize_quotes(rows, 30, "daily")
    assert "2 只债券" in summary and "2026-09-08 14:30:00" in summary
    assert "没有匹配" in xbond.summarize_quotes([], 30, "daily")


def test_schema_help_points_to_view_and_time_column():
    help_text = xbond.schema_help()
    assert help_text["view"] == "v_xbond_depth"
    assert "update_time" in help_text["columns"]
    assert "UPDATE_TIME2" not in str(help_text["time_filter"])  # 视图里已重命名


# ── 端到端：复用 auth.py 的鉴权与身份注入 ──────────────────────────────


DEPTH_ROW = {
    "update_time": 20260908143000,
    "update_date": "2026-09-08",
    "update_time_str": "143000",
    "feed_code": "XBOND",
    "bond_code": "2400006",
    "bond_name": "24国债06",
    "bond_type": "国债",
    "term": "9.8Y",
    "market_depth": "5",
    "price_id": "A",
    "transact_time": "20260908143000",
    "level_no": 1,
    "bid_settletype": "T+0",
    "bid_qty": Decimal("3000.00"),
    "bid_price": Decimal("100.2500"),
    "bid_yield": Decimal("2.135000"),
    "offer_settletype": "T+0",
    "offer_qty": Decimal("2000.00"),
    "offer_price": Decimal("100.3000"),
    "offer_yield": Decimal("2.130000"),
}


@pytest.fixture
async def dataqa_env(monkeypatch):
    """挂载真实 dataqa 业务，数据库查询替换为内存行。"""
    calls: list[tuple[str, tuple]] = []

    def fake_query(sql, params=()):
        calls.append((sql, tuple(params)))
        if "information_schema.TABLES" in sql:
            return [
                {"name": "v_xbond_depth", "object_type": "VIEW", "comment": "XBOND 长表视图",
                 "approx_rows": None, "updated_at": None},
                {"name": "fmut_mkt_bonddpanyhis", "object_type": "BASE TABLE",
                 "comment": "xbond市场深度行情表", "approx_rows": 12, "updated_at": None},
            ]
        if "information_schema.COLUMNS" in sql:
            return [
                {"name": "bond_code", "column_type": "varchar(30)", "nullable": "YES",
                 "default_value": None, "column_key": "", "comment": "债券代码", "pos": 5},
                {"name": "level_no", "column_type": "int(11)", "nullable": "NO",
                 "default_value": None, "column_key": "", "comment": "档位", "pos": 12},
            ]
        if "GROUP BY bond_code" in sql and "MAX(update_time) AS last_update" in sql:
            return [
                {"bond_code": "2400006", "bond_name": "24国债06", "bond_type": "国债",
                 "term": "9.8Y", "quote_rows": 320, "last_update": 20260908143000}
            ]
        return [dict(DEPTH_ROW), dict(DEPTH_ROW)]

    monkeypatch.setattr(dataqa_metadata, "readonly_query", fake_query)
    monkeypatch.setattr(dataqa_server, "readonly_query", fake_query)
    monkeypatch.setenv("DATAQA_MAX_ROWS", "100")

    verifier = lambda business, key: {"qa-alice": "alice", "qa-bob": "bob"}.get(key)
    app = create_app(verifier, businesses_override=[("dataqa", build())])
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]

    class Holder:
        base = f"http://127.0.0.1:{port}"

    Holder.calls = calls
    try:
        yield Holder
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=10)


async def _call_tool(base: str, key: str, tool: str, args: dict | None = None):
    async with streamablehttp_client(
        f"{base}/mcp/dataqa/", headers={"Authorization": f"Bearer {key}"}
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool, args or {})


async def test_get_bond_quotes_returns_monthly_daily_quotes(dataqa_env):
    """主场景：'Xbond 近一个月各债券行情'。"""
    result = await _call_tool(
        dataqa_env.base,
        "qa-alice",
        "get_bond_quotes",
        {"days": 30, "granularity": "daily", "level": 1},
    )
    text = result.content[0].text
    assert "2400006" in text and "24国债06" in text
    assert "2026-09-08 14:30:00" in text  # update_time 已格式化
    assert "100.25" in text and "2.135" in text
    assert "1 只债券" in text  # 去重后只剩一只
    assert any("GROUP BY bond_code, update_date" in sql for sql, _ in dataqa_env.calls)


async def test_get_bond_quotes_accepts_bond_codes(dataqa_env):
    result = await _call_tool(
        dataqa_env.base,
        "qa-alice",
        "get_bond_quotes",
        {"bond_codes": ["2400006"], "granularity": "latest"},
    )
    text = result.content[0].text
    assert "2400006" in text
    assert any("BOND_CODE = %s" in sql for sql, _ in dataqa_env.calls)


async def test_get_bond_quotes_reports_invalid_granularity(dataqa_env):
    result = await _call_tool(
        dataqa_env.base, "qa-alice", "get_bond_quotes", {"granularity": "weekly"}
    )
    assert "false" in result.content[0].text.lower()


async def test_list_bonds_returns_catalog(dataqa_env):
    result = await _call_tool(dataqa_env.base, "qa-alice", "list_bonds", {"days": 30})
    text = result.content[0].text
    assert "2400006" in text and "国债" in text


async def test_describe_xbond_schema_is_callable(dataqa_env):
    result = await _call_tool(dataqa_env.base, "qa-bob", "describe_xbond_schema")
    text = result.content[0].text
    assert "v_xbond_depth" in text and "update_time" in text


async def test_query_still_available_for_ad_hoc_sql(dataqa_env):
    result = await _call_tool(
        dataqa_env.base,
        "qa-alice",
        "query",
        {"sql": "SELECT bond_code, bid_price FROM v_xbond_depth WHERE level_no = 1"},
    )
    text = result.content[0].text
    assert "2400006" in text
    assert any(sql.strip().startswith("SELECT bond_code") for sql, _ in dataqa_env.calls)


async def test_query_returns_ok_false_for_blocked_sql(dataqa_env):
    result = await _call_tool(
        dataqa_env.base, "qa-alice", "query", {"sql": "DELETE FROM fmut_mkt_bonddpanyhis"}
    )
    assert "false" in result.content[0].text.lower()


async def test_both_users_get_same_rows_without_row_level_isolation(dataqa_env):
    """当前阶段不做行级隔离：alice 与 bob 看到相同数据，但身份各自独立。"""
    alice = await _call_tool(
        dataqa_env.base, "qa-alice", "get_bond_quotes", {"days": 7}
    )
    bob = await _call_tool(dataqa_env.base, "qa-bob", "get_bond_quotes", {"days": 7})
    assert alice.content[0].text == bob.content[0].text


async def test_invalid_api_key_is_rejected(dataqa_env):
    """鉴权完全复用全局中间件：无效 Key 直接 401，业务代码零感知。"""
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{dataqa_env.base}/mcp/dataqa/",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": "Bearer wrong-key"},
        )
    assert resp.status_code == 401
