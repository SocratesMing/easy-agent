"""iData 场景测试：SQL 合并后的维度白名单、合计行隔离、单位换算与工具层行为。"""

from __future__ import annotations

import pytest

import easy_mcp_server.businesses.idata.server as idata_server
from easy_mcp_server import context
from easy_mcp_server.businesses.idata import idata
from easy_mcp_server.businesses.idata.server import build
from easy_mcp_server.businesses.weekly_research import weekly_research

LATEST = "20260828"
PREV = "20260827"


# ── 日期校验 ────────────────────────────────────────────────────────────


def test_validate_date_requires_yyyymmdd():
    assert idata.validate_date("20260828") == "20260828"
    for bad in ("2026-08-28", "2026082", "abcdefgh", ""):
        with pytest.raises(ValueError):
            idata.validate_date(bad)


def test_normalize_dates_dedupes_and_keeps_order():
    assert idata.normalize_dates(["20260828", "20260827", "20260828"]) == [
        "20260828",
        "20260827",
    ]


def test_normalize_dates_accepts_single_date():
    assert idata.normalize_dates(None, "20260828") == ["20260828"]


def test_normalize_dates_rejects_too_many_days():
    """一次传几百个日期会让 SQL 膨胀，提前拦住。"""
    with pytest.raises(ValueError):
        idata.normalize_dates([f"2026{1000 + i:04d}" for i in range(idata.MAX_DATES + 1)])


def test_validate_source_rejects_unknown():
    assert idata.validate_source("LENDING") == "lending"
    with pytest.raises(ValueError):
        idata.validate_source("nope")


# ── 交易日查询（合并了原来的 10 条 SQL） ────────────────────────────────


def test_trade_dates_default_is_latest_only():
    sql, params = idata.trade_dates_sql()
    assert "SELECT DISTINCT `STATISTIC_REPORT_RANGE` AS d" in sql
    assert sql.endswith("LIMIT 1")
    assert params == ()


def test_trade_dates_previous_day_uses_before():
    sql, params = idata.trade_dates_sql(before="20260828")
    assert "`STATISTIC_REPORT_RANGE` < %s" in sql
    assert params == ("20260828",)


def test_trade_dates_range_uses_between():
    sql, params = idata.trade_dates_sql(start="20260817", end="20260823", ascending=True)
    assert "BETWEEN %s AND %s" in sql
    assert "ORDER BY d ASC" in sql
    assert params == ("20260817", "20260823")


def test_trade_dates_source_switches_table_and_column():
    sql, _ = idata.trade_dates_sql(source="bond")
    assert "`fmqt_bond_trade_counterpart_analysis`" in sql
    assert "`TRANSACT_DATE`" in sql


def test_trade_dates_rejects_bad_date():
    with pytest.raises(ValueError):
        idata.trade_dates_sql(before="2026-08-28")


# ── 机构流向：维度白名单与合计行隔离 ────────────────────────────────────


def test_flow_requires_dates():
    with pytest.raises(ValueError):
        idata.institution_flow_sql(dates=[])


def test_flow_rejects_unknown_dimension_instead_of_silently_defaulting():
    """静默回退会让模型拿到错误维度的数据却以为是对的。"""
    with pytest.raises(ValueError) as exc:
        idata.institution_flow_sql(dates=[LATEST], group_by=["institution", "nope"])
    assert "group_by" in str(exc.value)


def test_flow_default_dimension_is_institution():
    sql, _ = idata.institution_flow_sql(dates=[LATEST])
    assert "`STATISTIC_DETAIL_NAME` AS institution" in sql
    assert "GROUP BY `STATISTIC_DETAIL_NAME`" in sql


def test_flow_include_total_uses_total_rows_only():
    sql, _ = idata.institution_flow_sql(dates=[LATEST], include_total=True)
    assert "`STATISTIC_SUBDETAIL_NAME` = '合计'" in sql
    assert "`RELATED_STATISTIC_SUBDETAIL_NAME` = '合计'" in sql
    assert "!= '合计'" not in sql


