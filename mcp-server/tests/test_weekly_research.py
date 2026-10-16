"""研报观点场景测试：工具注册、表隔离、周标签校验与 JSON 展开 SQL（不连库）。

观点表是一周一行 + ``data_json`` 里嵌 ``views`` 数组，工具靠 MySQL ``JSON_TABLE``
把它展开成「一行一个观点」——这条是本场景的核心，故钉住 SQL 形态。
"""

from __future__ import annotations

import pytest

import easy_mcp_server.businesses.weekly_research.server as weekly_server
from easy_mcp_server.businesses.weekly_research import weekly_research
from easy_mcp_server.businesses.weekly_research.server import build

WEEK = "20260928-20261004"


async def test_core_tools_registered():
    names = {tool.name for tool in await build().list_tools()}
    assert {
        "describe_weekly_research_schema",
        "list_weekly_opinion_weeks",
        "get_weekly_opinion_summary",
        "get_weekly_opinions",
        "get_weekly_opinion_direction_counts",
        "get_weekly_asset_consensus",
        "get_weekly_opinion_sources",
        "get_weekly_asset_opinion_trend",
        "get_weekly_market_overview",
        "list_weekly_macro_weeks",
        "get_weekly_macro_indicators",
        "get_weekly_macro_forecasts",
        "get_weekly_macro_indicator_trend",
        "get_weekly_macro_coverage",
        "get_weekly_macro_numeric_average",
        "get_weekly_macro_text_mode",
    } <= names


def test_policy_only_sees_own_tables(monkeypatch):
    monkeypatch.setenv("WEEKLY_RESEARCH_ALLOWED_TABLES", "")
    policy = weekly_server._policy()
    assert set(policy.allowed or ()) == set(weekly_research.TABLES.values())
    assert policy.is_visible("fmqt_bond_exec_part_data") is False


def test_week_label_must_be_two_valid_dates():
    assert weekly_research.validate_week(WEEK) == WEEK
    for bad in (
        "20260928",  # 缺后半段
        "20260928-",  # 后半段不全
        "2026092-20261004",  # 前半段 7 位
        "20261004-20260928",  # 起止颠倒
        "20261301-20261302",  # 无效月份
    ):
        with pytest.raises(ValueError):
            weekly_research.validate_week(bad)


def test_opinion_detail_sql_expands_json_views():
    """核心查询：序号表展开 $.views → 一行一个观点。

    面向 MySQL 5.7：**不能**出现 JSON_TABLE（8.0 专有，5.7 上直接语法报错），
    数组展开一律用序号表 + CONCAT 拼下标。
    """
    sql, params = weekly_research.opinion_details_sql(WEEK, limit=10)
    assert "JSON_TABLE" not in sql
    assert "$.views" in sql
    assert "institution" in sql
    assert params[0] == WEEK


def test_opinion_expansion_excludes_macro_categories():
    """观点矩阵只含资产类观点：宏观指标/宏观 两种拼写都要排除。"""
    sql, _params = weekly_research.opinion_details_sql(WEEK)
    assert "NOT IN ('宏观指标', '宏观')" in sql


def test_no_sql_uses_json_table():
    """全套 SQL 都不许用 JSON_TABLE —— 目标库是 MySQL 5.7。"""
    builders = [
        weekly_research.list_opinion_weeks_sql(),
        weekly_research.opinion_summary_sql(WEEK),
        weekly_research.opinion_details_sql(WEEK),
        weekly_research.opinion_direction_counts_sql(WEEK),
        weekly_research.asset_consensus_sql(WEEK),
        weekly_research.opinion_sources_sql(WEEK, "美元指数(DXY)"),
        weekly_research.asset_opinion_trend_sql("美元指数(DXY)"),
        weekly_research.market_overview_sql(WEEK),
        weekly_research.list_macro_weeks_sql(),
        weekly_research.macro_indicators_sql(WEEK),
        weekly_research.macro_forecasts_sql(WEEK),
        weekly_research.macro_indicator_trend_sql("美国CPI(同比)"),
        weekly_research.macro_coverage_sql(WEEK),
        weekly_research.macro_numeric_average_sql(WEEK),
        weekly_research.macro_text_mode_sql(WEEK, "美联储下次加息预期"),
    ]
    for sql, _params in builders:
        assert "JSON_TABLE" not in sql


def test_cross_week_blocks_drop_the_week_filter():
    """跨周版展开块必须换成 DISTINCT 物化、且不能再按 week 过滤。"""
    assert "WHERE week = %s" not in weekly_research.VIEWS_EXPAND_ALL_WEEKS
    assert "DISTINCT" in weekly_research.VIEWS_EXPAND_ALL_WEEKS
    assert "WHERE week = %s" not in weekly_research.MACRO_ITEMS_EXPAND_ALL_WEEKS
    assert "DISTINCT" in weekly_research.MACRO_ITEMS_EXPAND_ALL_WEEKS


def test_nullable_json_fields_are_nullif_wrapped():
    """JSON 里的 null 解出来是字符串 'null'，可空字段必须套 NULLIF。"""
    assert "NULLIF(" in weekly_research.VIEWS_EXPAND
    assert "NULLIF(" in weekly_research.MACRO_ITEMS_EXPAND
    assert "NULLIF(" in weekly_research.MACRO_BANK_EXPAND
