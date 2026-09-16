"""HBase 元数据：表清单、维度反解、字段字典与数据采样。

HBase 没有 `information_schema`，字段含义只能靠配置里的口径字典（``config.FIELD_DICT``）
补中文语义；表的维度（渠道 / 产品类型 / 数据类型）从命名规则反解出来，
这样 `get_fx_bars` 才能按"哪个渠道的什么品种"选表，而不是把四张表全扫一遍。

表名规则：`{HBASEQA_TABLE_PREFIX}{CHANNEL}_{INSTRUMENT}_{DATATYPE}`，
例如 `HSDC_HDS2_UBS_FXSPOT_BAR_DEPTH`。
"""

from __future__ import annotations

import logging
from typing import Any

from . import client, config, guard, times

logger = logging.getLogger("easy-mcp-server")

DEFAULT_SAMPLE_LIMIT = 5
MAX_SAMPLE_LIMIT = 20


def list_tables() -> list[dict[str, Any]]:
    """列出可见表，并附带从表名反解出的渠道 / 产品类型 / 数据类型。"""
    tables: list[dict[str, Any]] = []
    for name in guard.filter_visible(client.table_names()):
        item = {"table": name, **parse_table_name(name)}
        tables.append(item)
    return tables


def parse_table_name(name: str) -> dict[str, Any]:
    """把表名拆成 渠道 / 产品类型 / 数据类型 三元组。

    做法是**以产品类型为锚点**切分：在 token 流里找到已知 INSTRUMENT 的位置，
    它左边的全部 token 归渠道、右边的全部 token 归数据类型。

    这样命名规则的"段数可变"就不用逐个 case 配：
    `HSDC_HDS2_BEST_HO_FXSPOT_BAR_BEST` → 渠道 `BEST_HO`、类型 `BAR_BEST`，
    而 `HSDC_HDS2_UBS_FXSPOT_BAR_DEPTH` → 渠道 `UBS`、类型 `BAR_DEPTH`，
    后者不会被 `BAR_DEPTH` 里的下划线干扰。

    锚点找不到时（表名偏离约定）退化为固定位次：第一段渠道、第二段产品、其余类型。
    """
    # 带命名空间的表名（`ns:TABLE`）先剥掉命名空间，否则前缀与切词都会错位
    local = name.split(":", 1)[1] if ":" in name else name
    prefix = config.table_prefix()
    remainder = local[len(prefix):] if prefix and local.startswith(prefix) else local
    tokens = [token for token in remainder.split("_") if token]
    if not tokens:
        return {"channel": None, "instrument": None, "datatype": None}

    anchor = _find_instrument(tokens)
    if anchor is None:
        return {
            "channel": tokens[0],
            "instrument": tokens[1] if len(tokens) > 1 else None,
            "datatype": "_".join(tokens[2:]) or None,
        }

    index, instrument = anchor
    return {
        "channel": "_".join(tokens[:index]) or None,
        "instrument": instrument,
        "datatype": "_".join(tokens[index + len(instrument.split("_")):]) or None,
    }


def _find_instrument(tokens: list[str]) -> tuple[int, str] | None:
    """在 token 流里定位已知产品类型，返回 (起始下标, 词表命中值)。

    长词优先（`FXSPOT` 优先于任何可能的前缀词），同长度靠前优先。
    """
    for candidate in sorted(
        config.KNOWN_INSTRUMENTS, key=lambda word: len(word.split("_")), reverse=True
    ):
        parts = candidate.split("_")
        for index in range(len(tokens) - len(parts) + 1):
            if tokens[index:index + len(parts)] == parts:
                return index, candidate
    return None


def resolve_tables(
    channel: str | None = None,
    instrument: str | None = None,
    datatype: str | None = None,
) -> list[str]:
    """按三个维度筛选可见表；三个都为空时返回全部可见表。"""
    wanted = {
        "channel": (channel or "").strip().upper() or None,
        "instrument": (instrument or "").strip().upper() or None,
        "datatype": (datatype or "").strip().upper() or None,
    }
    matched: list[str] = []
    for item in list_tables():
        if wanted["channel"] and (item.get("channel") or "").upper() != wanted["channel"]:
            continue
        if wanted["instrument"] and (item.get("instrument") or "").upper() != wanted["instrument"]:
            continue
        if wanted["datatype"] and (item.get("datatype") or "").upper() != wanted["datatype"]:
            continue
        matched.append(item["table"])
    return matched


