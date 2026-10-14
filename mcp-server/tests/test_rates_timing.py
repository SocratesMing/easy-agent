"""利率择时场景测试：工具注册、表隔离与日期口径（不连库）。

注意本场景日期是 ``YYYY-MM-DD``（表列类型为 DATE），与 iData 的 YYYYMMDD 不同。
"""

from __future__ import annotations

import pytest

import easy_mcp_server.businesses.rates_timing.server as rates_server
from easy_mcp_server.businesses.rates_timing import rates_timing
from easy_mcp_server.businesses.rates_timing.server import build


async def test_core_tools_registered():
    names = {tool.name for tool in await build().list_tools()}
    assert {
        "describe_rates_timing_schema",
        "get_rates_latest_signal_date",
        "get_rates_previous_signal_date",
        "get_rates_recent_signal_dates",
        "get_rates_prediction",
        "list_rates_predictions",
        "get_rates_run_status",
        "get_rates_run_audit",
        "get_rates_selected_factors",
        "get_rates_backtest_metrics",
        "get_rates_latest_explanation",
        "get_rates_latest_factor_evidence",
    } <= names


def test_policy_only_sees_own_tables(monkeypatch):
    monkeypatch.setenv("RATES_ALLOWED_TABLES", "")
    policy = rates_server._policy()
    assert set(policy.allowed or ()) == set(rates_timing.TABLES.values())
    assert policy.is_visible("fmqt_bond_exec_part_data") is False


def test_signal_date_is_iso_not_yyyymmdd():
    assert rates_timing.validate_date("2026-09-28") == "2026-09-28"
    for bad in ("20260928", "2026/09/28", "2026-9-28"):
        with pytest.raises(ValueError):
            rates_timing.validate_date(bad)


def test_run_id_must_be_positive():
    assert rates_timing.validate_run_id("2") == 2
    for bad in ("0", "-1", "abc"):
        with pytest.raises(ValueError):
            rates_timing.validate_run_id(bad)


def test_latest_signal_date_sql_is_a_single_aggregate():
    sql, params = rates_timing.latest_signal_date_sql()
    assert "MAX(`signal_date`)" in sql
    assert params == ()