def test_flow_detail_mode_excludes_total_rows():
    """合计行与明细行混用会让金额翻倍，两种模式必须互斥。"""
    sql, _ = idata.institution_flow_sql(dates=[LATEST], include_total=False)
    assert "`STATISTIC_SUBDETAIL_NAME` != '合计'" in sql
    assert "`RELATED_STATISTIC_SUBDETAIL_NAME` != '合计'" in sql
    # 不做 "= '合计'" 的反向断言：它是 "!= '合计'" 的子串，会误判


def test_flow_bond_type_filter_is_fixed_expression():
    sql, params = idata.institution_flow_sql(dates=[LATEST], bond_type="国债")
    assert "`RELATED_STATISTIC_SUBDETAIL_NAME` IN ('国债-新债','国债-老债')" in sql
    assert "国债" not in params  # 常量表达式直接进 SQL，不占参数位


def test_flow_bond_type_rejects_unknown_value():
    with pytest.raises(ValueError):
        idata.institution_flow_sql(dates=[LATEST], bond_type="随便写")


def test_flow_bond_type_filter_and_detail_mode_do_not_conflict():
    """券种过滤自带 RELATED 条件时不再叠加 != '合计'，否则互相打架。"""
    sql, _ = idata.institution_flow_sql(
        dates=[LATEST], include_total=False, bond_type="全部"
    )
    assert sql.count("`RELATED_STATISTIC_SUBDETAIL_NAME`") == 1


def test_flow_bond_type_dimension_uses_case_expression():
    sql, _ = idata.institution_flow_sql(dates=[LATEST], group_by=["institution", "bond_type"])
    assert "THEN '国债'" in sql
    assert "AS bond_type" in sql
    assert "GROUP BY `STATISTIC_DETAIL_NAME`, CASE" in sql


def test_flow_multi_day_group_by_date():
    sql, params = idata.institution_flow_sql(
        dates=[PREV, LATEST], group_by=["date", "institution"]
    )
    assert "`STATISTIC_REPORT_RANGE` AS date" in sql
    assert "IN (%s, %s)" in sql
    assert params == (PREV, LATEST)


def test_flow_institution_filter_is_parameterised():
    sql, params = idata.institution_flow_sql(dates=[LATEST], institution="大型银行")
    assert "`STATISTIC_DETAIL_NAME` = %s" in sql
    assert params == (LATEST, "大型银行")


def test_flow_returns_buy_sell_net_in_yi():
    sql, _ = idata.institution_flow_sql(dates=[LATEST])
    assert "AS buy_yi" in sql and "AS sell_yi" in sql and "AS net_yi" in sql
    assert sql.count("/ 100000000") == 3


def test_term_stack_buckets_terms():
    sql, _ = idata.institution_term_stack_sql(dates=[LATEST], institution="大型银行")
    assert "ELSE '大于10年'" in sql
    assert "`STATISTIC_SUBDETAIL_NAME` != '合计'" in sql


# ── 个券机构 ────────────────────────────────────────────────────────────


def test_bond_list_can_filter_by_keyword():
    sql, params = idata.bond_list_sql(date=LATEST, keyword="国债")
    assert sql.count("LIKE %s") == 2
    assert params == (LATEST, "%国债%", "%国债%")


def test_bond_counterparts_returns_net_and_ratio_together():
    """原 2.5 与 2.8 是同一条 SQL 的两个字段，必须一次取回。"""
    sql, params = idata.bond_counterparts_sql(bond_id="260205", dates=[LATEST])
    assert "`BID_SIZE` AS net" in sql
    assert "`TRADE_QTY_RATIO` AS ratio" in sql
    assert "`RELATED_STATISTIC_SUBDETAIL_NAME` = '全部'" in sql
    assert params == ("260205", LATEST)


def test_bond_counterparts_requires_bond_id():
    with pytest.raises(ValueError):
        idata.bond_counterparts_sql(bond_id="  ", dates=[LATEST])


def test_top_active_bonds_sql_returns_raw_rows_for_ranking():
    sql, params = idata.top_active_bonds_sql(date=LATEST)
    assert "GROUP BY" not in sql  # 聚合在应用层做
    assert params == (LATEST,)


# ── 债券借贷口径 ────────────────────────────────────────────────────────


