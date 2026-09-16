"""外汇行情问数（hbaseqa）测试。

Thrift 侧用 `thrift2_stub` 起一个**真实的 Thrift2 服务**（thriftpy2.make_server）：
走真实 socket、真实二进制协议、与生产同一份 IDL。比 mock 掉 client 扎实得多——
方法名、字段 ID、类型都被覆盖到，唯一没覆盖的只有 HBase 自身行为。

需要注入故障（连接被拒、服务端返回怪异异常）时才换成 `FailingClient`——
一个只有 6 个方法的假客户端。
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Callable

import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

import easy_mcp_server.businesses.hbaseqa.client as hbase_client
import easy_mcp_server.businesses.hbaseqa.config as hbase_config
import easy_mcp_server.businesses.hbaseqa.fxspot as fxspot
import easy_mcp_server.businesses.hbaseqa.guard as hbase_guard
import easy_mcp_server.businesses.hbaseqa.metadata as hbase_metadata
import easy_mcp_server.businesses.hbaseqa.times as hbase_times
from easy_mcp_server.app import create_app
from easy_mcp_server.businesses.hbaseqa import rowkey as rowkey_fmt
from easy_mcp_server.businesses.hbaseqa.server import build
from easy_mcp_server.context import set_identity
from thrift2_stub import (
    MINUTE,
    T0,
    TABLE_CITI,
    TABLE_DEPTH,
    TABLE_TICK,
    cells,
    row_key,
    stub_server,
)

# 生产上的四张外汇行情表：渠道 / 产品类型 / 数据类型 三维度组合
REAL_TABLES = {
    "HSDC_HDS2_UBS_FXSPOT_BAR_DEPTH": ("UBS", "FXSPOT", "BAR_DEPTH"),
    "HSDC_HDS2_JPMC_FXSPOT_BAR_DEPTH": ("JPMC", "FXSPOT", "BAR_DEPTH"),
    "HSDC_HDS2_UBS_FXFWD_BAR_DEPTH": ("UBS", "FXFWD", "BAR_DEPTH"),
    # 渠道名自带下划线、数据类型是复合词，命名段数与前三个不同
    "HSDC_HDS2_BEST_HO_FXSPOT_BAR_BEST": ("BEST_HO", "FXSPOT", "BAR_BEST"),
}


def winerror(code: int) -> OSError:
    """构造与 Windows 一致的 socket 异常（10053 → ConnectionAbortedError）。"""
    text = "你的主机中的软件中止了一个已建立的连接。"
    if code == 10053:
        return ConnectionAbortedError(code, text)
    if code == 10054:
        return ConnectionResetError(code, text)
    return OSError(code, text)


class ThriftLikeApplicationException(Exception):
    """复刻 thriftpy2 的 `TApplicationException` / `TProtocolException`。

    它们的 `__str__` 实现是"直接返回 message"，而 message 可能是 bytes，
    于是 `str(exc)` 抛 `TypeError: __str__ returned non-string (type bytes)`。
    """

    def __init__(self, message: bytes):
        super().__init__()
        self.message = message

    def __str__(self):
        return self.message if self.message else "unknown"


class FailingClient:
    """每个调用都抛指定异常的假客户端，用于注入连接/协议类故障。"""

    def __init__(self, make_exception: Callable[[], Exception]):
        self.make_exception = make_exception
        self.calls = 0

    def _fail(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise self.make_exception()

    getTableNamesByPattern = _fail
    getTableDescriptor = _fail
    openScanner = _fail
    getScannerRows = _fail
    closeScanner = _fail
    getThriftServerType = _fail


def _inject_failure(monkeypatch, make_exception: Callable[[], Exception]) -> FailingClient:
    fake = FailingClient(make_exception)
    monkeypatch.setattr(hbase_client, "get_client", lambda: fake)
    return fake


# ── 夹具：真实 Thrift2 stub 服务 ──────────────────────────────────────


@pytest.fixture(autouse=True)
def _no_shared_connection():
    """连接是模块级单例，测试之间必须清干净，避免相互污染。"""
    hbase_client.reset_connection()
    yield
    hbase_client.reset_connection()


@asynccontextmanager
async def _stub_cluster(monkeypatch, tables=None):
    """起 stub 服务并把 hbaseqa 指向它（真实 socket + 真实协议）。"""
    with stub_server(tables) as (module, handler, port):
        monkeypatch.setenv("HBASEQA_THRIFT_HOST", "127.0.0.1")
        monkeypatch.setenv("HBASEQA_THRIFT_PORT", str(port))
        hbase_client.reset_connection()
        try:
            yield handler
        finally:
            hbase_client.reset_connection()


@pytest.fixture
def cluster(monkeypatch):
    with stub_server() as (module, handler, port):
        monkeypatch.setenv("HBASEQA_THRIFT_HOST", "127.0.0.1")
        monkeypatch.setenv("HBASEQA_THRIFT_PORT", str(port))
        hbase_client.reset_connection()
        yield handler
        hbase_client.reset_connection()


@pytest.fixture
def real_cluster(monkeypatch):
    """只有四张生产表的假集群，用于验证维度筛选。"""
    tables = {
        name: {
            row_key(T0, "EURUSDSP"): cells(
                CHANNEL=channel, INSTRUMENT=instrument, CONTRACTCODE="EURUSDSP"
            )
        }
        for name, (channel, instrument, _) in REAL_TABLES.items()
    }
    with stub_server(tables) as (module, handler, port):
        monkeypatch.setenv("HBASEQA_THRIFT_HOST", "127.0.0.1")
        monkeypatch.setenv("HBASEQA_THRIFT_PORT", str(port))
        hbase_client.reset_connection()
        yield handler
        hbase_client.reset_connection()


# ── 配置与时间 ────────────────────────────────────────────────────────


def test_default_config_values():
    assert hbase_config.thrift_port() == 9090
    assert hbase_config.max_rows() == 500
    assert hbase_config.max_scanned_rows() == 50_000
    assert hbase_config.column_family() == "CF"
    # 真实 rowkey 以 region 开头，所以默认不是纯 ts
    assert hbase_config.rowkey_layout() == "region_ts"
    assert hbase_config.region_prefixes() == ("00",)
    assert hbase_config.default_window_hours() == 6


def test_env_overrides_are_read(monkeypatch):
    monkeypatch.setenv("HBASEQA_THRIFT_HOST", "hbase-thrift")
    monkeypatch.setenv("HBASEQA_THRIFT_PORT", "9099")
    monkeypatch.setenv("HBASEQA_MAX_ROWS", "7")
    assert hbase_config.thrift_host() == "hbase-thrift"
    assert hbase_config.thrift_port() == 9099
    assert hbase_config.max_rows() == 7


def test_invalid_int_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("HBASEQA_MAX_ROWS", "abc")
    assert hbase_config.max_rows() == hbase_config.DEFAULT_MAX_ROWS


def test_parse_ms_accepts_int_and_iso(monkeypatch):
    monkeypatch.setenv("HBASEQA_TZ", "UTC")
    assert hbase_times.parse_ms(1677583200000) == 1677583200000
    assert hbase_times.parse_ms("1677583200000") == 1677583200000
    # T0 = 1677583200000 = 2023-02-28 11:20:00 UTC
    assert hbase_times.parse_ms("2023-02-28T11:20:00Z") == T0
    assert hbase_times.parse_ms("2023-02-28 11:20:00") == T0


def test_parse_ms_rejects_garbage():
    with pytest.raises(ValueError):
        hbase_times.parse_ms("上个月")
    with pytest.raises(ValueError):
        hbase_times.parse_ms("")


def test_resolve_window_supports_start_end_and_hours():
    start, end = hbase_times.resolve_window(start=T0, end=T0 + 3_600_000)
    assert (start, end) == (T0, T0 + 3_600_000)
    start, end = hbase_times.resolve_window(end=T0, hours=1)
    assert (start, end) == (T0 - 3_600_000, T0)
    start, end = hbase_times.resolve_window(end=T0, days=1)
    assert (start, end) == (T0 - 86_400_000, T0)


def test_resolve_window_defaults_to_recent_window(monkeypatch):
    monkeypatch.setenv("HBASEQA_DEFAULT_WINDOW_HOURS", "2")
    start, end = hbase_times.resolve_window(end=T0)
    assert end - start == 2 * 3_600_000


def test_resolve_window_rejects_non_positive_hours():
    with pytest.raises(ValueError):
        hbase_times.resolve_window(end=T0, hours=0)


# ── 安全网关 ──────────────────────────────────────────────────────────


def test_table_name_rejects_system_namespace_and_odd_chars():
    assert hbase_guard.assert_table_name(TABLE_DEPTH) == TABLE_DEPTH
    for bad in ("system:meta", "ns:sys:table", "--evil", "", "9abc", "a b"):
        with pytest.raises(ValueError):
            hbase_guard.assert_table_name(bad)


def test_visibility_drops_unprefixed_and_denied_tables(monkeypatch):
    visible = hbase_guard.filter_visible([TABLE_DEPTH, "other_table", TABLE_TICK])
    assert visible == [TABLE_DEPTH, TABLE_TICK]
    monkeypatch.setenv("HBASEQA_DENY_TABLES", TABLE_TICK)
    assert hbase_guard.filter_visible([TABLE_DEPTH, TABLE_TICK]) == [TABLE_DEPTH]
    monkeypatch.setenv("HBASEQA_LIST_ALL_TABLES", "1")
    assert "other_table" in hbase_guard.filter_visible([TABLE_DEPTH, "other_table"])


def test_visibility_checks_qualifier_for_namespaced_tables():
    """`ns:HSDC_...` 的前缀判断要看 qualifier，否则命名空间表会被整批漏掉。"""
    assert hbase_guard.is_visible(f"myns:{TABLE_DEPTH}") is True
    assert hbase_guard.is_visible("myns:other_table") is False


def test_allowlist_wins_over_prefix(monkeypatch):
    monkeypatch.setenv("HBASEQA_ALLOWED_TABLES", TABLE_CITI)
    assert hbase_guard.filter_visible([TABLE_DEPTH, TABLE_CITI]) == [TABLE_CITI]


def test_literal_check_blocks_filter_language_injection():
    assert hbase_guard.assert_literal("1677583200000") == "1677583200000"
    for bad in ("1' AND ('x'='x", "1,true,false)", "1); drop table", ""):
        with pytest.raises(ValueError):
            hbase_guard.assert_literal(bad)


def test_row_ranges_are_region_prefixed_and_exclusive():
    assert hbase_guard.row_ranges(T0, T0 + 1) == [("00" + str(T0), "00" + str(T0 + 2))]


def test_row_ranges_empty_when_layout_unknown(monkeypatch):
    """rowkey 格式未知时不做范围过滤，退回 CF:TIME 列过滤。"""
    monkeypatch.setenv("HBASEQA_ROWKEY_LAYOUT", "unknown")
    assert hbase_guard.row_ranges(T0, T0 + 1) == []


def test_row_limit_is_capped_by_max_rows(monkeypatch):
    monkeypatch.setenv("HBASEQA_MAX_ROWS", "50")
    assert hbase_guard.row_limit(1000) == 50
    assert hbase_guard.row_limit(None) == 50
    assert hbase_guard.row_limit(3) == 3


# ── 元数据与维度反解 ──────────────────────────────────────────────────


def test_parse_table_name_splits_channel_instrument_datatype():
    assert hbase_metadata.parse_table_name(TABLE_DEPTH) == {
        "channel": "UBS",
        "instrument": "FXSPOT",
        "datatype": "BAR_DEPTH",
    }


def test_parse_table_name_keeps_multi_token_datatype_together():
    parsed = hbase_metadata.parse_table_name("HSDC_HDS2_CITI_METAL_TICK_DEPTH")
    assert parsed["datatype"] == "TICK_DEPTH"
    assert parsed["instrument"] == "METAL"


def test_parse_table_name_ignores_namespace_prefix():
    parsed = hbase_metadata.parse_table_name(f"myns:{TABLE_DEPTH}")
    assert parsed == {"channel": "UBS", "instrument": "FXSPOT", "datatype": "BAR_DEPTH"}


@pytest.mark.parametrize("name,expected", sorted(REAL_TABLES.items()))
def test_parse_table_name_on_real_tables(name, expected):
    """四张生产表都能反解出正确三维度（含带下划线的渠道 BEST_HO）。"""
    channel, instrument, datatype = expected
    assert hbase_metadata.parse_table_name(name) == {
        "channel": channel,
        "instrument": instrument,
        "datatype": datatype,
    }


def test_parse_table_name_falls_back_when_instrument_unknown():
    """表名偏离约定时退化为固定位次切分，不把 None 抛给上层。"""
    assert hbase_metadata.parse_table_name("HSDC_HDS2_AAA_UNKNOWN_BAR") == {
        "channel": "AAA",
        "instrument": "UNKNOWN",
        "datatype": "BAR",
    }


def test_list_tables_returns_only_visible_ones(cluster):
    tables = [item["table"] for item in hbase_metadata.list_tables()]
    assert tables == sorted([TABLE_DEPTH, TABLE_TICK, TABLE_CITI])


def test_real_tables_are_all_visible(real_cluster):
    assert [item["table"] for item in hbase_metadata.list_tables()] == sorted(REAL_TABLES)


@pytest.mark.parametrize(
    "filters,expected",
    [
        ({}, sorted(REAL_TABLES)),
        ({"channel": "UBS"}, sorted(
            ["HSDC_HDS2_UBS_FXSPOT_BAR_DEPTH", "HSDC_HDS2_UBS_FXFWD_BAR_DEPTH"]
        )),
        ({"instrument": "FXSPOT"}, sorted(
            ["HSDC_HDS2_UBS_FXSPOT_BAR_DEPTH", "HSDC_HDS2_JPMC_FXSPOT_BAR_DEPTH",
             "HSDC_HDS2_BEST_HO_FXSPOT_BAR_BEST"]
        )),
        ({"datatype": "BAR_BEST"}, ["HSDC_HDS2_BEST_HO_FXSPOT_BAR_BEST"]),
        ({"channel": "UBS", "datatype": "BAR_DEPTH"}, sorted(
            ["HSDC_HDS2_UBS_FXSPOT_BAR_DEPTH", "HSDC_HDS2_UBS_FXFWD_BAR_DEPTH"]
        )),
        ({"channel": "JPMC", "instrument": "FXFWD"}, []),
    ],
)
def test_resolve_tables_on_real_tables(real_cluster, filters, expected):
    assert hbase_metadata.resolve_tables(**filters) == expected


def test_describe_table_exposes_dictionary(cluster):
    info = hbase_metadata.describe_table(TABLE_DEPTH)
    assert info["channel"] == "UBS" and info["instrument"] == "FXSPOT"
    assert info["families"] == ["CF"]
    assert info["rowkey"]["range_filter"] is True
    assert "TIME" in [column["qualifier"] for column in info["columns"]]


def test_sample_rows_decodes_values(cluster):
    payload = hbase_metadata.sample_rows(TABLE_DEPTH, limit=2)
    assert payload["row_count"] == 2
    first = payload["rows"][0]
    parsed = rowkey_fmt.parse(first["rowkey"])
    assert parsed and parsed["ts_ms"] == T0 and parsed["region"] == "00"
    assert first["CONTRACTCODE"] == "EURUSDSP"
    assert first["CLOSE"] == 1.086
    assert first["ts_ms"] == T0 and first["ts"]


def test_sample_rows_limit_is_capped(cluster):
    assert hbase_metadata.sample_rows(TABLE_DEPTH, limit=999)["row_count"] == 20


# ── 领域层：scan 计划与合约解析 ───────────────────────────────────────


def test_scan_plan_prefixes_range_with_region():
    """真实 rowkey 以 region 开头，范围必须拼 `{region}{毫秒}`。"""
    plan = fxspot.scan_plan(TABLE_DEPTH, T0, T0 + MINUTE)
    assert plan["ranges"] == [("00" + str(T0), "00" + str(T0 + MINUTE + 1))]
    assert plan["filter"] is None


def test_scan_plan_supports_multiple_regions(monkeypatch):
    monkeypatch.setenv("HBASEQA_REGION_PREFIXES", "00,07")
    plan = fxspot.scan_plan(TABLE_DEPTH, T0, T0 + MINUTE)
    assert plan["ranges"] == [
        ("00" + str(T0), "00" + str(T0 + MINUTE + 1)),
        ("07" + str(T0), "07" + str(T0 + MINUTE + 1)),
    ]


def test_scan_plan_supports_legacy_ts_layout(monkeypatch):
    """单机简化表可能真的用纯毫秒当 rowkey。"""
    monkeypatch.setenv("HBASEQA_ROWKEY_LAYOUT", "ts")
    plan = fxspot.scan_plan(TABLE_DEPTH, T0, T0 + MINUTE)
    assert plan["ranges"] == [(str(T0), str(T0 + MINUTE + 1))]


def test_scan_plan_falls_back_to_column_filter(monkeypatch):
    monkeypatch.setenv("HBASEQA_ROWKEY_LAYOUT", "unknown")
    plan = fxspot.scan_plan(TABLE_DEPTH, T0, T0 + MINUTE)
    assert plan["ranges"] == []
    assert "SingleColumnValueFilter('CF', 'TIME'" in plan["filter"]
    assert plan["filter"].count("SingleColumnValueFilter") == 2


def test_scan_plan_only_requests_known_qualifiers():
    plan = fxspot.scan_plan(TABLE_DEPTH, T0, T0 + MINUTE)
    assert plan["columns"] == [f"CF:{name}" for name in hbase_config.FIELD_NAMES]


def test_resolve_contracts_expands_sp_suffix():
    wanted, mapping = fxspot.resolve_contracts(
        ["EURUSD", "USDJPYSP"], ["EURUSDSP", "USDJPYSP"]
    )
    assert wanted == ["EURUSDSP", "USDJPYSP"]
    assert mapping == {"EURUSD": "EURUSDSP"}


def test_resolve_contracts_keeps_unknown_code_as_is():
    wanted, mapping = fxspot.resolve_contracts(["GBPUSD"], ["EURUSDSP"])
    assert wanted == ["GBPUSD"] and mapping == {}


def test_resolve_contracts_is_case_insensitive():
    wanted, _ = fxspot.resolve_contracts(["eurusdsp"], ["EURUSDSP"])
    assert wanted == ["EURUSDSP"]


# ── 领域层：取数（经真实 socket 打到 stub 服务） ──────────────────────


def test_get_rows_filters_by_contract(cluster):
    payload = fxspot.collect([TABLE_DEPTH], T0, T0 + 12 * MINUTE, contract_codes=["EURUSD"])
    assert payload["contract_mapping"] == {"EURUSD": "EURUSDSP"}
    assert {row["CONTRACTCODE"] for row in payload["rows"]} == {"EURUSDSP"}
    assert payload["available_contracts"] == ["EURUSDSP", "USDJPYSP"]


def test_get_rows_covers_two_contracts(cluster):
    payload = fxspot.collect([TABLE_DEPTH], T0, T0 + 12 * MINUTE)
    assert payload["available_contracts"] == ["EURUSDSP", "USDJPYSP"]
    assert payload["row_count"] == 24


def test_get_rows_respects_time_window(cluster):
    """窗口 [T0, T0+2min]：EURUSD 占满 3 根，USDJPY 错位 30s 只落进来 2 根。"""
    payload = fxspot.collect([TABLE_DEPTH], T0, T0 + 2 * MINUTE)
    assert payload["row_count"] == 5
    assert all(T0 <= row["TIME"] <= T0 + 2 * MINUTE for row in payload["rows"])
    assert payload["skipped_out_of_range"] == 0


def test_order_desc_returns_newest_first(cluster):
    payload = fxspot.collect([TABLE_DEPTH], T0, T0 + 12 * MINUTE, order="desc", limit=3)
    times_seen = [row["TIME"] for row in payload["rows"]]
    assert times_seen == sorted(times_seen, reverse=True)
    assert payload["rows"][0]["TIME"] == T0 + 11 * MINUTE + 30_000


def test_order_asc_returns_oldest_first(cluster):
    payload = fxspot.collect([TABLE_DEPTH], T0, T0 + 12 * MINUTE, limit=3)
    times_seen = [row["TIME"] for row in payload["rows"]]
    assert times_seen == sorted(times_seen)


def test_order_desc_uses_server_side_reverse_scan(cluster):
    """order='desc' 走服务端反向扫描（Thrift2 的 TScan.reversed），拿到的是最新一根。

    注：limit=1 时窗口里还有更多行，所以 `truncated` 为真、notes 里会有截断说明——
    这恰恰是期望行为（宁可说出来，不要静默少给）。
    """
    payload = fxspot.collect([TABLE_DEPTH], T0, T0 + MINUTE, order="desc", limit=1)
    assert "openScanner" in cluster.calls
    assert payload["rows"][0]["ts_ms"] == T0 + MINUTE  # 最新一根
    assert payload["truncated"] is True


def test_limit_cannot_exceed_max_rows(monkeypatch, cluster):
    monkeypatch.setenv("HBASEQA_MAX_ROWS", "5")
    payload = fxspot.collect([TABLE_DEPTH], T0, T0 + 12 * MINUTE, limit=1000)
    assert payload["row_count"] == 5
    assert payload["truncated"] is True


def test_buysell_filter_applies(cluster):
    """窗口是闭区间，T0+1min 这根 EURUSD 也在里面，所以是 3 行而不是 2 行。"""
    payload = fxspot.collect([TABLE_DEPTH], T0, T0 + MINUTE, buysell="MID")
    assert payload["row_count"] == 3
    assert fxspot.collect([TABLE_DEPTH], T0, T0 + MINUTE, buysell="BID")["row_count"] == 0


@pytest.mark.parametrize("alias", ["1m", "1M", "1min", "M1"])
def test_frequency_alias_is_normalised_to_library_writing(cluster, alias):
    """库里存 1N，模型说 1m / 1min 也要能命中。"""
    payload = fxspot.collect([TABLE_DEPTH], T0, T0 + MINUTE, frequency=alias)
    assert payload["row_count"] == 3


def test_unknown_frequency_is_passed_through():
    assert hbase_config.normalise_frequency("5N") == "5N"
    assert hbase_config.normalise_frequency(None) is None
    assert hbase_config.normalise_frequency("  ") is None


@pytest.mark.parametrize("bad", ["limit", "mid price", "1", "BID,ASK"])
def test_buysell_rejects_unknown_values(bad):
    with pytest.raises(ValueError):
        fxspot.assert_buysell(bad)


def test_buysell_accepts_known_values_case_insensitively():
    assert fxspot.assert_buysell("mid") == "MID"
    assert fxspot.assert_buysell("ask") == "ASK"
    assert fxspot.assert_buysell(None) is None
    assert fxspot.assert_buysell("") is None


def test_buysell_values_are_configurable(monkeypatch):
    monkeypatch.setenv("HBASEQA_BUYSELL_VALUES", "BID ONLY,MID")
    assert fxspot.assert_buysell("bid only") == "BID ONLY"
    with pytest.raises(ValueError):
        fxspot.assert_buysell("ASK")


def test_invalid_order_is_rejected():
    with pytest.raises(ValueError):
        fxspot.assert_order("newest")


def test_summary_mentions_contracts_and_change(cluster):
    payload = fxspot.collect([TABLE_DEPTH], T0, T0 + MINUTE, contract_codes=["EURUSDSP"])
    assert "1 个合约" in payload["summary"]
    assert "EURUSDSP" in payload["summary"]


def test_empty_result_has_readable_summary(cluster):
    payload = fxspot.collect([TABLE_DEPTH], T0 - 10 * 86_400_000, T0 - 9 * 86_400_000)
    assert payload["row_count"] == 0
    assert "没有匹配" in payload["summary"]


def test_list_contracts_aggregates_per_contract(cluster):
    payload = fxspot.collect_contracts([TABLE_DEPTH], T0, T0 + 12 * MINUTE)
    assert [row["contract"] for row in payload["rows"]] == ["EURUSDSP", "USDJPYSP"]
    eurusd = payload["rows"][0]
    assert eurusd["bar_count"] == 12
    assert eurusd["last_close"] == 1.086
    assert eurusd["table"] == TABLE_DEPTH


# ── 通道失效：识别、重连重试与可读报错 ────────────────────────────────


@pytest.mark.parametrize("code", [10053, 10054, 10060, 10061])
def test_winerror_is_treated_as_connection_error(code):
    assert hbase_client.is_connection_error(winerror(code)) is True


def test_builtin_connection_errors_are_detected():
    assert hbase_client.is_connection_error(ConnectionResetError("x")) is True
    assert hbase_client.is_connection_error(TimeoutError("x")) is True


def test_wrapped_connection_error_is_detected():
    """thriftpy2 会把底层异常再包一层，必须沿 cause 链找根因。"""
    try:
        try:
            raise winerror(10053)
        except OSError as inner:
            raise RuntimeError("thrift 调用失败") from inner
    except RuntimeError as e:
        assert hbase_client.is_connection_error(e) is True


def test_thrift_transport_exception_is_detected_by_name():
    class TTransportException(Exception):
        pass

    assert hbase_client.is_connection_error(TTransportException("boom")) is True


def test_tio_error_can_retry_flag_is_honored():
    """Thrift2 的 TIOError 自带 canRetry：服务端说可重试才重试。"""

    class TIOError(Exception):
        def __init__(self, canRetry: bool):
            self.canRetry = canRetry

    assert hbase_client.is_connection_error(TIOError(True)) is True
    assert hbase_client.is_connection_error(TIOError(False)) is False


def test_business_errors_are_not_retryable():
    assert hbase_client.is_connection_error(ValueError("表不可查询: x")) is False
    assert hbase_client.is_connection_error(KeyError("nope")) is False


def test_run_with_retry_recovers_after_reset(monkeypatch):
    """第一次断、重连后成功：调用方感知不到异常。"""
    calls, resets = {"n": 0}, {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise winerror(10053)
        return "ok"

    monkeypatch.setattr(
        hbase_client, "reset_connection", lambda: resets.__setitem__("n", resets["n"] + 1)
    )
    assert hbase_client.run_with_retry(flaky) == "ok"
    assert (calls["n"], resets["n"]) == (2, 1)


def test_run_with_retry_does_not_retry_business_errors():
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise ValueError("表不可查询")

    with pytest.raises(ValueError):
        hbase_client.run_with_retry(boom)
    assert calls["n"] == 1


def test_run_with_retry_reraises_after_exhausting_attempts(monkeypatch):
    monkeypatch.setenv("HBASEQA_CONNECT_ATTEMPTS", "2")
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise winerror(10053)

    with pytest.raises(OSError):
        hbase_client.run_with_retry(boom)
    assert calls["n"] == 2


def test_connect_attempts_can_be_disabled(monkeypatch):
    monkeypatch.setenv("HBASEQA_CONNECT_ATTEMPTS", "1")
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise winerror(10053)

    with pytest.raises(OSError):
        hbase_client.run_with_retry(boom)
    assert calls["n"] == 1


def test_operations_refetch_client_so_retry_can_work(monkeypatch, cluster):
    """重试能生效的前提：每次操作都重新取 client，而不是缓存住废连接。"""
    original = hbase_client.get_client
    calls = {"n": 0}

    def counting():
        calls["n"] += 1
        return original()

    monkeypatch.setattr(hbase_client, "get_client", counting)
    hbase_metadata.sample_rows(TABLE_DEPTH, limit=1)
    assert calls["n"] >= 2  # openScanner + getScannerRows 各取一次


# ── 异常的字符串化：thriftpy2 的 __str__ 会返回 bytes ─────────────────


def test_thrift_like_exception_really_reproduces_the_bug():
    """先证明这个测试替身确实能复现原生行为，否则回归测试是假的。"""
    with pytest.raises(TypeError, match="__str__ returned non-string"):
        str(ThriftLikeApplicationException(b"boom"))


def test_exception_text_survives_bytes_str():
    exc = ThriftLikeApplicationException(
        b"org.apache.hadoop.hbase.security.AccessDeniedException: denied"
    )
    text = hbase_guard.exception_text(exc)
    assert "AccessDeniedException: denied" in text  # 服务端原话被解出来
    assert "ThriftLikeApplicationException" in text  # 类型名保留，便于定位


def test_exception_text_keeps_normal_exception_readable():
    assert hbase_guard.exception_text(ValueError("表不可查询: x")) == "表不可查询: x"


def test_exception_text_never_raises():
    """极端情况：__str__ / __repr__ 双双炸掉，也要能兜出一行文本。"""

    class Hostile(Exception):
        def __str__(self):
            raise RuntimeError("nope")

        def __repr__(self):
            raise RuntimeError("nope")

    assert "Hostile" in hbase_guard.exception_text(Hostile())


def test_as_text_decodes_bytes():
    assert hbase_guard.as_text(b"\xe4\xb8\xad\xe6\x96\x87") == "中文"
    assert hbase_guard.as_text("plain") == "plain"


ACCESS_DENIED = b"org.apache.hadoop.hbase.security.AccessDeniedException: denied"


async def test_bytes_str_exception_does_not_mask_the_real_error(monkeypatch):
    """回归：曾报 "Error executing tool list_tables: __str__ returned non-string
    (type bytes)" —— 真实错误（服务端原话）被彻底盖掉。"""
    _inject_failure(monkeypatch, lambda: ThriftLikeApplicationException(ACCESS_DENIED))
    set_identity("alice", "hbaseqa")

    payload = await _call_direct(build(), "list_tables", {})
    error = payload[0]["error"]
    assert "AccessDeniedException" in error
    assert "__str__ returned non-string" not in error


