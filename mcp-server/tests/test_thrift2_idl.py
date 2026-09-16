"""Thrift2 协议层自检：我们的 IDL 声明能否与真实 Thrift 服务互通。

这层刻意不用 mock：stub 是**真的** Thrift 服务端（thriftpy2.make_server），
客户端通过真实 socket 调用。所以它能验证到手写假对象验证不到的东西——
方法名、字段 ID、类型是否与协议一致。唯一没覆盖的只有 HBase 自身的行为。
"""

from __future__ import annotations

import pytest
from thriftpy2.rpc import make_client

from easy_mcp_server.businesses.hbaseqa import rowkey as rowkey_fmt
from thrift2_stub import TABLE_DEPTH, T0, load_idl, row_key, stub_server


def test_idl_declares_thrift2_method_names():
    """防回归：IDL 必须是 Thrift2 的方法名。

    曾经踩的坑：用 happybase（Thrift1）去连 Thrrift2 服务，报
    `Invalid method name: 'getTableNames'`。Thrift2 里根本不存在
    getTableNames / scannerOpenWithScan / scannerGetList / scannerClose。
    """
    mod = load_idl()
    service = mod.THBaseService
    names = {name.rsplit("_args", 1)[0] for name in dir(service) if name.endswith("_args")}

    assert {"getTableNamesByPattern", "openScanner", "getScannerRows", "closeScanner"} <= names
    # Thrift1 的方法名一个都不该出现
    for thrift1_only in ("getTableNames", "scannerOpenWithScan", "scannerOpen", "scannerGetList"):
        assert thrift1_only not in names


def test_list_tables_round_trip():
    with stub_server() as (mod, handler, port):
        client = make_client(mod.THBaseService, "127.0.0.1", port, timeout=10_000)
        try:
            names = client.getTableNamesByPattern(regex=".*", includeSysTables=False)
            assert [n.qualifier.decode() for n in names] == sorted(handler.tables)
            assert client.getThriftServerType() == 1  # TWO
        finally:
            client.close()


def test_scan_plan_fields_reach_the_server():
    """startRow / stopRow / columns / limit 必须真的传到服务端。

    注意 startRow 必须是 `region + 毫秒`：真实 rowkey 以 region 开头，直接用毫秒会
    一条都扫不到（静默返回空）。
    """
    with stub_server() as (mod, handler, port):
        client = make_client(mod.THBaseService, "127.0.0.1", port, timeout=10_000)
        try:
            # demo 数据步长：EURUSD 每 60s 一根、USDJPY 错位 30s
            scan = mod.TScan(
                startRow=rowkey_fmt.time_prefix("00", T0).encode(),
                stopRow=rowkey_fmt.time_prefix("00", T0 + 61_000).encode(),
                columns=[mod.TColumn(family=b"CF", qualifier=b"OPEN")],
                limit=2,
            )
            scanner = client.openScanner(TABLE_DEPTH.encode(), scan)
            rows = client.getScannerRows(scanner, 1000)
            client.closeScanner(scanner)

            assert len(rows) == 2  # limit 生效
            assert [rowkey_fmt.parse(r.row.decode())["ts_ms"] for r in rows] == [
                T0,
                T0 + 30_000,
            ]
            # columns 生效：只回 OPEN 一个限定符
            assert [(c.family, c.qualifier) for c in rows[0].columnValues] == [(b"CF", b"OPEN")]
            assert handler.calls[:2] == ["openScanner", "getScannerRows"]
        finally:
            client.close()


def test_plain_timestamp_range_would_find_nothing():
    """反证：拿毫秒直接当 startRow 扫不到任何东西——这正是必须拼 region 的原因。"""
    with stub_server() as (mod, handler, port):
        client = make_client(mod.THBaseService, "127.0.0.1", port, timeout=10_000)
        try:
            scan = mod.TScan(startRow=str(T0).encode(), stopRow=str(T0 + 61_000).encode())
            scanner = client.openScanner(TABLE_DEPTH.encode(), scan)
            rows = client.getScannerRows(scanner, 1000)
            client.closeScanner(scanner)
            assert rows == []
        finally:
            client.close()


def test_reversed_scan_returns_newest_first():
    """Thrift2 的 TScan.reversed 原生支持反向扫描（Thrift1 没有，要靠驱动探测）。"""
    with stub_server() as (mod, handler, port):
        client = make_client(mod.THBaseService, "127.0.0.1", port, timeout=10_000)
        try:
            scan = mod.TScan(reversed=True, limit=3)
            scanner = client.openScanner(TABLE_DEPTH.encode(), scan)
            rows = client.getScannerRows(scanner, 1000)
            client.closeScanner(scanner)
            order = [r.row.decode() for r in rows]
            assert order == sorted(order, reverse=True)
        finally:
            client.close()


def test_put_then_scan_round_trip():
    """写路径（只给 seed 脚本用）也要能通过真实协议验一遍。"""
    with stub_server() as (mod, handler, port):
        client = make_client(mod.THBaseService, "127.0.0.1", port, timeout=10_000)
        try:
            key = row_key(T0, "EURUSDSP", side="MID")
            client.put(
                TABLE_DEPTH.encode(),
                mod.TPut(
                    row=key,
                    columnValues=[
                        mod.TColumnValue(family=b"CF", qualifier=b"OPEN", value=b"1.2000"),
                        mod.TColumnValue(family=b"CF", qualifier=b"BUYSELL", value=b"MID"),
                    ],
                ),
            )
            assert handler.tables[TABLE_DEPTH][key][b"CF:OPEN"] == b"1.2000"
            assert handler.calls[-1] == "put"
        finally:
            client.close()


def test_table_descriptor_exposes_column_families():
    with stub_server() as (mod, handler, port):
        client = make_client(mod.THBaseService, "127.0.0.1", port, timeout=10_000)
        try:
            desc = client.getTableDescriptor(mod.TTableName(qualifier=TABLE_DEPTH.encode()))
            assert desc.tableName.qualifier.decode() == TABLE_DEPTH
            assert [c.name.decode() for c in desc.columns] == ["CF"]
        finally:
            client.close()


def test_server_side_error_propagates_as_tio_error():
    """表不存在时服务端抛 TIOError，客户端必须能拿到 message（而不是裸 socket 错）。"""
    with stub_server() as (mod, handler, port):
        client = make_client(mod.THBaseService, "127.0.0.1", port, timeout=10_000)
        try:
            with pytest.raises(mod.TIOError) as excinfo:
                client.getTableDescriptor(mod.TTableName(qualifier=b"NO_SUCH_TABLE"))
            assert "TableNotFoundException" in excinfo.value.message
        finally:
            client.close()