def test_lending_balance_convention_is_hardcoded():
    """借贷余额必须是 类型19 + 方向1，参数无法放松——口径错了结论就全错。"""
    sql, _ = idata.lending_snapshot_sql(date=LATEST)
    assert "`STATISTIC_AMT_TYPE` = '19'" in sql
    assert "`STATISTIC_AMT_SIDE` = '1'" in sql


def test_lending_concentration_is_aggregated_with_max():
    sql, _ = idata.lending_snapshot_sql(date=LATEST)
    assert "MAX(CAST(`BOND_CONCENTRATION` AS DECIMAL(20,6)))" in sql


def test_lending_trend_scopes_to_codes_when_given():
    sql, params = idata.lending_trend_sql(dates=[LATEST], codes=["260205", "260206"])
    assert "`STATISTIC_DETAIL_ID` IN (%s, %s)" in sql
    assert params == (LATEST, "260205", "260206")


def test_lending_trend_without_codes_covers_all():
    sql, params = idata.lending_trend_sql(dates=[LATEST])
    assert "`STATISTIC_DETAIL_ID` IN" not in sql
    assert params == (LATEST,)


def test_lending_bond_days_counts_distinct_dates():
    sql, _ = idata.lending_bond_days_sql()
    assert "COUNT(DISTINCT `TRANSACT_DATE`) AS days" in sql


# ── 大模型点评 ──────────────────────────────────────────────────────────


def test_llm_comment_requires_known_scene():
    sql, params = idata.llm_comment_sql(scene="institution_daily")
    assert "`STATUS` = 'success'" in sql
    assert params == ("institution_daily",)
    with pytest.raises(ValueError):
        idata.llm_comment_sql(scene="whatever")


def test_llm_comment_can_pin_a_date():
    _, params = idata.llm_comment_sql(scene="bond_lending", date=LATEST)
    assert params == ("bond_lending", LATEST)


# ── 结果整形（单位与口径归一） ──────────────────────────────────────────


def test_to_yi_converts_and_handles_none():
    assert idata.to_yi(123456789) == 1.23
    assert idata.to_yi(None) is None
    assert idata.to_yi("abc") is None


def test_to_float_tolerates_null_concentration():
    """BOND_CONCENTRATION 实测有 NULL，直接 float() 会抛异常。"""
    assert idata.to_float(None) == 0.0
    assert idata.to_float("24.53") == 24.53
    assert idata.to_float("x", default=-1.0) == -1.0


def test_normalize_bond_type_merges_new_and_old():
    assert idata.normalize_bond_type("国债-新债") == "国债"
    assert idata.normalize_bond_type("国债-老债") == "国债"
    assert idata.normalize_bond_type("中期票据") == "中票"
    assert idata.normalize_bond_type("合计") == "合计"
    assert idata.normalize_bond_type("别的") == "其他"


def test_decorate_bond_rows_converts_net_to_yi():
    rows = [{"institution": "A", "net": 123000000, "ratio": "24.53"}]
    item = idata.decorate_bond_rows(rows)[0]
    assert item["net_yi"] == 1.23
    assert item["ratio"] == 24.53
    assert "net" not in item


def test_decorate_lending_rows_null_concentration_becomes_zero():
    rows = [{"bond_id": "260205", "balance": 500000000, "concentration": None}]
    item = idata.decorate_lending_rows(rows)[0]
    assert item["balance_yi"] == 5.0
    assert item["concentration"] == 0.0


def test_pick_extremes_finds_buyer_and_seller():
    rows = [{"institution": "A", "net_yi": 2.0}, {"institution": "B", "net_yi": -3.0}]
    buyer, seller = idata.pick_extremes(rows)
    assert buyer["institution"] == "A"
    assert seller["institution"] == "B"


def test_rank_active_bonds_sorts_by_absolute_flow():
    rows = [
        {"bond_id": "1", "bond_name": "一", "institution": "A", "net": 100000000},
        {"bond_id": "1", "bond_name": "一", "institution": "B", "net": -300000000},
        {"bond_id": "2", "bond_name": "二", "institution": "A", "net": 50000000},
    ]
    ranked = idata.rank_active_bonds(rows, top=8)
    assert [b["bond_id"] for b in ranked] == ["1", "2"]
    assert ranked[0]["activity_yi"] == 4.0
    assert ranked[0]["top_buyer"]["institution"] == "A"
    assert ranked[0]["top_seller"]["institution"] == "B"