async def test_bytes_str_exception_on_dict_returning_tool(monkeypatch):
    _inject_failure(monkeypatch, lambda: ThriftLikeApplicationException(ACCESS_DENIED))
    set_identity("alice", "hbaseqa")

    payload = await _call_direct(build(), "sample_rows", {"table": TABLE_DEPTH})
    assert payload["ok"] is False
    assert "AccessDeniedException" in payload["error"]
    assert "__str__ returned non-string" not in payload["error"]


# ── Thrift 版本错配 ───────────────────────────────────────────────────


def test_thrift1_server_is_diagnosed():
    """我们的 Thrift2 客户端打到 Thrift1 服务上时的报错形态。"""
    exc = ThriftLikeApplicationException(b"Invalid method name: 'getTableNamesByPattern'")
    hint = hbase_client.thrift_version_hint(exc)
    assert hint is not None
    assert "Thrift1" in hint and "hbase thrift2 start" in hint


def test_thrift2_server_is_diagnosed_for_thrift1_callers():
    """反向：Thrift1 客户端（happybase）打到 Thrift2 服务上——就是当初踩的坑。"""
    exc = ThriftLikeApplicationException(b"Invalid method name: 'getTableNames'")
    hint = hbase_client.thrift_version_hint(exc)
    assert hint is not None
    assert "Thrift2" in hint


