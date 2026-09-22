"""外汇 K 线问数测试：大表保护（时间窗收敛）、SQL 构造、时间解析与工具层行为。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest

import easy_mcp_server.businesses.fxbar.server as fx_server
from easy_mcp_server import context
from easy_mcp_server.businesses.fxbar import fxbar
from easy_mcp_server.businesses.fxbar.server import build

DAY_MS = 86_400_000


def _ms(text: str) -> int:
    """`YYYY-MM-DD HH:MM:SS`（UTC）→ 毫秒时间戳。"""
    parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


# 固定基准时刻（UTC），避免用例依赖真实时间
NOW_MS = _ms("2026-09-21 12:00:00")


# ── 时间解析 ────────────────────────────────────────────────────────────


def test_parse_time_accepts_date_only():
    assert fxbar.parse_time("2026-10-01") == _ms("2026-10-01 00:00:00")


def test_parse_time_accepts_datetime():
    assert fxbar.parse_time("2026-10-01 08:30:00") == _ms("2026-10-01 08:30:00")


def test_parse_time_accepts_iso_variants():
    """ISO 的 T 分隔与末尾 Z 都要能吃下（模型很常这么写）。"""
    assert fxbar.parse_time("2026-10-01T08:30:00") == _ms("2026-10-01 08:30:00")
    assert fxbar.parse_time("2026-10-01T08:30:00Z") == _ms("2026-10-01 08:30:00")


def test_parse_time_rejects_garbage():
    with pytest.raises(ValueError):
        fxbar.parse_time("上个月")


def test_parse_time_is_utc_not_local():
    """按 UTC 解释——否则同一句话在北京和伦敦会查出不同结果。"""
    assert fxbar.parse_time("2026-10-01 00:00:00") == 1_790_812_800_000


# ── 时间窗收敛（大表保护的核心） ───────────────────────────────────────


def test_default_window_uses_configured_hours():
    window = fxbar.resolve_window(now=NOW_MS)
    assert window.end_ms == NOW_MS
    assert window.end_ms - window.start_ms == fxbar.default_window_hours() * 3_600_000
    assert not window.clamped
    assert not window.explicit


def test_days_argument_sets_relative_window():
    window = fxbar.resolve_window(days=3, now=NOW_MS)
    assert window.end_ms - window.start_ms == 3 * DAY_MS


def test_explicit_range_is_respected():
    window = fxbar.resolve_window(
        start_time="2026-09-01", end_time="2026-09-03 12:00:00", now=NOW_MS
    )
    assert window.start_ms == _ms("2026-09-01 00:00:00")
    assert window.end_ms == _ms("2026-09-03 12:00:00")
    assert window.explicit


def test_explicit_start_without_end_uses_now():
    window = fxbar.resolve_window(start_time="2026-09-09", now=NOW_MS)
    assert window.start_ms == _ms("2026-09-09 00:00:00")
    assert window.end_ms == NOW_MS


def test_range_over_limit_is_clamped_from_the_end():
    """超上限时从结束时间往前收敛（结束时间通常是用户真正关心的边界）。"""
    window = fxbar.resolve_window(
        start_time="2020-01-01", end_time="2026-09-21 12:00:00", now=NOW_MS
    )
    assert window.clamped
    assert window.end_ms == _ms("2026-09-21 12:00:00")
    assert window.end_ms - window.start_ms == fxbar.max_window_days() * DAY_MS


def test_clamped_flag_is_reported_to_the_model():
    window = fxbar.resolve_window(days=365, now=NOW_MS)
    assert window.clamped
    assert window.to_dict()["clamped"] is True
    assert window.to_dict()["days"] == fxbar.max_window_days()


def test_reversed_range_is_rejected():
    with pytest.raises(ValueError):
        fxbar.resolve_window(start_time="2026-09-21", end_time="2026-09-01", now=NOW_MS)


def test_window_never_collapses_to_zero():
    window = fxbar.resolve_window(hours=1, now=NOW_MS)
    assert window.end_ms > window.start_ms


# ── 枚举与渠道 ──────────────────────────────────────────────────────────


def test_frequency_whitelist():
    assert fxbar.normalize_frequency("1d") == "1D"
    assert fxbar.normalize_frequency(None) == fxbar.default_frequency()
    with pytest.raises(ValueError):
        fxbar.normalize_frequency("2H")


def test_default_channels_are_best_and_fxall():
    assert fxbar.resolve_channels(None) == ("BEST", "FXALL")
    assert fxbar.default_channels() == ("BEST", "FXALL")


def test_channel_all_disables_filter():
    assert fxbar.resolve_channels("ALL") == ()
    assert fxbar.resolve_channels("*") == ()


def test_explicit_channel_is_uppercased():
    assert fxbar.resolve_channels("ebs") == ("EBS",)


# ── SQL 构造：必须收敛到"合约 + 频率 + 时间窗 + LIMIT" ─────────────────


def test_bars_sql_requires_contract_and_frequency():
    with pytest.raises(ValueError):
        fxbar.bars_sql(contract_code="  ")
    with pytest.raises(ValueError):
        fxbar.bars_sql(contract_code="EURUSD", frequency="2H")


def test_bars_sql_is_fully_scoped():
    window = fxbar.resolve_window(days=1, now=NOW_MS)
    sql, params = fxbar.bars_sql(contract_code="EURUSD", frequency="1H", window=window)

    assert "`CONTRACT_CODE` = %s" in sql
    assert "`FREQUENCY` = %s" in sql
    assert "`TIME` >= %s AND `TIME` <= %s" in sql
    assert "`CHANNEL` IN (%s, %s)" in sql
    assert sql.endswith("LIMIT 500")
    assert "ORDER BY `TIME` DESC" in sql
    assert params == ("EURUSD", "1H", window.start_ms, window.end_ms, "BEST", "FXALL")


def test_bars_sql_never_inlines_user_input():
    """合约代码走参数化，不拼进 SQL 文本。"""
    window = fxbar.resolve_window(days=1, now=NOW_MS)
    sql, params = fxbar.bars_sql(
        contract_code="EURUSD' OR 1=1 --", frequency="1H", window=window
    )
    assert "OR 1=1" not in sql
    assert "EURUSD' OR 1=1 --" in params


def test_channel_all_removes_channel_condition():
    window = fxbar.resolve_window(days=1, now=NOW_MS)
    sql, params = fxbar.bars_sql(
        contract_code="EURUSD", frequency="1H", window=window, channel="ALL"
    )
    # 注意 SELECT 列表里本来就有 `CHANNEL` 列，所以只断言 WHERE 侧没有渠道过滤
    assert "`CHANNEL` IN" not in sql
    assert "BEST" not in params


def test_bars_sql_optional_filters_are_parameterised():
    window = fxbar.resolve_window(days=1, now=NOW_MS)
    sql, params = fxbar.bars_sql(
        contract_code="EURUSD",
        frequency="1H",
        window=window,
        side="bid",
        data_type="ohlc",
        instrument="spot",
    )
    assert sql.count("`SIDE` = %s") == 1
    assert "`DATA_TYPE` = %s" in sql
    assert "`INSTRUMENT` = %s" in sql
    assert params[-3:] == ("BID", "OHLC", "SPOT")


def test_bars_sql_limit_is_capped_by_querykit():
    """limit 再大也不能突破 QUERYKIT_MAX_ROWS。"""
    window = fxbar.resolve_window(days=1, now=NOW_MS)
    sql, _ = fxbar.bars_sql(
        contract_code="EURUSD", frequency="1H", window=window, limit=10_000
    )
    assert f"LIMIT {fxbar.max_rows()}" in sql


# ── 最新报价 / 合约清单 ────────────────────────────────────────────────


def test_latest_sql_keeps_a_lookback_window():
    """不写成无界 ORDER BY——主键是拼接串，全表扫的代价不可接受。"""
    sql, params = fxbar.latest_sql(contract_code="EURUSD", frequency="1H")
    assert "`TIME` >= %s" in sql
    assert sql.endswith("LIMIT 1")
    # 合约 + 频率 + 起止时间 + 两个默认渠道
    assert len(params) == 6


def test_latest_sql_lookback_is_configurable():
    _, params_default = fxbar.latest_sql(contract_code="EURUSD")
    _, params_wide = fxbar.latest_sql(contract_code="EURUSD", lookback_days=30)
    assert params_wide[2] < params_default[2]  # 起始时间更早


def test_instruments_sql_groups_and_limits():
    sql, params = fxbar.instruments_sql(days=7)
    assert "GROUP BY `CONTRACT_CODE`, `INSTRUMENT`, `CHANNEL`" in sql
    assert "COUNT(*) AS bar_count" in sql
    assert "`TIME` >= %s" in sql  # 大表上不能全历史 GROUP BY
    assert sql.endswith("LIMIT 200")
    assert isinstance(params[0], int) and params[0] > 0


# ── 表引用与可见性 ─────────────────────────────────────────────────────


def test_table_ref_default_has_no_schema_prefix():
    assert fxbar.table_ref() == "`HSDC_HDS2_BAR_FX`"


def test_table_ref_supports_cross_database(monkeypatch):
    """表在别的库时用 FX_SCHEMA 指定，不用改代码。"""
    monkeypatch.setenv("FX_SCHEMA", "hsdc")
    assert fxbar.table_ref() == "`hsdc`.`HSDC_HDS2_BAR_FX`"


def test_table_ref_rejects_identifier_injection(monkeypatch):
    monkeypatch.setenv("FX_SCHEMA", "hsdc; DROP TABLE t")
    with pytest.raises(ValueError):
        fxbar.table_ref()


# ── 结果整形 ────────────────────────────────────────────────────────────


def test_decorate_converts_millis_and_decimals():
    rows = [
        {
            "TIME": _ms("2026-10-01 08:00:00"),
            "START_TIMESTAMP": _ms("2026-10-01 08:00:00"),
            "END_TIMESTAMP": _ms("2026-10-01 09:00:00"),
            "CONTRACT_CODE": "EURUSD",
            "CHANNEL": "BEST",
            "INSTRUMENT": "SPOT",
            "DATA_TYPE": "OHLC",
            "FREQUENCY": "1H",
            "SIDE": "BID",
            "ENTITY": "E01",
            "OPEN": Decimal("1.1700000000"),
            "HIGH": Decimal("1.1750000000"),
            "LOW": Decimal("1.1650000000"),
            "CLOSE": Decimal("1.1720000000"),
            "TRADE_VOLUME": 100,
            "TRADE_AMT": 117,
        }
    ]
    item = fxbar.decorate(rows)[0]

    assert item["time_text"] == "2026-10-01 08:00:00"
    assert item["end_time_text"] == "2026-10-01 09:00:00"
    assert item["contract_code"] == "EURUSD"
    assert isinstance(item["close"], float)  # Decimal 已转 JSON 可序列化
    assert item["trade_volume"] == 100
    # 冗余的原始时间戳列不再返回
    assert "TIME" not in item and "START_TIMESTAMP" not in item


def test_summarize_bars_reports_empty_with_hint():
    window = fxbar.resolve_window(days=1, now=NOW_MS)
    text = fxbar.summarize_bars([], "EURUSD", "1H", window)
    assert "没有数据" in text
    assert "list_fx_instruments" in text  # 给出下一步排查路径


def test_summarize_bars_reports_range():
    items = [
        {"time_text": "2026-10-01 09:00:00", "high": 1.18, "low": 1.17},
        {"time_text": "2026-10-01 08:00:00", "high": 1.19, "low": 1.16},
    ]
    window = fxbar.resolve_window(days=1, now=NOW_MS)
    text = fxbar.summarize_bars(items, "EURUSD", "1H", window)
    assert "2 根 K 线" in text
    assert "1.19" in text and "1.16" in text


def test_schema_help_documents_window_rules():
    help_info = fxbar.schema_help()
    assert help_info["table"] == "`HSDC_HDS2_BAR_FX`"
    assert help_info["enums"]["frequency"] == list(fxbar.FREQUENCIES)
    assert help_info["enums"]["default_channels"] == ["BEST", "FXALL"]
    assert help_info["time"]["max_window_days"] == fxbar.max_window_days()
    assert any("大表" in note for note in help_info["performance"])


# ── 工具层 ──────────────────────────────────────────────────────────────


@pytest.fixture
def captured(monkeypatch):
    """拦截工具层的数据库调用，记录生成的 SQL 与参数。"""
    calls: list[tuple[str, tuple]] = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return []

    monkeypatch.setattr(fx_server, "readonly_query", fake_query)
    return calls


async def _tool_names(mcp) -> set[str]:
    return {tool.name for tool in await mcp.list_tools()}


async def _call(mcp, name: str, **kwargs):
    """按真实路径调用工具：会经过身份校验与参数校验。

    MCP SDK 的 ``call_tool`` 返回 ``(content, structured)``；这里优先取结构化结果，
    取不到再解析文本内容，避免依赖具体版本的返回约定。
    """
    context.set_identity("tester", "fxbar")
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


async def test_exposed_tools_are_scoped_only(captured):
    """刻意不暴露任意 SQL 工具——大表上模型写全表扫的代价不可控。"""
    names = await _tool_names(build())
    assert names == {
        "describe_fx_schema",
        "list_fx_instruments",
        "get_fx_bars",
        "get_latest_fx_quote",
        "list_tables",
        "describe_table",
    }
    assert "query" not in names


async def test_get_fx_bars_uses_default_window(captured):
    result = await _call(build(), "get_fx_bars", contract_code="EURUSD")

    assert result["ok"] is True
    sql, params = captured[-1]
    assert "`CONTRACT_CODE` = %s" in sql and "EURUSD" in params
    assert result["window"]["days"] > 0
    assert result["window"]["clamped"] is False


async def test_get_fx_bars_accepts_explicit_range(captured):
    result = await _call(
        build(),
        "get_fx_bars",
        contract_code="USDCNY",
        frequency="1D",
        start_time="2026-09-05",
        end_time="2026-09-11",
    )
    assert result["ok"] is True
    assert result["frequency"] == "1D"
    assert result["window"]["explicit_range"] is True
    assert result["window"]["start"] == "2026-09-05 00:00:00"


async def test_get_fx_bars_reports_clamping(captured):
    result = await _call(build(), "get_fx_bars", contract_code="EURUSD", days=365)
    assert result["ok"] is True
    assert result["window"]["clamped"] is True


async def test_get_fx_bars_returns_error_instead_of_raising(captured):
    """参数非法返回 ok:false，模型才能读到原因自行改写（而不是中断对话）。"""
    result = await _call(build(), "get_fx_bars", contract_code="EURUSD", frequency="2H")
    assert result["ok"] is False
    assert "frequency" in result["error"]


async def test_get_latest_fx_quote_reports_miss_with_hint(captured):
    result = await _call(build(), "get_latest_fx_quote", contract_code="EURUSD")
    assert result["ok"] is False
    assert "lookback_days" in result["error"]


async def test_tools_require_authentication():
    """没有身份时工具必须直接失败（业务代码主动调 current_username）。"""
    mcp = build()
    context.clear_identity()
    with pytest.raises(Exception):
        await mcp.call_tool("describe_fx_schema", {})


async def test_describe_fx_schema_is_callable_without_db(captured):
    result = await _call(build(), "describe_fx_schema")
    assert result["ok"] is True
    assert result["table"] == "`HSDC_HDS2_BAR_FX`"
    assert captured == []  # 纯计算，不碰数据库
