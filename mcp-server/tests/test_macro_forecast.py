"""宏观预测场景测试：工具注册、表隔离与参数校验（不连库）。

拆包后每个业务只对自己的表可见，白名单隔离在这一层钉住。
"""

from __future__ import annotations

import pytest

import easy_mcp_server.businesses.macro_forecast.server as macro_server
from easy_mcp_server.businesses.macro_forecast import domestic_macro
from easy_mcp_server.businesses.macro_forecast.server import build


async def test_core_tools_registered():
    names = {tool.name for tool in await build().list_tools()}
    assert {
        "describe_domestic_macro_schema",
        "list_reports",
        "get_report_by_month",
        "get_report_meta",
        "get_overview_indicators",
        "get_report_summary",
        "get_indicators_by_group",
        "search_indicator_by_name",
        "get_indicator_snapshot",
        "get_model_predictions",
        "get_feature_contributions",
        "get_selected_features",
        "get_top_shap_features",
        "compare_indicator_months",
        "get_llm_analysis",
    } <= names


def test_policy_only_sees_own_tables(monkeypatch):
    monkeypatch.setenv("FORECAST_ALLOWED_TABLES", "")
    policy = macro_server._policy()
    assert set(policy.allowed or ()) == set(domestic_macro.TABLES.values())
    assert policy.is_visible("fmqt_bond_exec_part_data") is False


def test_forecast_month_must_be_yyyy_mm():
    assert domestic_macro.validate_month("2026-09") == "2026-09"
    for bad in ("202609", "2026/09", "2026-13", "2026-00"):
        with pytest.raises(ValueError):
            domestic_macro.validate_month(bad)


def test_id_must_be_positive():
    assert domestic_macro.validate_id("3") == 3
    assert domestic_macro.validate_id(3) == 3
    for bad in ("0", "-1", "abc"):
        with pytest.raises(ValueError):
            domestic_macro.validate_id(bad)


def test_groups_are_the_documented_enums():
    """板块枚举是模型按 group_cat 筛选的依据，改动需同步 SQL 清单。"""
    assert domestic_macro.GROUPS == ("增长", "通胀", "信用", "外贸", "其他")
    assert domestic_macro.SCENARIOS == ("SC_GLOBAL", "SC_FEATURE", "SC_INDICATOR")