def test_thrift_version_hint_ignores_unrelated_errors():
    assert hbase_client.thrift_version_hint(ValueError("表不可查询: x")) is None
    assert hbase_client.thrift_version_hint(winerror(10053)) is None


async def test_thrift_mismatch_reaches_the_model_with_a_fix(monkeypatch):
    """回归：用户只看到 `Invalid method name: ...`，无从下手。"""
    _inject_failure(
        monkeypatch,
        lambda: ThriftLikeApplicationException(b"Invalid method name: 'getTableNamesByPattern'"),
    )
    set_identity("alice", "hbaseqa")

    payload = await _call_direct(build(), "list_tables", {})
    error = payload[0]["error"]
    assert "Invalid method name" in error  # 服务端原话保留，便于对日志
    assert "Thrift1" in error  # 同时给出该怎么办


# ── 连接参数：传输/协议/Kerberos ──────────────────────────────────────


def test_transport_factory_follows_config(monkeypatch):
    from thriftpy2.transport import TBufferedTransportFactory, TFramedTransportFactory

    monkeypatch.setenv("HBASEQA_THRIFT_TRANSPORT", "framed")
    assert isinstance(hbase_client._transport_factory(), TFramedTransportFactory)
    monkeypatch.setenv("HBASEQA_THRIFT_TRANSPORT", "buffered")
    assert isinstance(hbase_client._transport_factory(), TBufferedTransportFactory)