def test_with_change_computes_delta_against_previous_day():
    today = [{"bond_id": "1", "balance_yi": 10.0}]
    prev = [{"bond_id": "1", "balance_yi": 8.0}]
    item = idata.with_change(today, prev)[0]
    assert item["change_yi"] == 2.0
    assert item["prev_yi"] == 8.0


def test_with_change_treats_missing_prev_as_zero():
    item = idata.with_change([{"bond_id": "9", "balance_yi": 3.0}], [])[0]
    assert item["change_yi"] == 3.0


def test_filter_core_bonds_keeps_continuously_tracked():
    snapshot = [
        {"bond_id": "1", "balance_yi": 10.0},
        {"bond_id": "2", "balance_yi": 20.0},
        {"bond_id": "3", "balance_yi": 99.0},
    ]
    days = [{"bond_id": "1", "days": 5}, {"bond_id": "2", "days": 5}, {"bond_id": "3", "days": 2}]
    core = idata.filter_core_bonds(snapshot, days, top=7)
    assert [c["bond_id"] for c in core] == ["2", "1"]  # 3 不连续，被排除


# ── 表引用与配置 ────────────────────────────────────────────────────────


def test_table_ref_default_has_no_schema_prefix():
    assert idata.table_ref("daily") == "`fmqt_bond_exec_part_data`"


def test_table_ref_supports_cross_database(monkeypatch):
    monkeypatch.setenv("IDATA_SCHEMA", "idata")
    assert idata.table_ref("lending") == "`idata`.`fmqt_bond_lending_analysis`"


def test_table_ref_rejects_injection(monkeypatch):
    monkeypatch.setenv("IDATA_SCHEMA", "idata; DROP TABLE t")
    with pytest.raises(ValueError):
        idata.table_ref("daily")


def test_schema_help_documents_units_and_pitfalls():
    help_info = idata.schema_help()
    assert "亿元" in help_info["units"]["金额"]
    assert help_info["enums"]["group_by"] == list(idata.DIMENSIONS)
    assert any("翻倍" in p for p in help_info["pitfalls"])


# ── 探索可见性（缺省收敛到四张业务表） ──────────────────────────────────


def test_policy_defaults_to_idata_tables_only(monkeypatch):
    """缺省收敛到本场景的四张表，且**看不到其它场景的表**。

    拆成独立业务包后，每个包只管自己的表——这条同时验证了"隔离到位"。
    """
    monkeypatch.setenv("IDATA_ALLOWED_TABLES", "")
    policy = idata_server._policy()
    expected = set(idata.TABLES.values())
    assert set(policy.allowed or ()) == expected
    for table in expected:
        assert policy.is_visible(table) is True
    # 别的场景的表必须看不见
    assert policy.is_visible("fmqt_forecast_report_meta") is False
    assert policy.is_visible("fmqt_predictions_rates") is False
    assert policy.is_visible("weekly_analyzer_data") is False


def test_weekly_tables_match_business_schema():
    """表名钉住。

    新版 SQL 清单（2026-10 修订）把表名从 `weekly_analyzer_data` 改成了 `weekly_data`；
    这类错误只会在运行时才报 "Table ... doesn't exist"，用测试提前挡一道。
    若内网库仍叫旧名，把这里和领域层 TABLES 一起改回 `weekly_analyzer_data`。
    """
    assert weekly_research.TABLES == {
        "opinions": "weekly_data",
        "macro": "weekly_macro_data",
    }


def test_policy_can_be_overridden_by_env(monkeypatch):
    monkeypatch.setenv("IDATA_ALLOWED_TABLES", "fmqt_bond_exec_part_data,extra_table")
    policy = idata_server._policy()
    assert policy.is_visible("extra_table") is True
    assert policy.is_visible("fmqt_bond_lending_analysis") is False


def test_schema_help_lists_visible_tables():
    assert idata.schema_help()["visible_tables"] == list(idata.TABLES.values())


