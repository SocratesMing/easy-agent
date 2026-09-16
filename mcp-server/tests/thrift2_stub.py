"""内存版 HBase Thrift2 服务端（stub）。

用 `thriftpy2.make_server` 起一个**真实的 Thrift 服务**，讲的是与生产同一份 IDL
（`businesses/hbaseqa/idl/hbase_thrift2.thrift`）。所以用它做测试能验证到真实链路：

- IDL 声明与客户端调用是否匹配（方法名、字段 ID、类型）
- scan 计划里的 startRow / stopRow / columns / reversed / limit 是否被正确传递
- 异常如何跨进程传播（handler 抛 TIOError → 客户端抛出）

比起用手写假对象 mock 掉 `client`，这层验证更接近生产：唯一没覆盖的就是
HBase 自己的行为。
"""

from __future__ import annotations

import re
import socket
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import thriftpy2
from thriftpy2.rpc import make_server

from easy_mcp_server.businesses.hbaseqa import rowkey as rowkey_fmt

IDL_PATH = (
    Path(__file__).resolve().parent.parent
    / "easy_mcp_server"
    / "businesses"
    / "hbaseqa"
    / "idl"
    / "hbase_thrift2.thrift"
)

# thriftpy2 生成模块名必须以 _thrift 结尾，且同一进程内重复 load 会有副作用，故缓存
_MOD: Any = None

CF = "CF"


def load_idl() -> Any:
    global _MOD
    if _MOD is None:
        _MOD = thriftpy2.load(str(IDL_PATH), module_name="hbase_thrift2_thrift")
    return _MOD


def cells(**overrides: Any) -> dict[bytes, bytes]:
    """构造一行的限定符 → 值（bytes 形态，与真实 Thrift 返回一致）。"""
    row = {
        "CHANNEL": "UBS",
        "DATATYPESTR": "BAR_DEPTH",
        "CONTRACTCODE": "EURUSDSP",
        "TIME": 1677583200000,
        "SYSTIME": 1677583200123,
        "FREQUENCY": "1N",
        "OPEN": 1.0856,
        "CLOSE": 1.0860,
        "HIGH": 1.0862,
        "LOW": 1.0854,
        "TRADEVOLUME": 1000000,
        "TRADEAMT": 0,
        "STARTTIMESTAMP": 1677583200000,
        "ENDTIMESTAMP": 1677583260000,
        "INSTRUMENT": "FXSPOT",
        "BUYSELL": "MID",
    }
    row.update(overrides)
    return {f"{CF}:{k}".encode(): str(v).encode() for k, v in row.items()}


MINUTE = 60_000
T0 = 1677583200000
TABLE_DEPTH = "HSDC_HDS2_UBS_FXSPOT_BAR_DEPTH"
TABLE_TICK = "HSDC_HDS2_UBS_FXSPOT_TICK"
TABLE_CITI = "HSDC_HDS2_CITI_FXSPOT_BAR"


def row_key(
    ts: int, contract: str, side: str = "MID", region: str = "00", frequency: str = "1N"
) -> bytes:
    """真实格式的 rowkey（`region+time+hash+frequency+side`）的 bytes 形态。

    stub 必须用真实格式：如果这里图省事用纯毫秒，那 region 前缀的范围扫描测试
    就是"假通过"——数据与查询条件都错，正好互相抵消。
    """
    return rowkey_fmt.build(ts, contract, side, region=region, frequency=frequency).encode()


def demo_tables() -> dict[str, dict[bytes, dict[bytes, bytes]]]:
    """三张业务表：EURUSD 12 根 1 分钟 K 线 + USDJPY 半分钟错位的 12 根。"""
    depth: dict[bytes, dict[bytes, bytes]] = {}
    for i in range(12):
        ts = T0 + i * MINUTE
        depth[row_key(ts, "EURUSDSP")] = cells(
            TIME=ts, STARTTIMESTAMP=ts, ENDTIMESTAMP=ts + MINUTE, CONTRACTCODE="EURUSDSP"
        )
        offset = ts + 30_000
        depth[row_key(offset, "USDJPYSP")] = cells(
            TIME=offset, STARTTIMESTAMP=offset, ENDTIMESTAMP=ts + MINUTE,
            CONTRACTCODE="USDJPYSP", OPEN=136.2, CLOSE=136.25, HIGH=136.3, LOW=136.1,
        )
    return {
        TABLE_DEPTH: depth,
        TABLE_TICK: {
            row_key(T0, "EURUSDSP"): cells(
                TIME=T0, CONTRACTCODE="EURUSDSP", DATATYPESTR="TICK"
            ),
            row_key(T0 + 1, "EURUSDSP"): cells(
                TIME=T0 + 1, CONTRACTCODE="EURUSDSP", DATATYPESTR="TICK"
            ),
        },
        TABLE_CITI: {
            row_key(T0, "EURUSDSP"): cells(
                TIME=T0, CONTRACTCODE="EURUSDSP", CHANNEL="CITI"
            )
        },
    }