def test_protocol_factory_follows_config(monkeypatch):
    from thriftpy2.protocol import TBinaryProtocolFactory, TCompactProtocolFactory

    monkeypatch.setenv("HBASEQA_THRIFT_PROTOCOL", "compact")
    assert isinstance(hbase_client._protocol_factory(), TCompactProtocolFactory)
    monkeypatch.setenv("HBASEQA_THRIFT_PROTOCOL", "binary")
    assert isinstance(hbase_client._protocol_factory(), TBinaryProtocolFactory)


def test_unknown_transport_and_protocol_fall_back(monkeypatch):
    monkeypatch.setenv("HBASEQA_THRIFT_TRANSPORT", "quantum")
    monkeypatch.setenv("HBASEQA_THRIFT_PROTOCOL", "telepathy")
    assert hbase_config.thrift_transport() == "buffered"
    assert hbase_config.thrift_protocol() == "binary"


def test_kerberos_enabled_fails_closed(monkeypatch):
    monkeypatch.setenv("HBASEQA_KERBEROS_ENABLED", "1")
    assert hbase_config.kerberos_enabled() is True
    with pytest.raises(RuntimeError, match="Kerberos"):
        hbase_client._assert_kerberos_ready()


def test_open_failure_hint_points_at_thrift2_and_zookeeper(monkeypatch):
    """连不上时要一次说清：服务没起 / 端口写错（ZK 或 Thrift1）/ 传输不匹配。"""
    import thriftpy2.rpc

    def boom(*args: Any, **kwargs: Any):
        raise winerror(10061)

    monkeypatch.setattr(thriftpy2.rpc, "make_client", boom)
    with pytest.raises(RuntimeError) as excinfo:
        hbase_client._open_client()
    message = str(excinfo.value)
    assert "Thrift2" in message
    assert "ZooKeeper" in message
    assert "hbase thrift2 start" in message


