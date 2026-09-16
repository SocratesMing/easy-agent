"""HBase Thrift2 通道：连接获取、只读 scan 与 Kerberos 预留。

## 为什么直接讲 Thrift2

HBase 有**两个互不兼容**的 Thrift 服务。happybase（本业务最初的选择）走 Thrift1 的
`Hbase` 服务（`getTableNames()` / `scannerOpenWithScan`），而 `hbase thrift2 start`
起的是 Thrift2 的 `THBaseService`，方法名完全不同。混用的结果是一句毫无提示性的：

    TApplicationException: Invalid method name: 'getTableNames'

本机只有 Thrift2，所以这里直接讲 Thrift2。IDL 见 `idl/hbase_thrift2.thrift`
（只声明用到的子集，字段 ID 逐字取自 HBase 上游），用 thriftpy2 **运行时加载** ——
不需要 thrift 编译器，也不用 vendor 大段生成代码。

顺带拿到两个 Thrift1 没有的能力：

- `TScan.reversed`：原生反向扫描（原来要探测 happybase 是否支持 `reverse=`）
- `TScan.limit`：服务端行数上限（原来 happybase 的 limit 只是客户端计数）

## 输出契约保持不变

`scan_table` 仍然返回 ``[(rowkey_text, {"CF:OPEN": b"1.0856"}), ...]``——
与 happybase 时代完全一致，所以 `metadata` / `fxspot` / `guard` 一行都不用改。

## 三条硬约束

1. **thriftpy2 是硬依赖但不预加载 IDL**：IDL 解析有成本，首次真正查询时才做，
   避免拖慢 MCP Server 启动（`app.py` 会 import 所有业务包）
2. **连接复用**：进程内单例
3. **长连接会失效**：Thrift2 Server 也会回收空闲连接，:func:`run_with_retry`
   在连接类错误上重置连接重试一次

Kerberos：当前未启用，配置已预留（`HBASEQA_KERBEROS_*`），启用点在
:func:`_open_client`（换成 SASL/SSL 传输），上层调用点不用改。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Iterable, TypeVar

from . import config, guard

logger = logging.getLogger("easy-mcp-server")

T = TypeVar("T")

IDL_PATH = Path(__file__).resolve().parent / "idl" / "hbase_thrift2.thrift"
# thriftpy2 要求生成的模块名以 _thrift 结尾
IDL_MODULE_NAME = "hbase_thrift2_thrift"
# 一次 getScannerRows 取多少行（HBase 的 Scan.caching 语义）。
# 实际取值走 config.scan_batch_rows()：RPC 次数是扫描耗时的大头，必须可调。

_client: Any = None
_idl_module: Any = None

# 判断"通道断了"的 Windows socket 错误码
_CONNECTION_WINERRORS = frozenset({10048, 10053, 10054, 10057, 10060, 10061})
# thrift 的传输/协议异常按类名识别，避免为此 import 具体模块
_TRANSPORT_EXCEPTION_NAMES = frozenset(
    {"TTransportException", "TProtocolException", "TSocketException", "TTimedOutException"}
)
# Thrift1 独有的方法名，用于反向诊断（见 thrift_version_hint）
_THRIFT1_METHODS = (
    "getTableNames",
    "scannerOpenWithScan",
    "scannerGetList",
    "scannerClose",
)
# 本模块使用的 Thrift2 方法名
_THRIFT2_METHODS = (
    "getTableNamesByPattern",
    "openScanner",
    "getScannerRows",
    "closeScanner",
)

KERBEROS_HINT = (
    "Kerberos 认证尚未启用。配置已预留 HBASEQA_KERBEROS_*；接入时需在 "
    "easy_mcp_server/businesses/hbaseqa/client.py::_open_client 中把传输换成 "
    "SASL/SSL 传输（thriftpy2 的 TSSLSocket / thrift_sasl），上层无需改动。"
)


class TableHandle:
    """表句柄：只有名字。

    Thrift2 的 scan 只需要表名（不像 happybase 需要 per-table 的 client 对象），
    所以这里刻意不做任何 I/O —— 它在循环里被频繁创建。
    """

    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:  # pragma: no cover - 仅用于日志/调试
        return f"TableHandle({self.name!r})"


# ── IDL 与连接 ────────────────────────────────────────────────────────


def load_idl() -> Any:
    """加载 Thrift2 IDL（进程内只解析一次）。"""
    global _idl_module
    if _idl_module is None:
        import thriftpy2

        logger.info(f"[hbaseqa] 加载 Thrift2 IDL | {IDL_PATH}")
        _idl_module = thriftpy2.load(str(IDL_PATH), module_name=IDL_MODULE_NAME)
    return _idl_module


def _assert_kerberos_ready() -> None:
    """Kerberos 开关已开但未实现时显式报错（fail closed，不静默走明文）。"""
    if not config.kerberos_enabled():
        return
    logger.warning(f"[hbaseqa] Kerberos 已开启但传输未接入 | {config.kerberos_config()}")
    raise RuntimeError(KERBEROS_HINT)


def reset_connection() -> None:
    """丢弃当前连接（重试/测试用），下次调用重新握手。"""
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception:  # pragma: no cover - 关闭失败不影响后续重连
            pass
    _client = None


def get_client() -> Any:
    """返回进程内共享的 Thrift2 客户端。"""
    global _client
    if _client is None:
        _client = _open_client()
    return _client


def _transport_factory() -> Any:
    from thriftpy2.transport import TBufferedTransportFactory, TFramedTransportFactory

    if config.thrift_transport() == "framed":
        return TFramedTransportFactory()
    return TBufferedTransportFactory()


def _protocol_factory() -> Any:
    from thriftpy2.protocol import TBinaryProtocolFactory, TCompactProtocolFactory

    if config.thrift_protocol() == "compact":
        return TCompactProtocolFactory()
    return TBinaryProtocolFactory()


def _open_client() -> Any:
    """建立到 HBase Thrift2 Server 的连接。

    失败时的错误文案必须可操作：连不上 Thrift2 的原因基本只有三类
    （服务没起 / 端口写成了 ZooKeeper 或 Thrift1 / 传输协议不匹配）。
    """
    from thriftpy2.rpc import make_client

    _assert_kerberos_ready()

    module = load_idl()
    host = config.thrift_host()
    port = config.thrift_port()
    timeout = config.timeout_ms()
    transport = config.thrift_transport()
    protocol = config.thrift_protocol()
    logger.info(
        f"[hbaseqa] 连接 HBase Thrift2 | host: {host} | port: {port} | "
        f"transport: {transport} | protocol: {protocol} | timeout: {timeout}ms"
    )

    try:
        client = make_client(
            module.THBaseService,
            host=host,
            port=port,
            timeout=timeout,
            trans_factory=_transport_factory(),
            proto_factory=_protocol_factory(),
        )
        # 主动探活：把"服务不可用"暴露在建连这一步，而不是留给第一次业务查询
        client.getThriftServerType()
        return client
    except Exception as e:
        detail = guard.exception_text(e)
        logger.error(
            f"[hbaseqa] 连接 Thrift2 失败 | host: {host} | port: {port} | "
            f"{type(e).__name__}: {detail}"
        )
        raise RuntimeError(
            f"连接 HBase Thrift2 失败（{host}:{port}）：{detail}。"
            "请依次确认：①服务已启动（`hbase thrift2 start`）；"
            "②端口是 Thrift2 的端口，不是 ZooKeeper(2181) 也不是 Thrift1；"
            f"③传输/协议与服务端一致（当前 transport={transport}, protocol={protocol}，"
            "可试 HBASEQA_THRIFT_TRANSPORT=framed）；④防火墙/安全组放行"
        ) from e


# ── 错误分类与重试 ────────────────────────────────────────────────────


def is_connection_error(exc: BaseException) -> bool:
    """判断异常是否属于"通道没了"（可重连重试），而不是参数/业务错误。

    沿 ``__cause__`` / ``__context__`` 链走一遍：thriftpy2 会把底层异常包一层，
    只看最外层容易漏判。
    """
    for err in _exception_chain(exc):
        if isinstance(err, (ConnectionError, TimeoutError)):
            # WSAECONNABORTED(10053)→ConnectionAbortedError，10054→ConnectionResetError
            return True
        if isinstance(err, OSError):
            if getattr(err, "winerror", None) in _CONNECTION_WINERRORS:
                return True
            if err.args and err.args[0] in _CONNECTION_WINERRORS:
                return True
        if type(err).__name__ in _TRANSPORT_EXCEPTION_NAMES:
            return True
        # Thrift2 的 TIOError 自带 canRetry，服务端明确说了可重试就重试
        if getattr(err, "canRetry", False):
            return True
    return False


def _exception_chain(exc: BaseException):
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def run_with_retry(task: Callable[[], T]) -> T:
    """执行一次通道操作；遇连接类错误则重置连接并重试。

    只重试连接类错误：业务/参数错误（ValueError 等）直接抛，重试没意义。
    重试前必须 :func:`reset_connection` —— 单例里那个 socket 已经废了。
    """
    attempts = max(1, config.connect_attempts())
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return task()
        except Exception as e:
            if not is_connection_error(e):
                raise
            last = e
            if attempt >= attempts:
                break
            logger.warning(
                f"[hbaseqa] Thrift 通道异常，重置连接后重试"
                f"（{attempt}/{attempts - 1}）: {guard.exception_text(e)}"
            )
            reset_connection()
    assert last is not None  # 只可能是上面 break 出来的
    raise last


def thrift_version_hint(exc: BaseException) -> str | None:
    """识别"连错了 Thrift 版本的另一个服务"，返回可操作提示；不是则 None。

    Thrift1（`Hbase`）与 Thrift2（`THBaseService`）的服务端在收到对方的方法名时，
    都会回 `TApplicationException(UNKNOWN_METHOD, "Invalid method name: 'xxx'")`。
    这个报错对使用者零提示性，所以按方法名反推该用哪个服务。
    """
    text = guard.exception_text(exc)
    if "Invalid method name" not in text and "UNKNOWN_METHOD" not in text:
        return None

    if any(name in text for name in _THRIFT2_METHODS):
        return (
            "服务端认不出 Thrift2 的方法名，说明它跑的是 **Thrift1**（`Hbase` 服务），"
            "而本业务现在讲的是 Thrift2（`THBaseService`）。"
            "请把服务端换成 Thrift2：`hbase thrift2 start`"
        )
    if any(name in text for name in _THRIFT1_METHODS):
        return (
            "服务端认不出这些方法名，说明它跑的是 **Thrift2**（`THBaseService`），"
            "而调用方用的是 Thrift1 API。本业务已改为 Thrift2，"
            "请确认没有回退到 happybase 的调用路径"
        )
    return (
        "服务端不认识的 Thrift 方法名。请确认 HBASEQA_THRIFT_HOST/PORT 指向的是 "
        "HBase 的 Thrift 服务（不是 ZooKeeper / HMaster）"
    )


# ── 表与元数据 ────────────────────────────────────────────────────────


def table_names() -> list[str]:
    """集群上已存在的表名列表（原始表名，未做可见性过滤）。

    带命名空间的表返回 `ns:qualifier`；`default` / 空命名空间只返回 qualifier，
    这样既能去重也能直接喂给后续的 `assert_table_name`。
    """
    names = run_with_retry(
        lambda: get_client().getTableNamesByPattern(regex=".*", includeSysTables=False)
    )
    return [_format_table_name(item) for item in names]


def _format_table_name(item: Any) -> str:
    qualifier = guard.as_text(getattr(item, "qualifier", None))
    namespace = guard.as_text(getattr(item, "ns", None))
    if namespace and namespace != "default":
        return f"{namespace}:{qualifier}"
    return qualifier


def get_table(name: str) -> TableHandle:
    """取表句柄，表名先过可见性网关。不做 I/O。"""
    checked = guard.assert_table_name(name)
    if not guard.is_visible(checked):
        raise ValueError(f"表不可查询: {name}")
    return TableHandle(checked)


def families_of(table: str) -> list[str]:
    """列族列表。

    `getTableDescriptor` 属于可选能力，失败不该让 describe_table 整个挂掉；
    但**连接类错误必须往外抛**，否则通道断了也会"成功"返回降级数据，把故障藏起来。
    """
    module = load_idl()
    checked = guard.assert_table_name(table)

    def _fetch() -> Any:
        return get_client().getTableDescriptor(module.TTableName(qualifier=checked.encode()))

    descriptor = run_with_retry(_fetch)
    return [guard.as_text(column.name) for column in (descriptor.columns or [])]


def column_filters(family: str, names: list[str]) -> list[str]:
    """构造 scan 的 columns 参数：`["CF:OPEN", ...]`（保持 happybase 时代的契约）。"""
    checked = guard.assert_family(family)
    return [f"{checked}:{guard.assert_literal(name, '限定符')}" for name in names]


# ── 扫描 ──────────────────────────────────────────────────────────────


def supports_reverse_scan(table: Any) -> bool:
    """Thrift2 的 `TScan.reversed` 原生支持反向扫描。

    保留这个函数是为了不动上层调用点（happybase 时代需要探测驱动能力）。
    """
    return config.reverse_scan_enabled()


def scan_table(table: Any, plan: dict[str, Any]) -> dict[str, Any]:
    """按 plan 执行只读 scan，返回 ``{"rows", "scanned", "truncated", "timed_out"}``。

    行数上限有两道：

    - `plan["limit"]`：调用方要多少（如 sample_rows 只要几行）
    - `max_scanned_rows()`：兜底硬止损

    另有时间止损 `plan["deadline"]`（`time.monotonic()` 口径）：行数预算挡的是
    内存，挡不住耗时，而调用方（MCP 客户端）等响应头是**有超时**的。到点就返回
    已读到的行并置 `timed_out=True`，由上层在 notes 里说明，绝不干耗到被掐断。

    两者取小后**下推到服务端**（`TScan.limit`）。为了判断是否被截断，
    真实 limit 取 `cap + 1`：能拿到第 cap+1 行就说明后面还有数据。
    服务端若不支持该字段会忽略它，此时退化为客户端截断，结论依然正确。

    `plan["ranges"]` 是 rowkey 范围列表：真实 rowkey 以 region 开头，所以时间窗
    要按 region 拆成多段扫（见 `guard.row_ranges`）。空/缺省表示不做 rowkey 过滤。
    """
    module = load_idl()
    asked = plan.get("limit")
    cap = config.max_scanned_rows()
    if isinstance(asked, int) and asked > 0:
        cap = min(cap, asked)

    table_name = getattr(table, "name", None) or str(table)
    ranges: list[tuple[Any, Any]] = list(plan.get("ranges") or [(None, None)])
    columns = _columns(module, plan.get("columns"))
    filter_string = _to_bytes(plan.get("filter"))
    reverse = bool(plan.get("reverse"))

    deadline = plan.get("deadline")
    batch_limit = config.scan_batch_rows()

    rows: list[tuple[str, dict[str, Any]]] = []
    truncated = False
    timed_out = False
    for row_start, row_stop in ranges:
        remaining = cap - len(rows)
        if remaining <= 0:
            truncated = True
            break
        scan = module.TScan(
            startRow=_to_bytes(row_start),
            stopRow=_to_bytes(row_stop),
            columns=columns,
            filterString=filter_string,
            reversed=reverse,
            limit=remaining + 1,
            caching=batch_limit,
        )
        logger.debug(
            f"[hbaseqa] openScanner | 表: {table_name} | 范围: {row_start} ~ {row_stop} | "
            f"reversed: {reverse}"
        )
        batch_rows, hit_cap, hit_deadline = _scan_range(
            table_name, scan, remaining, deadline, batch_limit
        )
        rows.extend(batch_rows)
        truncated = truncated or hit_cap or hit_deadline
        timed_out = timed_out or hit_deadline
        if hit_deadline:
            break

    logger.debug(
        f"[hbaseqa] scan 完成 | 表: {table_name} | 行数: {len(rows)} | "
        f"截断: {truncated} | 时间预算用尽: {timed_out}"
    )
    return {
        "rows": rows,
        "scanned": len(rows),
        "truncated": truncated,
        "timed_out": timed_out,
    }


def _scan_range(
    table_name: str,
    scan: Any,
    cap: int,
    deadline: float | None = None,
    batch_rows: int | None = None,
) -> tuple[list[tuple[str, dict[str, Any]]], bool, bool]:
    """扫一个 rowkey 范围，最多取 cap 行（问服务端要 cap+1 以判断是否还有）。

    `deadline`（`time.monotonic()` 口径）到点即停，返回已读到的行并标记
    `timed_out`。**第一批总是放行**，否则预算设得过小时会一行都拿不到。
    """
    take = batch_rows or config.scan_batch_rows()
    scanner_id = run_with_retry(lambda: int(get_client().openScanner(table_name.encode(), scan)))
    try:
        rows: list[tuple[str, dict[str, Any]]] = []
        truncated = False
        timed_out = False
        started = False
        while True:
            if started and deadline is not None and time.monotonic() >= deadline:
                timed_out = True
                truncated = True
                break
            started = True
            batch = run_with_retry(
                lambda: get_client().getScannerRows(scanner_id, take)
            )
            if not batch:
                break
            for result in batch:
                if len(rows) >= cap:
                    truncated = True
                    break
                rows.append((guard.as_text(result.row), _cells(result)))
            if truncated:
                break
        return rows, truncated, timed_out
    finally:
        _close_scanner(scanner_id)


def _close_scanner(scanner_id: int) -> None:
    """关闭 scanner。

    失败只记日志：结果已经读回来了，此时再抛异常会把成功的查询变成失败。
    """
    try:
        get_client().closeScanner(scanner_id)
    except Exception as e:  # pragma: no cover - 取决于服务端状态
        logger.debug(f"[hbaseqa] closeScanner 失败 | id: {scanner_id} | {guard.exception_text(e)}")


def _cells(result: Any) -> dict[str, Any]:
    """TResult → `{"CF:OPEN": b"1.0856"}`，与 happybase 的返回结构保持一致。"""
    cells: dict[str, Any] = {}
    for column in result.columnValues or []:
        key = f"{guard.as_text(column.family)}:{guard.as_text(column.qualifier)}"
        cells[key] = column.value
    return cells


def _columns(module: Any, specs: Any) -> list[Any]:
    """`["CF:OPEN", "CF:LOW"]` → `[TColumn(family=b"CF", qualifier=b"OPEN"), ...]`"""
    if not specs:
        return []
    columns = []
    for spec in specs:
        family, _, qualifier = str(spec).partition(":")
        columns.append(
            module.TColumn(
                family=family.encode(),
                qualifier=qualifier.encode() if qualifier else None,
            )
        )
    return columns


def _to_bytes(value: Any) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return str(value).encode()


# ── 写入（仅供 CLI seed 脚本） ────────────────────────────────────────
#
# 问数链路是**只读**的：server.py 的工具永远不会调用下面这些函数。
# 写权限只存在于 `python -m easy_mcp_server.businesses.hbaseqa.seed`。


def put_rows(
    table: str,
    rows: Iterable[tuple[str, dict[str, Any]]],
    batch_size: int = 500,
    on_progress: Callable[[int], None] | None = None,
) -> int:
    """批量写入，返回写入行数。

    `rows` 为 ``(rowkey, {"CF:OPEN": b"1.0856"})``；值可以是 bytes（原样写）
    或任意可 str 化的对象（按 UTF-8 编码写）。

    用 `putMultiple` 分批，避免一次请求过大被服务端拒绝。
    `on_progress` 每批回调一次（已写行数）——上万行时没输出会让人以为卡死。
    """
    module = load_idl()
    checked = guard.assert_table_name(table)
    total = 0
    batch: list[Any] = []

    def _flush() -> None:
        nonlocal total
        if not batch:
            return
        payload = list(batch)
        run_with_retry(lambda: get_client().putMultiple(checked.encode(), payload))
        total += len(payload)
        batch.clear()
        if on_progress is not None:
            on_progress(total)

    for rowkey, cells in rows:
        batch.append(
            module.TPut(row=rowkey.encode(), columnValues=_column_values(module, cells))
        )
        if len(batch) >= batch_size:
            _flush()
    _flush()
    logger.info(f"[hbaseqa] 写入完成 | 表: {checked} | 行数: {total}")
    return total


def row_exists(table: str, rowkey: str, limit: int = 1) -> dict[str, Any] | None:
    """判断某个 rowkey 是否已存在，存在则返回该行（用于写前自检）。

    用 `openScanner([rowkey, rowkey + "\\xff"))` 代替 Thrift2 的 `get`：
    少声明一个结构体，语义上也足够——扫到的第一行 key 相同即命中。
    """
    table_name = guard.assert_table_name(table)
    module = load_idl()
    scan = module.TScan(
        startRow=rowkey.encode(),
        stopRow=f"{rowkey}\xff".encode(),
        limit=limit,
    )
    scanner_id = run_with_retry(lambda: int(get_client().openScanner(table_name.encode(), scan)))
    try:
        batch = run_with_retry(lambda: get_client().getScannerRows(scanner_id, limit))
    finally:
        _close_scanner(scanner_id)
    for result in batch:
        if guard.as_text(result.row) == rowkey:
            return _cells(result)
    return None


def _column_values(module: Any, cells: dict[str, Any]) -> list[Any]:
    """`{"CF:OPEN": b"1.0856"}` → `[TColumnValue(family=b"CF", qualifier=b"OPEN", ...)]`"""
    values = []
    for key, value in cells.items():
        family, _, qualifier = str(key).partition(":")
        values.append(
            module.TColumnValue(
                family=family.encode(),
                qualifier=qualifier.encode(),
                value=value if isinstance(value, (bytes, bytearray)) else str(value).encode(),
            )
        )
    return values
