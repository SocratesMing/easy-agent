"""策略问数测试：行级隔离（AUTHOR）、SQL 构造、毫秒时间戳与端到端调用。"""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

import easy_mcp_server.businesses.strategyqa.server as strategy_server
from easy_mcp_server.app import create_app
from easy_mcp_server.businesses.strategyqa import strategy
from easy_mcp_server.businesses.strategyqa.server import build

MILLIS = 1789952000000


# ── 行级隔离：AUTHOR 条件不可绕过 ───────────────────────────────────────


def test_list_forces_author_filter():
    sql, params = strategy.list_strategies_sql(viewer="zhangsan")
    assert "AUTHOR = %s" in sql
    assert "AUTH_VIEW = 1" in sql
    assert params[0] == "zhangsan"


def test_author_filter_is_always_first_condition():
    """AUTHOR 必须固定为第一个条件，便于审计，也不给拼接留空间。"""
    sql, params = strategy.list_strategies_sql(
        viewer="zhangsan", keyword="股票", strategy_type="自营"
    )
    assert sql.index("AUTHOR = %s") < sql.index("LIKE %s")
    assert params[0] == "zhangsan"


def test_different_viewers_get_different_bindings():
    _, alice_params = strategy.list_strategies_sql(viewer="alice")
    _, bob_params = strategy.list_strategies_sql(viewer="bob")
    assert alice_params == ("alice",)
    assert bob_params == ("bob",)


def test_get_strategy_forces_author_filter():
    sql, params = strategy.get_strategy_sql(viewer="zhangsan", strategy_id="QL001")
    assert "AUTHOR = %s" in sql
    assert "AUTH_VIEW = 1" in sql
    assert "STRATEGY_ID = %s" in sql
    assert params == ("zhangsan", "QL001")


def test_visibility_also_requires_auth_view():
    """AUTH_VIEW=0 表示谁都不能看（含作者本人），因此必须叠加该条件。"""
    sql, _ = strategy.list_strategies_sql(viewer="zhangsan")
    assert "AUTH_VIEW = 1" in sql


def test_no_parameter_can_relax_isolation():
    """把所有可传参数拉满，两个硬条件依然各只有一条。"""
    sql, params = strategy.list_strategies_sql(
        viewer="zhangsan",
        keyword="x",
        strategy_type="自营",
        source="LOC",
        report_type=1,
        order_by="total_yield",
        limit=10,
    )
    assert sql.count("AUTHOR = %s") == 1
    assert sql.count("AUTH_VIEW = 1") == 1
    assert params.count("zhangsan") == 1


# ── SQL 构造 ────────────────────────────────────────────────────────────


def test_list_order_and_limit():
    sql, _ = strategy.list_strategies_sql(viewer="a")
    assert "ORDER BY UPDATE_TIME DESC" in sql
    assert sql.endswith("LIMIT 50")


def test_keyword_searches_id_name_and_tags():
    sql, params = strategy.list_strategies_sql(viewer="a", keyword="股票")
    assert sql.count("LIKE %s") == 3
    assert params == ("a", "%股票%", "%股票%", "%股票%")


def test_filters_are_parameterised():
    sql, params = strategy.list_strategies_sql(
        viewer="a", strategy_type="自营", source="LOC", report_type=3
    )
    assert "STRATEGY_TYPE = %s" in sql
    assert "STRATEGY_SOURCE = %s" in sql
    assert "REPORT_TYPE = %s" in sql
    assert params == ("a", "自营", "LOC", 3)


def test_blank_filters_ignored():
    sql, params = strategy.list_strategies_sql(viewer="a", strategy_type="  ", source="")
    assert "STRATEGY_TYPE = %s" not in sql
    assert "STRATEGY_SOURCE = %s" not in sql
    assert params == ("a",)


def test_order_by_uses_whitelist_and_falls_back():
    assert "ORDER BY TOTAL_YIELD" in strategy.list_strategies_sql(
        viewer="a", order_by="total_yield"
    )[0]
    # 不在白名单里的值一律回退，避免 ORDER BY 注入
    sql, _ = strategy.list_strategies_sql(viewer="a", order_by="UPDATE_TIME; DROP TABLE t")
    assert "ORDER BY UPDATE_TIME DESC" in sql
    assert "DROP" not in sql


def test_ascending_order_supported():
    assert "ORDER BY CREATE_TIME ASC" in strategy.list_strategies_sql(
        viewer="a", order_by="create_time", descending=False
    )[0]