def test_client_is_reused_across_calls(cluster):
    assert hbase_client.get_client() is hbase_client.get_client()


def test_reset_connection_closes_and_forgets(monkeypatch):
    closed = {"n": 0}

    class Client:
        def close(self) -> None:
            closed["n"] += 1

    monkeypatch.setattr(hbase_client, "_client", Client())
    hbase_client.reset_connection()
    assert closed["n"] == 1
    assert hbase_client._client is None


def test_load_idl_is_cached():
    assert hbase_client.load_idl() is hbase_client.load_idl()


# ── 端到端：进程内工具调用 ────────────────────────────────────────────


async def _call_direct(mcp, name: str, args: dict | None = None):
    """进程内调用工具，返回工具函数的原始返回值。

    "通道一直断"这类场景刻意不走 HTTP：一来更精确（直接验证"异常被转成 ok:false"），
    二来鉴权中间件是 `BaseHTTPMiddleware`，它包着流式响应应用，客户端中断连接时
    Starlette 会抛 `RuntimeError("No response returned.")` 并污染后续测试。

    返回值形状随 FastMCP 对输出 schema 的推断而变（实测）：
    - `-> list[dict]`：`(content_blocks, {"result": [...]})`
    - `-> dict`：`[content_block]`，文本就是 JSON
    """
    result = await mcp.call_tool(name, args or {})
    if isinstance(result, tuple):
        structured = result[1]
        if isinstance(structured, dict):
            return structured.get("result", structured)
        return structured
    return json.loads("".join(getattr(block, "text", "") for block in result))


async def test_in_process_tool_call_returns_domain_payload(monkeypatch):
    async with _stub_cluster(monkeypatch):
        set_identity("alice", "hbaseqa")
        payload = await _call_direct(
            build(), "get_fx_bars", {"contract_codes": ["EURUSD"], "start": T0, "end": T0 + MINUTE}
        )
        assert payload["ok"] is True
        assert payload["contract_mapping"] == {"EURUSD": "EURUSDSP"}
        assert payload["rows"]


def _verifier(business: str, key: str) -> str | None:
    return {"hbase-alice": "alice", "hbase-bob": "bob"}.get(key)


@asynccontextmanager
async def _serving(monkeypatch):
    """把 hbaseqa 挂到真实 uvicorn 上，Thrift 侧接 stub 服务。"""
    with stub_server() as (module, handler, port):
        monkeypatch.setenv("HBASEQA_THRIFT_HOST", "127.0.0.1")
        monkeypatch.setenv("HBASEQA_THRIFT_PORT", str(port))
        hbase_client.reset_connection()
        app = create_app(_verifier, businesses_override=[("hbaseqa", build())])
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
        server = uvicorn.Server(config)
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.02)
        actual_port = server.servers[0].sockets[0].getsockname()[1]
        try:
            yield f"http://127.0.0.1:{actual_port}"
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=10)
            hbase_client.reset_connection()


@pytest.fixture
async def hbase_env(monkeypatch):
    async with _serving(monkeypatch) as base:
        yield base


async def _call_tool(base: str, key: str, tool: str, args: dict | None = None):
    async with streamablehttp_client(
        f"{base}/mcp/hbaseqa/", headers={"Authorization": f"Bearer {key}"}
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await session.call_tool(tool, args or {})


async def test_get_fx_bars_end_to_end(hbase_env):
    result = await _call_tool(
        hbase_env,
        "hbase-alice",
        "get_fx_bars",
        {"contract_codes": ["EURUSD"], "start": T0, "end": T0 + 3 * MINUTE},
    )
    text = result.content[0].text
    assert "EURUSDSP" in text and "1.086" in text
    assert "EURUSD" in text  # 映射说明回填给模型


async def test_list_contracts_end_to_end(hbase_env):
    result = await _call_tool(
        hbase_env, "hbase-alice", "list_contracts", {"start": T0, "end": T0 + MINUTE}
    )
    text = result.content[0].text
    assert "EURUSDSP" in text and "USDJPYSP" in text


async def test_describe_fx_schema_is_callable(hbase_env):
    result = await _call_tool(hbase_env, "hbase-bob", "describe_fx_schema")
    text = result.content[0].text
    assert "HSDC_HDS2_" in text and "BUYSELL" in text
    assert "BID" in text and "ASK" in text and "MID" in text
    assert "1N" in text  # 频率口径：1N = 1 分钟


async def test_invalid_buysell_is_reported_with_allowed_values(hbase_env):
    result = await _call_tool(
        hbase_env, "hbase-alice", "get_fx_bars", {"buysell": "middle", "start": T0}
    )
    text = result.content[0].text
    assert "false" in text.lower()
    assert "BID/ASK/MID" in text


async def test_unknown_table_is_reported(hbase_env):
    result = await _call_tool(
        hbase_env, "hbase-alice", "describe_table", {"table": "system:meta"}
    )
    assert "false" in result.content[0].text.lower()


async def test_unresolvable_dimensions_list_available_tables(hbase_env):
    result = await _call_tool(hbase_env, "hbase-alice", "get_fx_bars", {"channel": "NOPE"})
    assert TABLE_DEPTH in result.content[0].text


async def test_invalid_api_key_is_rejected(hbase_env):
    import httpx

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{hbase_env}/mcp/hbaseqa/",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"Authorization": "Bearer wrong-key"},
        )
    assert resp.status_code == 401


