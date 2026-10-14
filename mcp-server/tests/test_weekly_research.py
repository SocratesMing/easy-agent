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
        "get_weekly_macro_opinions",
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
    """核心查询：JSON_TABLE 展开 $.views → 一行一个观点。"""
    sql, params = weekly_research.opinion_details_sql(WEEK, limit=10)
    assert "JSON_TABLE" in sql
    assert "$.views" in sql
    assert "jt.institution" in sql
    assert params[0] == WEEK


def test_macro_only_filter_uses_both_category_spellings():
    """「宏观」分页的口径是 category IN ('宏观指标','宏观')，两种拼写都要覆盖。"""
    sql, _params = weekly_research.macro_opinions_sql(WEEK)
    assert "op.category IN ('宏观指标', '宏观')" in sql
