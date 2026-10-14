"""黄金价格多因子归因场景测试：白名单校验、参数边界与 SQL 形态（不连库）。

重点盯两类容易出错的地方：

- ``group`` 是**列名**，无法用预编译占位符保护，只能白名单拒绝自由文本——
  文档专门强调过这点，这里用注入样例钉住。
- 「日期留空 → 先取最新交易日」的统一行为，以及白名单**只含本业务那张表**
  （拆包后各业务互不可见）。
"""

from __future__ import annotations

import json

import pytest

import easy_mcp_server.businesses.gold_analysis.server as gold_server
from easy_mcp_server import context
from easy_mcp_server.businesses.gold_analysis import gold_analysis
from easy_mcp_server.businesses.gold_analysis.server import build

LATEST = "2026-09-28"
DEFAULT_MODEL = "多元线性回归"
TABLE = "fmqt_gold_analysis_result"


@pytest.fixture
def captured(monkeypatch):
    """拦截数据库调用：日期查询返回固定最新日期，其余返回空。"""
    calls: list[tuple[str, tuple]] = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        if "MAX(`creation_time`)" in sql:
            return [{"d": LATEST}]
        return []

    monkeypatch.setattr(gold_server, "readonly_query", fake_query)
    return calls


async def _tool_names(mcp) -> set[str]:
    return {tool.name for tool in await mcp.list_tools()}


async def _call(mcp, name: str, **kwargs):
    context.set_identity("tester", "gold_analysis")
    try:
        result = await mcp.call_tool(name, kwargs)
    finally:
        context.clear_identity()
    if isinstance(result, tuple):
        content, structured = result
        if structured is not None:
            return structured
        return json.loads(content[0].text)
    return result


# ── 工具注册 ────────────────────────────────────────────────────────────


async def test_all_tools_registered(captured):
    """13 个工具都要在（数量 + 关键工具名双重断言）。"""
    names = await _tool_names(build())
    assert len(names) == 13
    assert {
        "describe_gold_schema",
        "get_gold_latest_date",
        "get_gold_prev_date",
        "list_gold_dates",
        "list_gold_attribution_history",
        "get_gold_attribution",
        "compare_gold_models",
        "get_gold_group_trend",
        "get_gold_group_series",
        "get_gold_model_coverage",
        "get_gold_group_change",
        "get_gold_group_stats",
        "get_gold_total_contribution",
    } <= names


async def test_schema_tool_documents_units_and_models(captured):
    """口径必须写给模型看：小数口径、两个模型、六个分组、累计窗口。"""
    result = await _call(build(), "describe_gold_schema")
    assert result["ok"] is True
    assert "小数" in result["units"]
    assert list(result["models"]) == list(gold_analysis.MODELS)
    assert set(result["groups"]) == set(gold_analysis.VALUE_COLUMNS)
    assert "累计" in result["window_rule"]


# ── 枚举与参数校验（纯函数） ────────────────────────────────────────────


def test_model_whitelist():
    assert gold_analysis.validate_model(None) == DEFAULT_MODEL
    assert gold_analysis.validate_model("") == DEFAULT_MODEL
    assert gold_analysis.validate_model("随机森林") == "随机森林"
    with pytest.raises(ValueError):
        gold_analysis.validate_model("LightGBM")


def test_group_whitelist_rejects_free_text():
    """group 进 SQL 的是列名，不能靠占位符——必须白名单拒绝。"""
    assert gold_analysis.validate_group("fund_flow") == "fund_flow"
    for bad in ("nope", "fund_flow; DROP TABLE x", "`fund_flow`", "fund_flow()"):
        with pytest.raises(ValueError):
            gold_analysis.validate_group(bad)


def test_days_and_limit_are_bounded():
    assert gold_analysis.bounded_days(None) == 30
    assert gold_analysis.bounded_days(999) == gold_analysis.MAX_DAYS
    assert gold_analysis.bounded_days(-5) == 1
    assert gold_analysis.bounded_limit(None) == 30
    assert gold_analysis.bounded_limit(999) == gold_analysis.MAX_LIMIT
    assert gold_analysis.bounded_limit(-1) == 1


def test_date_must_be_yyyy_mm_dd():
    assert gold_analysis.validate_date("2026-09-28") == "2026-09-28"
    assert gold_analysis.validate_date(None) is None
    assert gold_analysis.validate_date("") is None
    for bad in ("20260928", "2026/09/28", "2026-9-28"):
        with pytest.raises(ValueError):
            gold_analysis.validate_date(bad)