async def test_broken_channel_does_not_break_other_tools(monkeypatch):
    """同一个通道故障，每个工具都应是"返回错误"而不是抛异常。"""
    _inject_failure(monkeypatch, lambda: winerror(10053))
    set_identity("alice", "hbaseqa")
    mcp = build()

    for tool, args in (
        ("list_tables", {}),
        ("get_fx_bars", {"table": TABLE_DEPTH, "start": T0}),
        ("describe_table", {"table": TABLE_DEPTH}),
        ("sample_rows", {"table": TABLE_DEPTH}),
    ):
        payload = await _call_direct(mcp, tool, args)
        if isinstance(payload, list):  # list_tables 保持 list 契约
            assert payload[0]["ok"] is False, f"{tool} 未把异常转成错误"
        else:
            assert payload["ok"] is False, f"{tool} 未把异常转成错误"


# ── rowkey 格式 ───────────────────────────────────────────────────────


def test_rowkey_layout_matches_documented_format():
    """`region(2) + time(13) + symbolHash(13) + frequency + side`。"""
    key = rowkey_fmt.build(T0, "EURUSDSP", "MID")
    assert key.startswith("00" + str(T0))
    assert key.endswith("1NMID")
    assert len(key) == 2 + 13 + 13 + len("1N") + len("MID")


def test_rowkey_round_trip():
    key = rowkey_fmt.build(T0, "USDJPYSP", "ASK", region="07", frequency="5N")
    assert rowkey_fmt.parse(key) == {
        "region": "07",
        "ts_ms": T0,
        "symbol_hash": rowkey_fmt.symbol_hash("USDJPYSP"),
        "frequency": "5N",
        "side": "ASK",
    }


def test_symbol_hash_is_stable_and_thirteen_digits():
    first = rowkey_fmt.symbol_hash("EURUSDSP")
    assert first == rowkey_fmt.symbol_hash("EURUSDSP")  # 不能用带随机盐的 hash()
    assert len(first) == 13 and first.isdigit()
    assert first != rowkey_fmt.symbol_hash("USDJPYSP")


def test_same_timestamp_does_not_collide_across_symbols_or_sides():
    """rowkey 里带 symbolHash + side，同一时刻的多合约/多方向互不覆盖。

    这正是当初"纯毫秒时间戳"推断最危险的地方：那样写多合约会互相覆盖。
    """
    keys = {
        rowkey_fmt.build(T0, "EURUSDSP", "MID"),
        rowkey_fmt.build(T0, "USDJPYSP", "MID"),
        rowkey_fmt.build(T0, "EURUSDSP", "BID"),
    }
    assert len(keys) == 3


def test_rowkey_parse_rejects_foreign_layout():
    assert rowkey_fmt.parse(str(T0)) is None  # 纯毫秒：正是曾经的错误假设
    assert rowkey_fmt.parse("bogus") is None


def test_rowkey_build_rejects_bad_region():
    with pytest.raises(ValueError):
        rowkey_fmt.build(T0, "EURUSDSP", "MID", region="0")


# ── seed 造数 ─────────────────────────────────────────────────────────


def test_generate_rows_produces_consistent_ohlc():
    from easy_mcp_server.businesses.hbaseqa import seed

    rows = list(
        seed.generate_rows(TABLE_DEPTH, "EURUSDSP", T0, 20, MINUTE, "1N", ("MID",), "00")
    )
    assert len(rows) == 20
    for key, values in rows:
        parsed = rowkey_fmt.parse(key)
        assert parsed and parsed["side"] == "MID"
        open_ = float(values["CF:OPEN"])
        close = float(values["CF:CLOSE"])
        assert float(values["CF:LOW"]) <= min(open_, close)
        assert float(values["CF:HIGH"]) >= max(open_, close)
        assert values["CF:BUYSELL"] == b"MID"
        assert values["CF:CHANNEL"] == b"UBS"  # 从表名反解出的维度


def test_generate_rows_is_reproducible():
    from easy_mcp_server.businesses.hbaseqa import seed

    def first_close() -> bytes:
        rows = seed.generate_rows(TABLE_DEPTH, "EURUSDSP", T0, 3, MINUTE, "1N", ("MID",), "00")
        return next(rows)[1]["CF:CLOSE"]

    assert first_close() == first_close()


def test_generate_rows_emits_one_row_per_side():
    from easy_mcp_server.businesses.hbaseqa import seed

    rows = list(
        seed.generate_rows(
            TABLE_DEPTH, "EURUSDSP", T0, 2, MINUTE, "1N", ("BID", "ASK", "MID"), "00"
        )
    )
    assert len(rows) == 6
    assert {rowkey_fmt.parse(key)["side"] for key, _ in rows} == {"BID", "ASK", "MID"}


def test_write_then_read_round_trip(cluster):
    """造出来的数据必须能被问数链路读到——rowkey 拼错就会静默读不到。"""
    from easy_mcp_server.businesses.hbaseqa import seed

    rows = seed.generate_rows(TABLE_TICK, "EURUSDSP", T0, 3, MINUTE, "1N", ("MID",), "00")
    assert hbase_client.put_rows(TABLE_TICK, list(rows)) == 3

    payload = fxspot.collect([TABLE_TICK], T0, T0 + 5 * MINUTE)
    # 原有 2 行 + 新增 2 行：T0 那根的 rowkey 与既有行相同，属**覆盖**而非追加
    assert payload["row_count"] == 4


def test_row_exists_detects_the_overwrite_guard(cluster):
    from easy_mcp_server.businesses.hbaseqa import seed

    # 用一个 demo 数据里不存在的时刻，才能验证"写前探测"的两侧行为
    fresh_ts = T0 + 10 * MINUTE
    key, values = next(
        seed.generate_rows(TABLE_TICK, "EURUSDSP", fresh_ts, 1, MINUTE, "1N", ("MID",), "00")
    )
    assert hbase_client.row_exists(TABLE_TICK, key) is None  # 还没写
    hbase_client.put_rows(TABLE_TICK, [(key, values)])
    existing = hbase_client.row_exists(TABLE_TICK, key)
    assert existing is not None
    # _cells 的键是字符串形态的 "CF:XXX"，与 scan_table 的输出一致
    assert existing["CF:CONTRACTCODE"] == b"EURUSDSP"


def test_raw_scan_pushes_row_limit_to_the_server(monkeypatch, multiday_cluster):
    """raw 必须把行数上限下推：否则会读满整个窗口（一个月 13 万行）。

    这是"一次取数又大又慢"的真正主因——返回给模型的只有 MAX_ROWS 行，
    却把整窗数据都从 HBase 拉回来解码了一遍。
    """
    monkeypatch.setenv("HBASEQA_MAX_ROWS", "50")
    window_end = T0 + (BARS_PER_CONTRACT - 1) * HOUR_MS
    payload = fxspot.collect([TABLE_DEPTH], T0, window_end, granularity="raw")
    assert payload["row_count"] == 50
    assert payload["scanned_rows"] <= 51  # 只多读 1 行用于判断截断
    assert payload["truncated"] is True
    assert any("已达扫描上限" in note for note in payload["notes"])


def test_scan_stops_at_cap(monkeypatch, cluster):
    monkeypatch.setenv("HBASEQA_MAX_SCANNED_ROWS", "5")
    result = hbase_client.scan_table(hbase_client.get_table(TABLE_DEPTH), {"ranges": []})
    assert result["scanned"] == 5
    assert result["truncated"] is True


def test_put_rows_batches_and_reports_progress(cluster):
    """上万行是常态，必须分批且能报进度，否则看起来像卡死。"""
    from easy_mcp_server.businesses.hbaseqa import seed

    rows = list(
        seed.generate_rows(
            TABLE_TICK, "EURUSDSP", T0 + 20 * MINUTE, 5, MINUTE, "1N", ("MID",), "00"
        )
    )
    seen: list[int] = []
    written = hbase_client.put_rows(TABLE_TICK, rows, batch_size=2, on_progress=seen.append)
    assert written == 5
    assert seen == [2, 4, 5]  # 每批回调一次，最后一批可能不满
    assert cluster.calls.count("putMultiple") == 3


# ── 粒度：auto / raw / latest / hourly / daily ────────────────────────

CONTRACTS = ("EURUSDSP", "USDJPYSP")
HOUR_MS = 3_600_000
BARS_PER_CONTRACT = 72  # 3 天的小时线


