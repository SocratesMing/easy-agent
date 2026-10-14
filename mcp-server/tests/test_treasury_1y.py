"""1 年期国债模型场景测试：JSON 解码、原值保留与工具契约（不连库）。

这个场景的风险点和前面几个不一样——结果全在一个 JSON 字段里，所以重点盯：

- ``result_json`` 的解码（驱动返回 JSON 文本，工具必须还原成对象）
- **原值保留**：``yield_pct`` 是百分数原值，不能被当成 0—1 再乘 100；
  ``yield_change_2d_bp`` 为 null 时不能被改成 0
- 无数据是 ``found=false``，不是错误
- 历史列表只给摘要，不带每期长解释
"""

from __future__ import annotations

import json

import pytest

import easy_mcp_server.businesses.treasury_1y.server as treasury_server
from easy_mcp_server import context
from easy_mcp_server.businesses.treasury_1y import treasury_1y
from easy_mcp_server.businesses.treasury_1y.server import build

LATEST = "2026-10-10"
TABLE = "fmqt_treasury_1y_history"

PAYLOAD = {
    "signal_date": LATEST,
    "strategy_id": "curve_funding_7d_mtht_v3",
    "yield_pct": 1.2147,
    "yield_percentile_756": 0.62,
    "prob_up_7d": 0.6058,
    "prob_down_7d": 0.3942,
    "trade_threshold": 0.65,
    "signal_blocked": False,
    "funding_signal": 1,
    "mt_signal": 0,
    "ht_signal": -1,
    "technical_signal": 0,
    "yield_change_2d_bp": None,
    "funding_advice": "资金面偏松，可适度加仓",
    "technical_advice": "技术面中性",
    "funding_explanation": "资金利率下行，流动性宽松。",
    "technical_explanation": "均线多头排列。",
    "funding_source": "资金面工作簿",
    "technical_source": "技术面工作簿",
}


@pytest.fixture
def captured(monkeypatch):
    """拦截数据库调用：返回一行伪造结果（result_json 用 JSON 文本，贴近真实驱动）。"""
    calls: list[tuple[str, tuple]] = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        if "COUNT(*) AS matched_count" in sql:
            return [{"matched_count": 7}]
        if "COUNT(*)" in sql:
            return [{"total_count": 35, "first_date": "2026-09-06", "last_date": LATEST}]
        if "`result_json`" in sql and "`signal_date` = %s" not in sql:
            return [
                {
                    "signal_date": LATEST,
                    "result_json": json.dumps(PAYLOAD, ensure_ascii=False),
                    "created_at": "2026-10-10 08:00:00",
                    "updated_at": "2026-10-10 08:00:00",
                }
            ]
        return []

    monkeypatch.setattr(treasury_server, "readonly_query", fake_query)
    return calls


@pytest.fixture
def empty(monkeypatch):
    """拦截数据库调用：任何查询都返回空（模拟表里没有该日期的数据）。"""
    def fake_query(sql, params=()):
        if "COUNT(*) AS matched_count" in sql:
            return [{"matched_count": 0}]
        if "COUNT(*)" in sql:
            return [{"total_count": 0, "first_date": None, "last_date": None}]
        return []

    monkeypatch.setattr(treasury_server, "readonly_query", fake_query)
    return []


async def _tool_names(mcp) -> set[str]:
    return {tool.name for tool in await mcp.list_tools()}


async def _call(mcp, name: str, **kwargs):
    context.set_identity("tester", "treasury_1y")
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


# ── 工具注册与隔离 ──────────────────────────────────────────────────────


async def test_all_tools_registered():
    names = await _tool_names(build())
    assert len(names) == 8
    assert {
        "describe_treasury_1y_schema",
        "get_treasury_1y_data_range",
        "get_treasury_1y_previous_date",
        "list_treasury_1y_dates",
        "get_treasury_1y_result",
        "list_treasury_1y_history",
        "get_treasury_1y_signal_summary",
        "get_treasury_1y_explanation",
    } <= names


def test_policy_only_sees_own_table(monkeypatch):
    monkeypatch.setenv("TREASURY_1Y_ALLOWED_TABLES", "")
    policy = treasury_server._policy()
    assert set(policy.allowed or ()) == {TABLE}
    assert policy.is_visible("fmqt_gold_analysis_result") is False


# ── JSON 解码 ───────────────────────────────────────────────────────────


def test_decode_result_handles_text_object_and_garbage():
    assert treasury_1y.decode_result(json.dumps(PAYLOAD)) == PAYLOAD
    assert treasury_1y.decode_result(PAYLOAD) == PAYLOAD
    assert treasury_1y.decode_result(None) == {}
    assert treasury_1y.decode_result("not json") == {}
    assert treasury_1y.decode_result("[1,2]") == {}  # 非对象
    assert treasury_1y.decode_result(b'{"a": 1}') == {"a": 1}