# ── 白名单只含本业务的表 ────────────────────────────────────────────────


def test_policy_only_sees_own_table(monkeypatch):
    """拆包后每个业务只管自己的表——别的业务的表必须看不见。"""
    monkeypatch.setenv("GOLD_ALLOWED_TABLES", "")
    policy = gold_server._policy()
    assert set(policy.allowed or ()) == {TABLE}
    assert policy.is_visible(TABLE) is True
    for other in ("fmqt_bond_exec_part_data", "fmqt_predictions_rates", "weekly_analyzer_data"):
        assert policy.is_visible(other) is False


def test_default_allowed_tables_is_single_wide_table():
    assert gold_analysis.default_allowed_tables() == (TABLE,)


# ── SQL 形态 ────────────────────────────────────────────────────────────


def test_latest_date_sql_without_model_has_no_where():
    """模型留空时不加 WHERE——一条聚合 SQL 走索引即取全表最新。"""
    sql, params = gold_analysis.latest_date_sql(None)
    assert "WHERE" not in sql
    assert params == ()


def test_latest_date_sql_with_model_filters():
    sql, params = gold_analysis.latest_date_sql("随机森林")
    assert "WHERE `model_name` = %s" in sql
    assert params == ("随机森林",)


def test_group_trend_unions_all_six_groups():
    """宽表转长表靠 UNION ALL，6 个分组共 6 段 SQL。"""
    sql, params = gold_analysis.group_trend_sql(None, 5)
    assert sql.count("UNION ALL") == len(gold_analysis.VALUE_COLUMNS) - 1
    assert len(params) == len(gold_analysis.VALUE_COLUMNS) + 1
    for column in gold_analysis.VALUE_COLUMNS:
        assert f"'{column}' AS group_key" in sql


def test_group_series_uses_requested_column_only():
    sql, params = gold_analysis.group_series_sql(None, "treasury_factor", 10)
    assert "`treasury_factor` AS value" in sql
    assert "`fund_flow`" not in sql
    assert params == (DEFAULT_MODEL,)


def test_total_contribution_sums_all_groups():
    sql, _params = gold_analysis.total_contribution_sql(None, 5)
    for column in gold_analysis.VALUE_COLUMNS:
        assert f"`{column}`" in sql
    assert sql.count(" + ") == len(gold_analysis.VALUE_COLUMNS) - 1


# ── 工具层行为 ──────────────────────────────────────────────────────────


async def test_omitted_date_falls_back_to_latest(captured):
    """creation_time 留空 → 先查最新日期，再查归因（两次查询）。"""
    result = await _call(build(), "get_gold_attribution")
    assert result["ok"] is True
    assert len(captured) == 2
    assert "MAX(`creation_time`)" in captured[0][0]
    assert LATEST in captured[1][1]


async def test_explicit_date_skips_latest_lookup(captured):
    await _call(build(), "get_gold_attribution", creation_time="2026-09-25")
    assert len(captured) == 1
    assert captured[0][1] == (DEFAULT_MODEL, "2026-09-25")


async def test_compare_models_resolves_latest_when_omitted(captured):
    await _call(build(), "compare_gold_models")
    assert len(captured) == 2
    assert captured[1][1] == (LATEST,)


async def test_unknown_group_returns_ok_false(captured):
    result = await _call(build(), "get_gold_group_series", group="nope")
    assert result["ok"] is False
    assert "group" in result["error"]


async def test_unknown_model_returns_ok_false(captured):
    result = await _call(build(), "get_gold_attribution", model="LightGBM")
    assert result["ok"] is False


async def test_bad_date_returns_ok_false(captured):
    result = await _call(build(), "list_gold_dates", days=5)
    assert result["ok"] is True
    # 日期查询本身不接受 date 参数，这里用 prev_date 验证格式校验
    result = await _call(build(), "get_gold_prev_date", creation_time="20260928")
    assert result["ok"] is False
    assert "YYYY-MM-DD" in result["error"]


async def test_coverage_needs_no_parameters(captured):
    result = await _call(build(), "get_gold_model_coverage")
    assert result["ok"] is True
    assert len(captured) == 1
    assert "GROUP BY `model_name`" in captured[0][0]