@pytest.fixture
def multiday_cluster(monkeypatch):
    """3 天的小时线（用真实 rowkey 格式写入），用于验证降采样。"""
    from easy_mcp_server.businesses.hbaseqa import seed

    with stub_server({TABLE_DEPTH: {}}) as (module, handler, port):
        monkeypatch.setenv("HBASEQA_THRIFT_HOST", "127.0.0.1")
        monkeypatch.setenv("HBASEQA_THRIFT_PORT", str(port))
        hbase_client.reset_connection()
        for contract in CONTRACTS:
            rows = seed.generate_rows(
                TABLE_DEPTH, contract, T0, BARS_PER_CONTRACT, HOUR_MS, "1H", ("MID",), "00"
            )
            hbase_client.put_rows(TABLE_DEPTH, list(rows), batch_size=200)
        yield handler
        hbase_client.reset_connection()


def test_resolve_granularity_keeps_raw_for_short_windows():
    chosen, note = fxspot.resolve_granularity("auto", T0, T0 + 3 * 3_600_000)
    assert (chosen, note) == ("raw", None)


def test_resolve_granularity_coarsens_long_windows_with_a_note():
    """auto 不能静默降级：必须给出说明，模型才知道可以改用 raw。"""
    chosen, note = fxspot.resolve_granularity("auto", T0, T0 + 72 * 3_600_000)
    assert chosen == "daily"
    assert note and "自动降采样" in note and "granularity='raw'" in note


def test_explicit_granularity_is_not_coarsened(monkeypatch):
    monkeypatch.setenv("HBASEQA_AUTO_COARSEN_HOURS", "1")
    assert fxspot.resolve_granularity("raw", T0, T0 + 100 * 3_600_000) == ("raw", None)
    assert fxspot.resolve_granularity("hourly", T0, T0 + 100 * 3_600_000) == ("hourly", None)


@pytest.mark.parametrize("bad", ["weekly", "minutely", "1m"])
def test_assert_granularity_rejects_unknown(bad):
    with pytest.raises(ValueError, match="granularity"):
        fxspot.assert_granularity(bad)


def test_daily_buckets_align_to_local_midnight(monkeypatch):
    """日线指**本地日**：否则中国用户的"每天最后一根"会落在 UTC 16:00。"""
    monkeypatch.setenv("HBASEQA_TZ", "Asia/Shanghai")
    buckets = fxspot._buckets(T0, T0 + 72 * HOUR_MS, "daily")
    assert len(buckets) >= 3
    first_of_second = datetime.fromtimestamp(buckets[1][0] / 1000, tz=hbase_times.timezone_of())
    assert (first_of_second.hour, first_of_second.minute, first_of_second.second) == (0, 0, 0)
    assert buckets[0][0] == T0  # 首桶被窗口起点裁剪
    assert all(start <= end for start, end in buckets)


def test_hourly_buckets_are_utc_aligned():
    buckets = fxspot._buckets(T0, T0 + 3 * HOUR_MS, "hourly")
    # 首桶被窗口起点裁剪（T0 不是整点），其余对齐 UTC 整点
    assert buckets[0][0] == T0
    assert len(buckets) == 4
    assert all(start % HOUR_MS == 0 for start, _ in buckets[1:])


def test_daily_granularity_keeps_last_bar_of_each_local_day(multiday_cluster):
    window_end = T0 + (BARS_PER_CONTRACT - 1) * HOUR_MS
    raw = fxspot.collect([TABLE_DEPTH], T0, window_end, granularity="raw")
    daily = fxspot.collect([TABLE_DEPTH], T0, window_end, granularity="daily")

    assert daily["granularity"] == "daily"
    assert daily["summary_scope"] == "downsampled"
    assert daily["row_count"] < raw["row_count"]  # 确实降下来了

    # 日线每一行 = 该合约该本地日 时间最大的那根
    expected = {(row["CONTRACTCODE"], row["ts"][:10]): row["ts"] for row in raw["rows"]}
    actual = {(row["CONTRACTCODE"], row["ts"][:10]): row["ts"] for row in daily["rows"]}
    assert actual == expected


def test_hourly_granularity_keeps_one_bar_per_hour(multiday_cluster):
    window_end = T0 + (BARS_PER_CONTRACT - 1) * HOUR_MS
    hourly = fxspot.collect([TABLE_DEPTH], T0, window_end, granularity="hourly")
    # 数据本身就是小时线，所以每小时一根、每合约根数 = 桶数
    buckets = len(fxspot._buckets(T0, window_end, "hourly"))
    assert hourly["row_count"] == buckets * len(CONTRACTS)


def test_latest_granularity_returns_one_row_per_contract(multiday_cluster):
    window_end = T0 + (BARS_PER_CONTRACT - 1) * HOUR_MS
    latest = fxspot.collect([TABLE_DEPTH], T0, window_end, granularity="latest")
    assert latest["row_count"] == len(CONTRACTS)
    assert {row["CONTRACTCODE"] for row in latest["rows"]} == set(CONTRACTS)
    # 每合约一行，且是窗口内最后一根
    raw = fxspot.collect([TABLE_DEPTH], T0, window_end, granularity="raw")
    last_ts = {}
    for row in raw["rows"]:
        code = row["CONTRACTCODE"]
        last_ts[code] = max(last_ts.get(code, ""), row["ts"])
    assert {row["CONTRACTCODE"]: row["ts"] for row in latest["rows"]} == last_ts


def test_downsampling_does_not_claim_truncation(multiday_cluster):
    """降采样"只探桶尾"是设计，不能报成 `scan_truncated`。

    真实踩过：响应里带一个没有解释的 `scan_truncated: true`，调用方的模型就自己
    编了原因（"因正向扫描截断只到 09-12"），而实际是反向扫描、日期也完全不对。
    """
    window_end = T0 + (BARS_PER_CONTRACT - 1) * HOUR_MS
    for granularity in ("latest", "hourly", "daily"):
        payload = fxspot.collect([TABLE_DEPTH], T0, window_end, granularity=granularity)
        assert payload["scan_truncated"] is False, granularity
        assert payload["row_count"] > 0


def _one_row(contract: str, ts: int) -> tuple[str, dict[str, bytes]]:
    from easy_mcp_server.businesses.hbaseqa import seed

    return next(seed.generate_rows(TABLE_DEPTH, contract, ts, 1, MINUTE, "1N", ("MID",), "00"))


def _fake_scan(monkeypatch, rows, capped: bool = True) -> None:
    """让 client.scan_table 返回固定行；capped=True 表示"桶里还有数据没读完"。"""

    def fake_scan(handle, plan):
        return {
            "rows": list(rows),
            "scanned": plan["limit"] if capped else min(len(rows), plan["limit"]),
            "truncated": capped,
        }

    monkeypatch.setattr(fxspot.client, "scan_table", fake_scan)
    monkeypatch.setattr(fxspot.client, "get_table", lambda name: None)


def test_bucket_flags_incomplete_when_contract_missing(monkeypatch):
    """期望合约没集齐、且桶里还有数据没读 → 结果确实可能缺，必须报出来。"""
    _fake_scan(monkeypatch, [_one_row("EURUSDSP", T0)])
    _, stat = fxspot._bucket_newest(
        TABLE_DEPTH, T0, T0 + MINUTE, None, None, 50_000, {"EURUSDSP", "USDJPYSP"}
    )
    assert stat["incomplete"] is True
    assert stat["probe_capped"] is False


def test_bucket_is_not_incomplete_once_all_contracts_found(monkeypatch):
    rows = [_one_row("EURUSDSP", T0), _one_row("USDJPYSP", T0)]
    _fake_scan(monkeypatch, rows)
    _, stat = fxspot._bucket_newest(
        TABLE_DEPTH, T0, T0 + MINUTE, None, None, 50_000, {"EURUSDSP", "USDJPYSP"}
    )
    assert stat["incomplete"] is False