def test_limit_is_capped(monkeypatch):
    monkeypatch.setenv("QUERYKIT_MAX_ROWS", "20")
    assert strategy.list_strategies_sql(viewer="a", limit=9999)[0].endswith("LIMIT 20")


def test_list_sql_excludes_large_text_column():
    """PROFIT_LOSS_CHART 是大字段，任何列表查询都不该带出来。"""
    assert "PROFIT_LOSS_CHART" not in strategy.list_strategies_sql(viewer="a")[0]


def test_detail_sql_excludes_large_text_column():
    assert "PROFIT_LOSS_CHART" not in strategy.get_strategy_sql("a", "QL001")[0]


# ── 毫秒时间戳 ──────────────────────────────────────────────────────────


def test_format_millis_matches_local_time():
    expected = datetime.fromtimestamp(MILLIS / 1000).strftime("%Y-%m-%d %H:%M:%S")
    assert strategy.format_millis(MILLIS) == expected


def test_format_millis_accepts_second_precision():
    assert strategy.format_millis(MILLIS // 1000) == strategy.format_millis(MILLIS)


@pytest.mark.parametrize("value", [None, "", 0, "abc"])
def test_format_millis_returns_empty_for_invalid(value):
    assert strategy.format_millis(value) == ""


def test_decorate_adds_readable_fields():
    row = strategy.decorate([{"CREATE_TIME": MILLIS, "UPDATE_TIME": MILLIS, "REPORT_TYPE": 3}])[0]
    assert row["create_time_text"] == strategy.format_millis(MILLIS)
    assert row["update_time_text"] == strategy.format_millis(MILLIS)
    assert row["report_type_text"] == "实盘"


def test_decorate_handles_null_report_type():
    assert strategy.decorate([{"REPORT_TYPE": None}])[0]["report_type_text"] is None


def test_summarize_counts_by_type():
    rows = [{"STRATEGY_TYPE": "自营"}, {"STRATEGY_TYPE": "做市"}]
    text = strategy.summarize(rows, "zhangsan")
    assert "账号 zhangsan" in text and "共 2 个策略" in text


def test_summarize_names_account_when_empty():
    """空结果要点出账号名，并说明"可能被隐藏"，便于区分账号不匹配与权限过滤。"""
    text = strategy.summarize([], "zhangsan")
    assert "账号 zhangsan" in text
    assert "没有可查看" in text
    assert "AUTH_VIEW=0" in text


def test_schema_help_documents_isolation():
    help_text = strategy.schema_help()
    assert help_text["table"] == "fmut2_strategy_manage"
    assert "毫秒" in help_text["time"]
    assert "AUTHOR" in help_text["visibility"]
    assert "AUTH_VIEW=0" in help_text["visibility"]
    assert help_text["enums"]["report_type"]["3"] == "实盘"
    assert any("PROFIT_LOSS_CHART" in note for note in help_text["notes"])


# ── 端到端 ──────────────────────────────────────────────────────────────


def _row(strategy_id: str, author: str, name: str, auth_view: int = 1) -> dict:
    return {
        "STRATEGY_ID": strategy_id,
        "STRATEGY_NAME": name,
        "STRATEGY_TAGS": "股票,多因子",
        "STRATEGY_TYPE": "自营",
        "AUTHOR": author,
        "RP_NUM": 3,
        "STRATEGY_SOURCE": "LOC",
        "AUTH_VIEW": auth_view,
        "REPORT_TYPE": 3,
        "TOTAL_YIELD": Decimal("0.155"),
        "BANK_ID": "000052",
        "CREATE_TIME": MILLIS,
        "UPDATE_TIME": MILLIS,
    }


ROWS_BY_AUTHOR = {
    "alice": [
        _row("QL001", "alice", "爱丽丝的策略"),
        # 自己的但 AUTH_VIEW=0：谁都不能看，包括作者
        _row("QL009", "alice", "爱丽丝的隐藏策略", auth_view=0),
    ],
    "bob": [_row("QL002", "bob", "鲍勃的策略")],
}


@pytest.fixture
async def strategy_env(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    def fake_query(sql, params=()):
        """模拟数据库：按 AUTHOR 与 AUTH_VIEW 过滤，详情再按 STRATEGY_ID 过滤。"""
        calls.append((sql, tuple(params)))
        viewer = params[0] if params else None
        rows = ROWS_BY_AUTHOR.get(viewer, [])
        if "AUTH_VIEW = 1" in sql:
            rows = [r for r in rows if r.get("AUTH_VIEW") == 1]
        if "STRATEGY_ID = %s" in sql and len(params) > 1:
            strategy_id = params[1]
            rows = [r for r in rows if r["STRATEGY_ID"] == strategy_id]
        return [dict(r) for r in rows]

    monkeypatch.setattr(strategy_server, "readonly_query", fake_query)
    monkeypatch.setenv("QUERYKIT_MAX_ROWS", "100")

    verifier = lambda business, key: {"sq-alice": "alice", "sq-bob": "bob"}.get(key)
    app = create_app(verifier, businesses_override=[("strategyqa", build())])
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
        f"{base}/mcp/strategyqa/", headers={"Authorization": f"Bearer {key}"}
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool, args or {})


async def _tool_names(base: str, key: str) -> set[str]:
    async with streamablehttp_client(
        f"{base}/mcp/strategyqa/", headers={"Authorization": f"Bearer {key}"}
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.list_tools()
            return {tool.name for tool in result.tools}


async def test_users_only_see_their_own_strategies(strategy_env):
    alice = await _call_tool(strategy_env.base, "sq-alice", "list_strategies")
    bob = await _call_tool(strategy_env.base, "sq-bob", "list_strategies")
    assert "爱丽丝的策略" in alice.content[0].text
    assert "鲍勃的策略" not in alice.content[0].text
    assert "鲍勃的策略" in bob.content[0].text
    assert "爱丽丝的策略" not in bob.content[0].text


async def test_own_hidden_strategy_is_not_returned(strategy_env):
    """AUTH_VIEW=0 表示谁都不能看——作者自己也不例外。"""
    result = await _call_tool(strategy_env.base, "sq-alice", "list_strategies")
    text = result.content[0].text
    assert "爱丽丝的策略" in text
    assert "爱丽丝的隐藏策略" not in text
    assert "QL009" not in text


async def test_own_hidden_strategy_detail_is_not_found(strategy_env):
    result = await _call_tool(
        strategy_env.base, "sq-alice", "get_strategy", {"strategy_id": "QL009"}
    )
    assert "false" in result.content[0].text.lower()


async def test_author_binding_comes_from_api_key(strategy_env):
    await _call_tool(strategy_env.base, "sq-alice", "list_strategies")
    sql, params = strategy_env.calls[-1]
    assert "AUTHOR = %s" in sql
    assert params[0] == "alice"


async def test_filters_are_parameterised(strategy_env):
    await _call_tool(
        strategy_env.base,
        "sq-alice",
        "list_strategies",
        {"strategy_type": "自营", "source": "LOC"},
    )
    sql, params = strategy_env.calls[-1]
    assert "STRATEGY_TYPE = %s" in sql and "STRATEGY_SOURCE = %s" in sql
    assert params == ("alice", "自营", "LOC")


async def test_get_strategy_is_scoped_to_owner(strategy_env):
    own = await _call_tool(
        strategy_env.base, "sq-alice", "get_strategy", {"strategy_id": "QL001"}
    )
    assert "爱丽丝的策略" in own.content[0].text

    # alice 查 bob 的策略 → 查不到（模拟库里 AUTHOR 不匹配）
    other = await _call_tool(
        strategy_env.base, "sq-alice", "get_strategy", {"strategy_id": "QL002"}
    )
    assert "false" in other.content[0].text.lower()
    assert "只能查看自己创建的策略" in other.content[0].text


async def test_raw_sql_and_sample_tools_are_not_exposed(strategy_env):
    """权限闭环的关键：不能暴露能绕过 AUTHOR 过滤的工具。"""
    names = await _tool_names(strategy_env.base, "sq-alice")
    assert "query" not in names
    assert "sample_rows" not in names
    assert {"list_strategies", "get_strategy", "describe_strategy_schema"} <= names


async def test_describe_strategy_schema_is_callable(strategy_env):
    result = await _call_tool(strategy_env.base, "sq-alice", "describe_strategy_schema")
    text = result.content[0].text
    assert "fmut2_strategy_manage" in text and "毫秒" in text


async def test_describe_table_exposes_schema_not_rows(strategy_env):
    result = await _call_tool(
        strategy_env.base, "sq-alice", "describe_table", {"table": "fmut2_strategy_manage"}
    )
    text = result.content[0].text
    assert "STRATEGY_ID" in text
    assert "爱丽丝的策略" not in text  # 不含任何业务数据


async def test_invalid_api_key_is_rejected(strategy_env):
    import httpx

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{strategy_env.base}/mcp/strategyqa/",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": "Bearer wrong-key"},
        )
    assert resp.status_code == 401