class StubHandler:
    """实现本业务用到的那 7 个 Thrift2 方法。

    scan 语义对齐 HBase：startRow 含、stopRow 不含、reversed 反转、limit 截断。
    """

    def __init__(self, tables: dict[str, dict[bytes, dict[bytes, bytes]]]):
        self.mod = load_idl()
        self.tables = tables
        self.scanners: dict[int, list[Any]] = {}
        self.next_scanner = 1
        self.calls: list[str] = []

    # ── 表 ──────────────────────────────────────────────────────────

    def getTableNamesByPattern(self, regex: str, includeSysTables: bool):
        self.calls.append("getTableNamesByPattern")
        names = [n for n in self.tables if re.fullmatch(regex, n)]
        return [self.mod.TTableName(qualifier=n.encode()) for n in sorted(names)]

    def getTableDescriptor(self, table: Any):
        self.calls.append("getTableDescriptor")
        name = _decode(table.qualifier)
        if name not in self.tables:
            raise self.mod.TIOError(message=f"TableNotFoundException: {name}", canRetry=False)
        return self._descriptor(name)

    def getTableDescriptors(self, tables: list[Any]):
        self.calls.append("getTableDescriptors")
        return [self._descriptor(_decode(t.qualifier)) for t in tables]

    def _descriptor(self, name: str):
        return self.mod.TTableDescriptor(
            tableName=self.mod.TTableName(qualifier=name.encode()),
            columns=[self.mod.TColumnFamilyDescriptor(name=CF.encode())],
        )

    def getThriftServerType(self) -> int:
        self.calls.append("getThriftServerType")
        return 1  # TThriftServerType.TWO

    # ── Scanner ────────────────────────────────────────────────────

    def openScanner(self, table: bytes, tscan: Any) -> int:
        self.calls.append("openScanner")
        name = _decode(table)
        if name not in self.tables:
            raise self.mod.TIOError(message=f"TableNotFoundException: {name}", canRetry=False)
        rows = self._select(name, tscan)
        scanner_id = self.next_scanner
        self.next_scanner += 1
        self.scanners[scanner_id] = rows
        return scanner_id

    def getScannerRows(self, scannerId: int, numRows: int):
        self.calls.append("getScannerRows")
        pending = self.scanners.get(scannerId)
        if pending is None:
            raise self.mod.TIllegalArgument(message=f"Scanner {scannerId} not found")
        batch, self.scanners[scannerId] = pending[:numRows], pending[numRows:]
        return batch

    def closeScanner(self, scannerId: int) -> None:
        self.calls.append("closeScanner")
        self.scanners.pop(scannerId, None)

    # ── 写入（仅供 seed 脚本链路验证） ──────────────────────────────

    def put(self, table: bytes, tput: Any) -> None:
        self.calls.append("put")
        name = _decode(table)
        if name not in self.tables:
            raise self.mod.TIOError(message=f"TableNotFoundException: {name}", canRetry=False)
        self.tables[name][tput.row] = {
            f"{_decode(cv.family)}:{_decode(cv.qualifier)}".encode(): cv.value
            for cv in (tput.columnValues or [])
        }

    def putMultiple(self, table: bytes, tputs: list[Any]) -> None:
        self.calls.append("putMultiple")
        for tput in tputs:
            self.put(table, tput)

    def _select(self, table: str, tscan: Any) -> list[Any]:
        rows = sorted(self.tables[table].items())
        start = getattr(tscan, "startRow", None)
        stop = getattr(tscan, "stopRow", None)
        if start:
            rows = [(k, v) for k, v in rows if k >= start]
        if stop:
            rows = [(k, v) for k, v in rows if k < stop]
        if getattr(tscan, "reversed", None):
            rows = list(reversed(rows))
        limit = getattr(tscan, "limit", None)
        if limit:
            rows = rows[:limit]

        wanted = None
        if getattr(tscan, "columns", None):
            wanted = set()
            for column in tscan.columns:
                family = _decode(column.family)
                qualifier = getattr(column, "qualifier", None)
                wanted.add(f"{family}:{_decode(qualifier)}" if qualifier else f"{family}:")
        return [self._result(rowkey, values, wanted) for rowkey, values in rows]

    def _result(self, rowkey: bytes, values: dict[bytes, bytes], wanted: set[str] | None):
        columns = []
        for qualifier, value in sorted(values.items()):
            text = _decode(qualifier)
            if wanted is not None and text not in wanted and not any(
                w.endswith(":") and text.startswith(w) for w in wanted
            ):
                continue
            family, _, short = text.partition(":")
            columns.append(
                self.mod.TColumnValue(
                    family=family.encode(), qualifier=short.encode(), value=value
                )
            )
        return self.mod.TResult(row=rowkey, columnValues=columns)


def _decode(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def stub_server(
    tables: dict[str, dict[bytes, dict[bytes, bytes]]] | None = None,
) -> Iterator[tuple[Any, StubHandler, int]]:
    """起一个本地 stub 服务，yield ``(thrift 模块, handler, port)``。"""
    mod = load_idl()
    handler = StubHandler(tables if tables is not None else demo_tables())
    port = free_port()
    server = make_server(mod.THBaseService, handler, host="127.0.0.1", port=port)
    thread = threading.Thread(target=server.serve, daemon=True, name="thrift2-stub")
    thread.start()
    try:
        yield mod, handler, port
    finally:
        try:
            server.close()
        except Exception:  # pragma: no cover - 关闭失败不影响测试结论
            pass