def test_bucket_marks_probe_capped_when_contracts_unknown(monkeypatch):
    """合约未知时只探一段，可能漏合约——但这不等于"结果不完整"，两者要分开。"""
    _fake_scan(monkeypatch, [_one_row("EURUSDSP", T0)])
    _, stat = fxspot._bucket_newest(TABLE_DEPTH, T0, T0 + MINUTE, None, None, 50_000, set())
    assert stat["probe_capped"] is True
    assert stat["incomplete"] is False


def test_probe_capped_is_explained_in_notes(monkeypatch):

    monkeypatch.setattr(
        fxspot,
        "_bucket_newest",
        lambda *args, **kwargs: (
            [],
            {"scanned": 5, "skipped": 0, "incomplete": False, "probe_capped": True},
        ),
    )
    _, _, truncated, _, notes = fxspot._collect_bucketed(
        [TABLE_DEPTH], T0, T0 + MINUTE, "latest", None, None, "asc"
    )
    assert truncated is False
    joined = "\n".join(notes)
    assert "只探了每桶末尾一段" in joined
    assert "list_contracts" in joined


def test_downsampled_response_flags_high_low_scope(multiday_cluster):
    """降采样后 high/low 不是真实极值，必须显式告知，否则模型会当成区间最高价。"""
    window_end = T0 + (BARS_PER_CONTRACT - 1) * HOUR_MS
    daily = fxspot.collect([TABLE_DEPTH], T0, window_end, granularity="daily")
    assert any("非真实极值" in note for note in daily["notes"])


def test_auto_granularity_is_reported_even_when_unchanged(cluster):
    payload = fxspot.collect([TABLE_DEPTH], T0, T0 + MINUTE)
    assert payload["granularity"] == "raw"
    assert payload["granularity_requested"] == "auto"


def test_limit_ladder_is_cheap_when_contracts_are_known():
    """探查起点决定读取量，这是"快"的关键，所以直接钉住它。"""
    # 已知 1 个合约：从 32 行起步
    assert fxspot._limit_ladder(50_000, {"EURUSDSP"})[0] == 32
    # 已知 20 个合约：按合约数放大
    assert fxspot._limit_ladder(50_000, {f"C{i}" for i in range(20)})[0] == 80
    # 未知合约：中档探测一次（不用最大档，避免解码成本被放大）
    assert fxspot._limit_ladder(50_000, set()) == [512]
    # 剩余预算会裁掉超出的大档：预算 100 时只剩 32 这一档
    assert fxspot._limit_ladder(100, {"A", "B"}) == [32]
    assert fxspot._limit_ladder(4, set()) == [4]


def test_requested_contracts_keep_bucket_probes_small(multiday_cluster):
    """指定了合约就不必"探测有哪些合约"——桶内几十行就够，这是最常见的调用形态。"""
    window_end = T0 + (BARS_PER_CONTRACT - 1) * HOUR_MS
    with_codes = fxspot.collect(
        [TABLE_DEPTH], T0, window_end, granularity="daily", contract_codes=["EURUSDSP"]
    )
    # 4 个左右的天桶 × 每桶 32 行，而不是把 144 行明细全读回来
    assert with_codes["scanned_rows"] <= 200
    assert with_codes["row_count"] == 4  # 每本地日最后一根
    assert {row["CONTRACTCODE"] for row in with_codes["rows"]} == {"EURUSDSP"}


def test_unknown_contracts_emit_a_discovery_note(multiday_cluster):
    window_end = T0 + (BARS_PER_CONTRACT - 1) * HOUR_MS
    payload = fxspot.collect([TABLE_DEPTH], T0, window_end, granularity="daily")
    assert any("未指定 contract_codes" in note for note in payload["notes"])


def test_contract_prefix_match_satisfies_expectation():
    """模型说 EURUSD、库里是 EURUSDSP 也算命中，否则每个桶都会白放大重扫。"""
    picked = {"EURUSDSP": {"CONTRACTCODE": "EURUSDSP"}}
    assert fxspot._expected_satisfied({"EURUSD"}, picked) is True
    assert fxspot._expected_satisfied({"EURUSD", "USDJPY"}, picked) is False
    assert fxspot._expected_satisfied(set(), picked) is False


def test_contract_without_data_in_window_is_reported(multiday_cluster):
    """静默返回空比报错更糟：模型会以为"这段时间没行情"。"""
    window_end = T0 + (BARS_PER_CONTRACT - 1) * HOUR_MS
    payload = fxspot.collect(
        [TABLE_DEPTH], T0, window_end, granularity="daily", contract_codes=["GBPUSDSP"]
    )
    assert payload["row_count"] == 0
    assert any("GBPUSDSP" in note and "没有数据" in note for note in payload["notes"])


def test_structured_contract_summary(multiday_cluster):
    """每合约一行的结构化摘要——模型先看这个就够答"涨了还是跌了"。"""
    window_end = T0 + (BARS_PER_CONTRACT - 1) * HOUR_MS
    payload = fxspot.collect([TABLE_DEPTH], T0, window_end, granularity="daily")
    assert [item["contract"] for item in payload["contracts"]] == sorted(CONTRACTS)
    for item in payload["contracts"]:
        assert item["bars"] == payload["row_count"] // len(CONTRACTS)
        assert item["first_close"] and item["last_close"]
        assert item["change_pct"] is not None
        assert item["high"] >= item["low"]
        assert item["buy_sells"] == ["MID"]


# ── list_contracts 的扫描预算与截断暴露 ───────────────────────────────


def test_list_contracts_reports_scan_truncation(monkeypatch, cluster):
    """预算用尽时必须说出来：否则调用方以为拿到的是完整合约清单。"""
    monkeypatch.setenv("HBASEQA_CONTRACT_SCAN_BUDGET", "3")
    payload = fxspot.collect_contracts([TABLE_DEPTH], T0, T0 + 12 * MINUTE)
    assert payload["scanned_rows"] == 3
    assert payload["scan_truncated"] is True
    assert any("预算" in note for note in payload["notes"])


def test_list_contracts_is_not_truncated_within_budget(cluster):
    payload = fxspot.collect_contracts([TABLE_DEPTH], T0, T0 + 12 * MINUTE)
    assert payload["scan_truncated"] is False
    assert payload["notes"] == []
    assert payload["row_count"] == 2  # EURUSDSP / USDJPYSP


def test_list_contracts_budget_is_configurable(monkeypatch):
    monkeypatch.setenv("HBASEQA_CONTRACT_SCAN_BUDGET", "123")
    assert hbase_config.contract_scan_budget() == 123


async def test_get_fx_bars_reports_granularity(hbase_env):
    result = await _call_tool(
        hbase_env, "hbase-alice", "get_fx_bars", {"start": T0, "end": T0 + MINUTE}
    )
    text = result.content[0].text
    assert '"granularity": "raw"' in text
    assert '"contracts"' in text


async def test_invalid_granularity_is_reported(hbase_env):
    result = await _call_tool(
        hbase_env, "hbase-alice", "get_fx_bars", {"granularity": "weekly", "start": T0}
    )
    text = result.content[0].text
    assert "false" in text.lower()
    assert "granularity" in text


def test_time_budget_still_returns_the_first_batch(monkeypatch, cluster):
    """时间预算到点也必须**带着已读到的行**返回——空手而归比慢更糟。"""
    monkeypatch.setattr(fxspot.config, "scan_budget_ms", lambda: 0)
    payload = fxspot.collect([TABLE_DEPTH], T0, T0 + 12 * MINUTE, granularity="raw")
    assert payload["row_count"] > 0
    assert any("时间预算" in note for note in payload["notes"])


def test_time_budget_skips_the_remaining_buckets(monkeypatch, multiday_cluster):
    """降采样时到点就交：已经拿到的桶照常返回，后面的桶跳过并说明。"""
    monkeypatch.setattr(fxspot.config, "scan_budget_ms", lambda: 0)
    window_end = T0 + (BARS_PER_CONTRACT - 1) * HOUR_MS
    payload = fxspot.collect([TABLE_DEPTH], T0, window_end, granularity="daily")
    assert payload["row_count"] > 0
    assert payload["scan_truncated"] is True
    assert any("后续桶" in note for note in payload["notes"])