def describe_table(table: str) -> dict[str, Any]:
    """返回表字典：维度、列族、字段中文含义、rowkey 口径与当前过滤策略。"""
    return {
        "table": guard.assert_table_name(table),
        **parse_table_name(table),
        "families": sorted(_families(table)) or [config.column_family()],
        "columns": _column_dictionary(),
        "rowkey": {
            "layout": config.rowkey_layout(),
            "format": "region(2) + time(13) + symbolHash(13) + frequency + side",
            "meaning": (
                "region 是 2 位分区前缀，紧跟 13 位毫秒时间戳；"
                "时间戳不在开头，范围扫描必须拼 region 前缀"
            ),
            "regions": list(config.region_prefixes()),
            "range_filter": guard.use_row_bounds(),
            "column_filter_used": guard.build_time_filter(0, 1) is not None,
        },
    }


def _families(table: str) -> list[str]:
    """列族列表。

    `getTableDescriptor` 属于可选能力，无权限时退回配置的列族即可，不该让
    describe_table 整个挂掉。但**连接类错误必须往外抛**：否则通道断了也会"成功"
    返回一份降级数据，把故障藏起来，排查时反而更难定位。
    """
    try:
        return client.families_of(table)
    except Exception as e:  # pragma: no cover - 取决于集群权限
        if client.is_connection_error(e):
            raise
        logger.debug(
            f"[hbaseqa] 读取列族失败，退回配置值 | 表: {table} | {guard.exception_text(e)}"
        )
        return [config.column_family()]


def _column_dictionary() -> list[dict[str, Any]]:
    family = config.column_family()
    return [
        {
            "name": f"{family}:{name}",
            "qualifier": name,
            "numeric": numeric,
            "comment": comment,
        }
        for name, comment, numeric in config.FIELD_DICT
    ]


def sample_rows(table: str, limit: int = DEFAULT_SAMPLE_LIMIT) -> dict[str, Any]:
    """采样真实数据行：既让模型看到取值形态，也让第一步 discovery 能跑通。

    样本取的是**最早**的若干行（正向 scan），rowkey 会一并返回，便于核对
    rowkey 是否真的是毫秒时间戳——这直接决定 `HBASEQA_ROWKEY_LAYOUT` 该怎么配。
    """
    size = max(1, min(int(limit or DEFAULT_SAMPLE_LIMIT), MAX_SAMPLE_LIMIT))
    family = config.column_family()
    plan = {
        "table": table,
        "columns": client.column_filters(family, list(config.FIELD_NAMES)),
        "row_start": None,
        "row_stop": None,
        "filter": None,
        "reverse": False,
        "limit": size,
    }

    def _scan() -> dict[str, Any]:
        # 取句柄也放进重试闭包里：重连后旧句柄绑的还是那个废掉的 socket
        return client.scan_table(client.get_table(table), plan)

    result = client.run_with_retry(_scan)
    # fx_decode 已经带上 rowkey，这里不能再重复传一次同名关键字
    rows = [fx_decode(rowkey, cells, family) for rowkey, cells in result["rows"][:size]]
    return {
        "table": guard.assert_table_name(table),
        "row_count": len(rows),
        "truncated": result["truncated"],
        "rows": [guard.json_safe(row) for row in rows],
    }


def fx_decode(rowkey: str, cells: dict[str, Any], family: str) -> dict[str, Any]:
    """把一行 Thrift cells 解码成结构化字典（数值字段转 float，时间字段补 ISO）。

    抽到这里是因为 `fxspot` 与 `metadata` 都要用，避免两处重复字节解码逻辑。
    """
    decoded: dict[str, Any] = {"rowkey": rowkey}
    for name in config.FIELD_NAMES:
        raw = cells.get(f"{family}:{name}")
        text = _to_text(raw)
        if name in config.NUMERIC_FIELDS:
            decoded[name] = _to_number(text)
        else:
            decoded[name] = text or None
    decoded["ts_ms"] = decoded.get("TIME")
    decoded["ts"] = times.to_iso(decoded["ts_ms"])
    return decoded


def bar_timestamp(decoded: dict[str, Any]) -> int | None:
    """取一行的最可靠时间：TIME 优先，缺失时退回 STARTTIMESTAMP / ENDTIMESTAMP。"""
    for name in config.TIME_FIELDS:
        value = decoded.get(name)
        if isinstance(value, (int, float)):
            return int(value)
    return None


def _to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return str(value)


def _to_number(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None