def test_normalize_blocked_handles_bool_and_numbers():
    """signal_blocked 可能是 0/1 也可能是 false/true，不能把 "false" 当数值真。"""
    assert treasury_1y.normalize_blocked(True) is True
    assert treasury_1y.normalize_blocked(1) is True
    assert treasury_1y.normalize_blocked(0) is False
    assert treasury_1y.normalize_blocked("false") is False
    assert treasury_1y.normalize_blocked("true") is True
    assert treasury_1y.normalize_blocked(None) is None


# ── 原值保留 ────────────────────────────────────────────────────────────


def test_summarize_keeps_nulls_and_drops_long_text():
    """摘要保留 null（不当 0），并剔掉长解释与来源文本。"""
    summary = treasury_1y.summarize(PAYLOAD)
    assert summary["yield_change_2d_bp"] is None
    assert summary["signal_blocked"] is False
    assert summary["yield_pct"] == 1.2147
    for field in treasury_1y.LONG_TEXT_FIELDS:
        assert field not in summary


def test_display_values_never_scales_yield_pct():
    """yield_pct 已是百分数原值——只有 0—1 的那几个字段才换算成百分数。"""
    out = treasury_1y.display_values({"yield_pct": 1.2147, "prob_up_7d": 0.6058})
    assert "yield_pct_pct" not in out
    assert out["prob_up_7d_pct"] == 60.58


# ── 参数校验 ────────────────────────────────────────────────────────────


def test_date_must_be_yyyy_mm_dd():
    assert treasury_1y.validate_date("2026-10-10") == "2026-10-10"
    assert treasury_1y.validate_date(None) is None
    with pytest.raises(ValueError):
        treasury_1y.validate_date("20261010")


def test_required_date_rejects_blank():
    assert treasury_1y.validate_required_date("2026-10-10") == "2026-10-10"
    with pytest.raises(ValueError):
        treasury_1y.validate_required_date("")


def test_limit_defaults_and_ceiling():
    assert treasury_1y.bounded_limit(None, treasury_1y.DEFAULT_DATES_LIMIT) == 20
    assert treasury_1y.bounded_limit(None, treasury_1y.DEFAULT_HISTORY_LIMIT) == 30
    assert treasury_1y.bounded_limit(999) == treasury_1y.MAX_LIMIT
    assert treasury_1y.bounded_limit(-1) == 1


def test_history_range_rejects_reversed_bounds():
    with pytest.raises(ValueError):
        treasury_1y.history_sql("2026-10-10", "2026-09-01")
    sql, params = treasury_1y.history_sql("2026-09-01", "2026-10-10", 5)
    assert params == ("2026-09-01", "2026-10-10")
    assert "LIMIT 5" in sql


# ── 工具层 ──────────────────────────────────────────────────────────────


async def test_result_decodes_json_and_reports_found(captured):
    result = await _call(build(), "get_treasury_1y_result")
    assert result["found"] is True
    assert result["ok"] is True
    data = result["data"]
    assert data["signal_date"] == LATEST
    assert isinstance(data["result_json"], dict)
    assert data["result_json"]["yield_pct"] == 1.2147
    assert result["units"]["yield_pct"] == "percent"


async def test_missing_date_returns_found_false_not_error(empty):
    """指定日无数据是 found=false，不是错误，也不回退到其它日期。"""
    result = await _call(build(), "get_treasury_1y_result", date="2020-01-01")
    assert result["found"] is False
    assert result["data"] is None
    assert result.get("ok") is not False


async def test_explicit_date_uses_equality_query(captured):
    await _call(build(), "get_treasury_1y_result", date="2026-10-09")
    assert captured[0][1] == ("2026-10-09",)
    assert "`signal_date` = %s" in captured[0][0]


async def test_signal_summary_normalizes_blocked(captured):
    result = await _call(build(), "get_treasury_1y_signal_summary")
    assert result["found"] is True
    assert result["data"]["signal_blocked"] is False
    assert result["data"]["funding_signal"] == 1
    assert result["data"]["yield_change_2d_bp"] is None


async def test_history_reports_both_counts(captured):
    result = await _call(build(), "list_treasury_1y_history")
    assert result["ok"] is True
    assert result["matched_count"] == 7
    assert result["returned_count"] >= 1
    item = result["items"][0]
    assert "funding_explanation" not in item["result_json"]
    assert "yield_pct" in item["result_json"]


async def test_history_rejects_bad_range(captured):
    result = await _call(
        build(), "list_treasury_1y_history", start_date="2026-10-10", end_date="2026-09-01"
    )
    assert result["ok"] is False
    assert "start_date" in result["error"]


async def test_explanation_returns_saved_text_only(captured):
    result = await _call(build(), "get_treasury_1y_explanation")
    assert result["found"] is True
    assert result["data"]["funding_advice"] == "资金面偏松，可适度加仓"
    assert result["data"]["funding_source"] == "资金面工作簿"


async def test_previous_date_requires_date(captured):
    result = await _call(build(), "get_treasury_1y_previous_date", date="")
    assert result["ok"] is False


async def test_schema_tool_documents_units(captured):
    result = await _call(build(), "describe_treasury_1y_schema")
    assert result["ok"] is True
    assert result["units"]["yield_pct"] == "percent"
    assert "收益率上行" in result["signal_scale"]
    assert "收益率下行" in result["signal_scale"]