# ── 工具层 ──────────────────────────────────────────────────────────────


@pytest.fixture
def captured(monkeypatch):
    """拦截数据库调用：日期查询返回固定最新交易日，其余返回空。"""
    calls: list[tuple[str, tuple]] = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        if "AS d " in sql or sql.rstrip().endswith("AS d"):
            return [{"d": LATEST}]
        return []

    monkeypatch.setattr(idata_server, "readonly_query", fake_query)
    return calls


async def _tool_names(mcp) -> set[str]:
    return {tool.name for tool in await mcp.list_tools()}


async def _call(mcp, name: str, **kwargs):
    context.set_identity("tester", "idata")
    try:
        result = await mcp.call_tool(name, kwargs)
    finally:
        context.clear_identity()
    if isinstance(result, tuple):
        content, structured = result
        if structured is not None:
            return structured
        import json

        return json.loads(content[0].text)
    return result


async def test_core_idata_tools_registered(captured):
    """iData 看板的 10 个工具必须都在。

    刻意断言"包含"而不是"相等"：同一业务下还会挂其它场景（宏观预测、10年期国债趋势判断、
    研报观点），用相等断言会导致每加一个场景就得改这个文件。
    """
    names = await _tool_names(build())
    assert {
        "describe_idata_schema",
        "get_trade_dates",
        "get_institution_flow",
        "list_bonds",
        "get_bond_counterparts",
        "get_bond_trend",
        "list_top_active_bonds",
        "get_lending_snapshot",
        "get_lending_trend",
        "get_llm_comment",
    } <= names


async def test_omitted_date_falls_back_to_latest(captured):
    """所有工具统一：date 留空 → 先查最新交易日，再执行主查询。"""
    result = await _call(build(), "get_institution_flow")
    assert result["ok"] is True
    assert result["dates"] == [LATEST]
    assert len(captured) == 2  # 日期查询 + 主查询
    assert "AS d" in captured[0][0]


async def test_explicit_date_skips_latest_lookup(captured):
    result = await _call(build(), "get_institution_flow", date=PREV)
    assert result["dates"] == [PREV]
    assert len(captured) == 1  # 不需要再查最新日期


async def test_flow_reports_unit_in_yi(captured):
    result = await _call(build(), "get_institution_flow", date=LATEST)
    assert result["unit"] == "亿元"


async def test_bad_date_returns_ok_false(captured):
    result = await _call(build(), "get_institution_flow", date="2026-08-28")
    assert result["ok"] is False
    assert "YYYYMMDD" in result["error"]


async def test_unknown_dimension_returns_ok_false(captured):
    result = await _call(build(), "get_institution_flow", date=LATEST, group_by=["nope"])
    assert result["ok"] is False
    assert "group_by" in result["error"]


async def test_bond_counterparts_without_data_reports_empty(captured):
    result = await _call(build(), "get_bond_counterparts", bond_id="260205")
    assert result["ok"] is True
    assert result["row_count"] == 0
    assert result["top_buyer"] is None and result["top_seller"] is None


async def test_top_active_bonds_reports_ranking(captured):
    result = await _call(build(), "list_top_active_bonds")
    assert result["ok"] is True
    assert result["bonds"] == []


async def test_lending_snapshot_without_change_skips_prev_lookup(captured):
    result = await _call(build(), "get_lending_snapshot", date=LATEST, include_change=False)
    assert result["ok"] is True
    assert result["changes"] is None
    assert len(captured) == 1


async def test_lending_snapshot_with_change_queries_prev_day(captured):
    result = await _call(build(), "get_lending_snapshot", date=LATEST, include_change=True)
    assert result["ok"] is True
    # 当日快照 + 上一交易日查询 + 上一日快照
    assert len(captured) == 3


async def test_llm_comment_reports_miss_with_hint(captured):
    result = await _call(build(), "get_llm_comment", scene="bond_lending")
    assert result["ok"] is False
    assert "点评" in result["error"]


async def test_describe_schema_needs_no_db(captured):
    result = await _call(build(), "describe_idata_schema")
    assert result["ok"] is True
    assert captured == []
