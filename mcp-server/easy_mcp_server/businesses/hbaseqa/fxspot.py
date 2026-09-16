"""外汇行情领域层：K 线（Bar）取数与合约发现。

设计取舍与 `dataqa.xbond` 同源：**让模型挑参数（表 + 时间窗 + 合约），不让它拼条件**。
`get_fx_bars` / `list_contracts` 覆盖绝大多数问法，工具内部完成三件事——

1. **选表**：按 渠道 / 产品类型 / 数据类型 三个维度从可见表里挑（见
   `metadata.resolve_tables`），不会去扫无关的表
2. **限流**：rowkey 是毫秒时间戳，可以直接当 scan 边界用（字典序 == 数值序）；
   rowkey 不是纯时间戳时退回 `CF:TIME` 列过滤（服务端代价更高，但结果是对的）
3. **兜底校验**：rowkey 范围可能因 rowkey 形态与预期不符而失效，所以取回的数据
   还会用 `CF:TIME` 再卡一遍区间，超出部分丢掉并计数——宁可少给，不能静默给错
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from . import client, config, guard, metadata, times

DEFAULT_ORDER = "asc"
VALID_ORDERS = ("asc", "desc")
# 合约代码常见后缀：模型常说 EURUSD，库里存的是 EURUSDSP
CONTRACT_SUFFIXES = ("SP", "SPOT")


def schema_help() -> dict[str, Any]:
    """口径字典：模型写参数前先看这个，避免猜错列名与取值。"""
    return {
        "business": "外汇行情（K 线 / Tick）问数",
        "table_naming": (
            "{前缀}{渠道}_{产品类型}_{数据类型}，前缀默认 "
            f"{config.table_prefix()}；渠道名可能自带下划线，如 "
            "HSDC_HDS2_BEST_HO_FXSPOT_BAR_BEST 的渠道是 BEST_HO。"
            "先 list_tables 拿准确表名与三维度"
        ),
        "column_family": config.column_family(),
        "qualifiers": {name: comment for name, comment, _ in config.FIELD_DICT},
        "known_instruments": list(config.KNOWN_INSTRUMENTS),
        "known_datatypes": list(config.KNOWN_DATATYPES),
        "time_fields": {
            config.TIME_FIELDS[0]: "K 线时间，主过滤列",
            "SYSTIME": "系统落库时间，晚于 TIME，用来评估延迟，不要拿来过滤行情",
            "STARTTIMESTAMP/ENDTIMESTAMP": "Bar 的起止时间",
        },
        "frequency": (
            f"K 线频率，{config.DEFAULT_FREQUENCY} = 1 分钟。"
            "也接受 1m / 1min / M1 等别名，服务端会归一化成库里的写法"
        ),
        "buysell": (
            "买卖方向，取值 " + " / ".join(config.buy_sell_values()) +
            "（BID 买价 / ASK 卖价 / MID 中间价）。同一时刻三个方向可能各有一根 K 线"
        ),
        "granularity": (
            "取数粒度：auto（缺省，窗口超过 "
            f"{config.AUTO_COARSEN_HOURS} 小时自动降为 daily）/ daily / hourly / "
            "latest / raw。一个月 1 分钟明细约 13 万行，必须降采样；"
            "降采样后每合约只保留最后一根，响应里的 contracts 摘要够回答"
            "'涨了还是跌了'；但 high/low 只是采样点区间，**不是真实极值**"
        ),
        "rowkey": {
            "format": "region(2) + time(13) + symbolHash(13) + frequency + side",
            "meaning": (
                "region 是 2 位分区前缀，紧跟 13 位毫秒时间戳；"
                "**时间戳不在开头**，所以不要把毫秒直接当 startRow"
            ),
            "layout_configured": config.rowkey_layout(),
            "regions": list(config.region_prefixes()),
            "range_filter_active": guard.use_row_bounds(),
        },
        "pitfalls": [
            "先看响应里的 contracts 摘要（每合约一行），够答就不用再拉明细",
            "问'近一个月行情'别用 granularity='raw'：明细十几万行，会被 MAX_ROWS 截断",
            "要精确的区间高低点：granularity='raw' + 缩小时间窗，别用 daily 的高低点",
            "先 list_tables 确认表与维度，再 get_fx_bars，不要凭印象写表名",
            "合约代码要精确，EURUSD 与 EURUSDSP 不是同一个东西（工具会尝试补 SP 后缀）",
            "不传时间只有最近若干小时（HBASEQA_DEFAULT_WINDOW_HOURS），"
            "问历史行情必须显式传 start / end / hours / days",
            "同一时刻可能有多个 BUYSELL，只要中间价请显式传 buysell='MID'",
        ],
    }


def scan_plan(
    table: str,
    start_ms: int,
    end_ms: int,
    reverse: bool = False,
    deadline: float | None = None,
) -> dict[str, Any]:
    """把时间窗翻译成 Thrift scan 参数。

    `ranges` 可能有多段：真实 rowkey 是 `region(2)+time(13)+...`，时间戳不在开头，
    所以按 region 各拼一段 `{region}{毫秒}`（见 `guard.row_ranges`）。

    `deadline` 是时间止损（`time.monotonic()` 口径），透传给 `client.scan_table`。
    """
    return {
        "table": table,
        "columns": client.column_filters(config.column_family(), list(config.FIELD_NAMES)),
        "ranges": guard.row_ranges(start_ms, end_ms),
        "filter": guard.build_time_filter(start_ms, end_ms),
        "reverse": bool(reverse),
        "deadline": deadline,
    }


def collect(
    tables: list[str],
    start_ms: int,
    end_ms: int,
    contract_codes: list[str] | None = None,
    frequency: str | None = None,
    buysell: str | None = None,
    order: str = DEFAULT_ORDER,
    limit: int | None = None,
    granularity: str = "auto",
) -> dict[str, Any]:
    """跨表扫描并拼装 K 线结果；返回可直接交给 MCP 工具的结构。

    `granularity` 决定取多少数据（见 `config.GRANULARITIES`）：明细一个月有 13 万行，
    `daily` 只有 90 行。降采样走**按桶就近取值**，不是"读完再筛"——
    后者会被 `MAX_SCANNED_ROWS` 截断，导致日线静默缺一半。
    """
    notes: list[str] = []
    chosen, auto_note = resolve_granularity(granularity, start_ms, end_ms)
    if auto_note:
        notes.append(auto_note)

    take = guard.row_limit(limit)
    # 时间止损：与行数预算并行的另一道闸（见 config.scan_budget_ms 的说明）。
    # 扫描绝不能无上限地耗时间——调用方等响应头是有超时的，超时后报的还是
    # 一个看不出原因的 ExceptionGroup。
    deadline = time.monotonic() + config.scan_budget_ms() / 1000

    timed_out = False
    if chosen == "raw":
        # 关键：把"要多少行"下推到服务端。否则 TScan 会**读满整个时间窗**
        # （一个月 13 万行），而最终返回给模型的只有 MAX_ROWS 行——纯浪费。
        # +1 是为了知道"后面还有没有"（服务端返回第 take+1 行即说明被截断）。
        scanned = _collect_rows(
            tables, start_ms, end_ms, order=order, budget=take + 1, deadline=deadline
        )
        notes.extend(scanned["notes"])
        rows = scanned["rows"]
        scanned_rows = scanned["scanned"]
        scan_truncated = scanned["truncated"]
        skipped = scanned["skipped"]
        timed_out = scanned["timed_out"]
    else:
        rows, scanned_rows, scan_truncated, skipped, bucket_notes = _collect_bucketed(
            tables,
            start_ms,
            end_ms,
            chosen,
            frequency,
            buysell,
            order,
            contract_codes,
            deadline=deadline,
        )
        notes.extend(bucket_notes)

    known_codes = sorted({row["CONTRACTCODE"] for row in rows if row.get("CONTRACTCODE")})
    wanted, mapping = resolve_contracts(contract_codes, known_codes)
    if mapping:
        notes.append(f"合约代码映射: {mapping}")

    selected = [row for row in rows if _passes_dimensions(row, wanted, frequency, buysell)]
    selected.sort(key=lambda row: (row.get("ts_ms") or 0, row.get("CONTRACTCODE") or ""))
    trimmed = _trim(selected, want_desc=(order == "desc"), take=take)

    # 合约在 rowkey 中段，没法下推到服务端过滤；所以"按合约过滤 + 触到扫描上限"
    # 可能漏掉匹配行，必须说明（否则就是静默不完整）
    if timed_out:
        notes.append(
            f"扫描时间预算 {config.scan_budget_ms() / 1000:.1f}s 已用尽，"
            f"仅返回已读到的 {scanned_rows} 行（整个时间窗未扫完）。"
            "问\u201c某天有没有行情 / 最新到哪天\u201d请用 granularity='latest'："
            "它只反向探测少量行、一次就够；也可以指定 contract_codes 或缩短时间窗。"
        )
    if scan_truncated and chosen == "raw":
        notes.append(
            f"已达扫描上限（{scanned_rows} 行），仅返回其中最早/最新的 {len(trimmed)} 行"
            "；available_contracts 也只反映已扫描部分。"
            "问\u201c某天有没有行情 / 最新到哪天\u201d请用 granularity='latest'（只需反向探测少量行）；"
            "或缩短时间窗、指定 contract_codes，或用 granularity='daily'/'hourly'"
        )

    # 降采样后 high/low 只是**采样点的**区间，不是真实极值（日线取值会丢掉日内
    # 极值）。必须让模型知道这点，否则它会当成"本月最高价"报出去。
    if chosen != "raw":
        notes.append(
            f"granularity={chosen}：每{'小时' if chosen == 'hourly' else '天' if chosen == 'daily' else '合约'}"
            "只保留最后一根，high/low 是采样点区间、非真实极值；需要真实极值请用 "
            "granularity='raw' 并缩小时间窗"
        )

    return {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "window": times.describe_window(start_ms, end_ms),
        "tables": tables,
        "granularity": chosen,
        "granularity_requested": granularity,
        "available_contracts": known_codes,
        "contract_mapping": mapping,
        "scanned_rows": scanned_rows,
        "scan_truncated": scan_truncated,
        "skipped_out_of_range": skipped,
        "row_count": len(trimmed),
        "truncated": len(trimmed) < len(selected),
        "order": order,
        "summary": summarize(trimmed, start_ms, end_ms),
        "contracts": _contract_summary(trimmed),
        "summary_scope": "downsampled" if chosen != "raw" else "returned_rows",
        "rows": [guard.json_safe(row) for row in trimmed],
        "notes": notes,
    }


# ── 粒度 ──────────────────────────────────────────────────────────────


def assert_granularity(value: str | None) -> str:
    text = (value or config.granularity()).strip().lower()
    if text not in config.GRANULARITIES:
        raise ValueError(
            f"granularity 只能是 {' / '.join(config.GRANULARITIES)}，收到 {value!r}"
        )
    return text


def resolve_granularity(
    value: str | None, start_ms: int, end_ms: int
) -> tuple[str, str | None]:
    """确定实际粒度，返回 (粒度, 说明)。

    `auto` 是"别把模型上下文塞爆"的默认行为：窗口一长就降到 daily，
    并**明确告知**（模型看到 note 可以改用 raw 重问），不做静默降级。
    """
    chosen = assert_granularity(value)
    if chosen != "auto":
        return chosen, None
    hours = (end_ms - start_ms) / 3_600_000
    if hours > config.auto_coarsen_hours():
        return "daily", (
            f"时间窗 {hours:.1f} 小时超过 {config.auto_coarsen_hours()} 小时，"
            "已自动降采样为 granularity='daily'；要明细请显式传 granularity='raw'"
        )
    return "raw", None


def _collect_bucketed(
    tables: list[str],
    start_ms: int,
    end_ms: int,
    granularity: str,
    frequency: str | None,
    buysell: str | None,
    order: str,
    requested: list[str] | None = None,
    deadline: float | None = None,
) -> tuple[list[dict[str, Any]], int, bool, int, list[str]]:
    """按桶降采样：每个桶里每个合约只保留最后一根。"""
    notes: list[str] = []
    rows: list[dict[str, Any]] = []
    scanned_total = 0
    truncated = False
    skipped = 0
    budget = config.max_scanned_rows()

    # 自适应合约集合。它的作用只是决定"什么时候可以停止放大扫描"：
    # - 模型指定了合约 → 直接用它，桶内几十行就够（最常见、最省）
    # - 没指定 → 用一个中等规模探测建立集合，并在 notes 里说明
    expected: set[str] = {
        str(code).strip().upper() for code in (requested or []) if str(code).strip()
    }
    discovering = not expected
    missing: set[str] = set()
    probe_capped = False

    timed_out = False
    for table in tables:
        for bucket_start, bucket_end in _buckets(start_ms, end_ms, granularity):
            remaining = budget - scanned_total
            if remaining <= 0:
                truncated = True
                notes.append(
                    f"扫描预算 {budget} 行已用尽，后续桶（{times.to_iso(bucket_start)} 起）未取"
                )
                break
            # 时间止损。注意 `scanned_total` 的判断：**第一个桶总要跑**，否则
            # 预算设小了会拿到空结果（比慢更糟）。
            if scanned_total and deadline is not None and time.monotonic() >= deadline:
                truncated = True
                timed_out = True
                notes.append(
                    f"扫描时间预算 {config.scan_budget_ms() / 1000:.1f}s 已用尽，"
                    f"后续桶（{times.to_iso(bucket_start)} 起）未取"
                )
                break
            picked, stat = _bucket_newest(
                table,
                bucket_start,
                bucket_end,
                frequency,
                buysell,
                remaining,
                expected,
                deadline=deadline,
            )
            rows.extend(picked)
            scanned_total += stat["scanned"]
            skipped += stat["skipped"]
            truncated = truncated or stat["incomplete"]
            probe_capped = probe_capped or stat["probe_capped"]
            found = {row["CONTRACTCODE"] for row in picked if row.get("CONTRACTCODE")}
            for want in expected:
                if not any(code == want or code.startswith(want) for code in found):
                    missing.add(want)
            expected |= found
        if scanned_total >= budget or timed_out:
            break

    if discovering:
        notes.append(
            "未指定 contract_codes：合约清单由桶尾探测得出"
            + ("，且只探了每桶末尾一段" if probe_capped else "")
            + "，长期无报价的合约可能未被发现；需要完整清单请先调 list_contracts"
        )
    if missing:
        # 静默省略比报错更糟：模型会以为"这段时间没行情"
        notes.append(
            f"以下合约在窗口内的部分桶里没有数据（该时段无报价或已停止更新）："
            f"{', '.join(sorted(missing))}"
        )
    if truncated:
        notes.append(
            f"扫描预算 {budget} 行已用尽、时间预算用尽或单桶未取全，降采样可能不完整；"
            "可缩短时间窗、收窄合约范围，或改用 granularity='latest'"
        )
    return rows, scanned_total, truncated, skipped, notes


def _buckets(start_ms: int, end_ms: int, granularity: str) -> list[tuple[int, int]]:
    """把窗口切成桶（闭区间）。

    - `latest`：整窗当一个桶（每合约最终只剩最新一根）
    - `hourly`：按 UTC 整点切
    - `daily`：按**业务时区**的自然日切（`HBASEQA_TZ`）——"日线"指本地日，
      否则中国用户的"每天最后一根"会落在 UTC 16:00
    """
    if granularity == "latest":
        return [(start_ms, end_ms)]

    if granularity == "hourly":
        step = config.BUCKET_MS_BY_GRANULARITY["hourly"]
        cursor = start_ms - (start_ms % step)
        buckets: list[tuple[int, int]] = []
        while cursor <= end_ms:
            buckets.append((max(cursor, start_ms), min(cursor + step - 1, end_ms)))
            cursor += step
        return buckets

    timezone = times.timezone_of()
    cursor = _day_start_ms(start_ms, timezone)
    buckets = []
    while cursor <= end_ms:
        next_day = _day_start_ms(cursor + 86_400_000, timezone)
        buckets.append((max(cursor, start_ms), min(next_day - 1, end_ms)))
        cursor = next_day
    return buckets


def _day_start_ms(ms: int, timezone: Any) -> int:
    moment = datetime.fromtimestamp(ms / 1000, tz=timezone)
    midnight = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return int(midnight.timestamp() * 1000)


def _bucket_newest(
    table: str,
    bucket_start: int,
    bucket_end: int,
    frequency: str | None,
    buysell: str | None,
    remaining_budget: int,
    expected: set[str],
    deadline: float | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """取一个桶里每个合约的最后一根。

    做法：**反向扫桶尾**（新→旧），首次遇到的某合约即为该桶内最新。

    终止条件分两种情况，这是效率与正确性的关键：

    - `expected` 已知（后续桶）：集齐这些合约就停 → 通常一次几十行的扫描
    - `expected` 为空（第一个桶）：一次取够（上限 4096 行）以建立完整合约集合

    行数不足时按阶梯放大 limit；桶被扫完（服务端返回不足 cap 行）就不再放大——
    桶本身有界（天桶 ≈ 1440 行 × 合约数），不会失控。

    返回值里的两个标志刻意分开，别混为一谈：

    - `incomplete`：**期望合约没集齐，且桶里还有数据没读**——结果确实可能缺
    - `probe_capped`：合约未知，只探了桶尾一段——可能漏掉长期无报价的合约

    "只探桶尾"是降采样的**设计**（`result["truncated"]` 会一直为真），不能直接当成
    "结果不完整"报出去：否则调用方看到一个没有解释的 `truncated: true` 就会自己
    脑补原因（真实发生过：模型据此编出"正向扫描截断只到 09-12"，而实际是反向扫描、
    日期完全不对）。
    """
    picked: dict[str, dict[str, Any]] = {}
    scanned = 0
    skipped = 0
    capped = False
    exhausted = False

    for cap in _limit_ladder(remaining_budget, expected):
        plan = {
            "table": table,
            "columns": client.column_filters(config.column_family(), list(config.FIELD_NAMES)),
            "ranges": guard.row_ranges(bucket_start, bucket_end),
            "filter": guard.build_time_filter(bucket_start, bucket_end),
            "reverse": config.reverse_scan_enabled(),
            "limit": cap,
            "deadline": deadline,
        }
        result = client.scan_table(client.get_table(table), plan)
        scanned += result["scanned"]

        for rowkey, values in result["rows"]:
            row = metadata.fx_decode(rowkey, values, config.column_family())
            ts = metadata.bar_timestamp(row)
            if ts is None or not (bucket_start <= ts <= bucket_end):
                skipped += 1
                continue
            if not _passes_dimensions(row, None, frequency, buysell):
                continue
            code = row.get("CONTRACTCODE")
            if not code:
                continue
            row["table"] = table
            # 反向扫描 = 新→旧，所以首次出现的就是该桶内最新的一根
            picked.setdefault(code, row)

        # 桶已扫完（返回不足 cap 行）→ 再放大也没意义
        if result["scanned"] < cap:
            exhausted = True
            break
        capped = True
        # 该出现的合约都出现了 → 目的达成，别再放大
        if _expected_satisfied(expected, picked):
            break

    satisfied = _expected_satisfied(expected, picked)
    return list(picked.values()), {
        "scanned": scanned,
        "skipped": skipped,
        "incomplete": bool(capped and not exhausted and expected and not satisfied),
        "probe_capped": bool(capped and not expected),
    }


def _expected_satisfied(expected: set[str], picked: dict[str, dict[str, Any]]) -> bool:
    """每个"期望合约"是否都已露面。

    用前缀匹配兼容口语化代码：模型说 `EURUSD`、库里是 `EURUSDSP` 也算命中，
    否则会以为没取全而把每个桶都放大重扫。
    """
    if not expected:
        return False
    found = {code.upper() for code in picked}
    return all(
        any(code == want or code.startswith(want) for code in found) for want in expected
    )


def _limit_ladder(remaining_budget: int, expected: set[str]) -> list[int]:
    """桶内探查的 limit 阶梯（去重、升序、受剩余预算约束）。"""
    if not expected:
        # 合约未知：用中等规模探测一次建立集合。
        # 不用最大档（4096）是因为它会被解码成本放大——而"哪些合约"这类信息
        # 本来就有 list_contracts 专门负责。
        rung = min(config.BUCKET_LIMIT_LADDER[0], remaining_budget)
        return [rung] if rung > 0 else [remaining_budget]
    base = max(32, len(expected) * config.BUCKET_LIMIT_FACTOR)
    ladder = {base, *config.BUCKET_LIMIT_LADDER}
    return sorted(cap for cap in ladder if cap <= remaining_budget) or [remaining_budget]


def _contract_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """每合约一行的结构化摘要——模型先看这几十行就能答"涨了还是跌了"。

    字段刻意标注了 scope（`summary_scope`）：降采样时 high/low 是采样点区间，
    不是真实极值，避免模型把它当成"该区间最高价"报出去。
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        code = row.get("CONTRACTCODE")
        if code:
            grouped.setdefault(code, []).append(row)

    summary: list[dict[str, Any]] = []
    for code, items in sorted(grouped.items()):
        items.sort(key=lambda row: row.get("ts_ms") or 0)
        closes = [row["CLOSE"] for row in items if row.get("CLOSE") is not None]
        highs = [row["HIGH"] for row in items if row.get("HIGH") is not None]
        lows = [row["LOW"] for row in items if row.get("LOW") is not None]
        first_close = closes[0] if closes else None
        last_close = closes[-1] if closes else None
        change_pct = None
        if first_close and last_close is not None:
            change_pct = round((last_close - first_close) / first_close * 100, 4)
        summary.append(
            {
                "contract": code,
                "bars": len(items),
                "first_ts": items[0].get("ts"),
                "last_ts": items[-1].get("ts"),
                "first_close": first_close,
                "last_close": last_close,
                "change_pct": change_pct,
                "high": max(highs) if highs else None,
                "low": min(lows) if lows else None,
                "buy_sells": sorted({row.get("BUYSELL") for row in items if row.get("BUYSELL")}),
            }
        )
    return summary


def collect_contracts(
    tables: list[str], start_ms: int, end_ms: int, limit: int = 50
) -> dict[str, Any]:
    """列出时间窗内出现过的合约代码，附 K 线数量、最新收盘价与高低点。

    只做"发现"，所以扫描带**预算**（`HBASEQA_CONTRACT_SCAN_BUDGET`，缺省 5000 行）：
    一个月窗口有 13 万行，读满纯属浪费。预算用尽时会在 `scan_truncated` 与 notes
    里明确说明——**不能让调用方以为拿到的是完整清单**（bar_count 会偏小）。
    """
    budget = config.contract_scan_budget()
    deadline = time.monotonic() + config.scan_budget_ms() / 1000
    scanned = _collect_rows(tables, start_ms, end_ms, budget=budget, deadline=deadline)
    notes: list[str] = []
    if scanned["truncated"]:
        notes.append(
            f"扫描达预算上限 {budget} 行，合约清单与 bar_count 可能不完整；"
            "缩短时间窗或收窄 --channel/--instrument 可得到完整结果"
        )
    aggregated: dict[str, dict[str, Any]] = {}
    for row in scanned["rows"]:
        code = row.get("CONTRACTCODE")
        if not code:
            continue
        entry = aggregated.setdefault(
            code,
            {
                "contract": code,
                "table": row.get("table"),
                "channel": row.get("CHANNEL"),
                "instrument": row.get("INSTRUMENT"),
                "frequency": row.get("FREQUENCY"),
                "bar_count": 0,
                "first_ts": row.get("ts"),
                "last_ts": row.get("ts"),
                "last_close": row.get("CLOSE"),
                "high": row.get("HIGH"),
                "low": row.get("LOW"),
                "_first_ms": row.get("ts_ms"),
                "_last_ms": row.get("ts_ms"),
            },
        )
        entry["bar_count"] += 1
        ts_ms = row.get("ts_ms")
        if ts_ms is None:
            continue
        if entry["_first_ms"] is None or ts_ms < entry["_first_ms"]:
            entry["_first_ms"] = ts_ms
            entry["first_ts"] = row.get("ts")
        if entry["_last_ms"] is None or ts_ms >= entry["_last_ms"]:
            entry["_last_ms"] = ts_ms
            entry["last_ts"] = row.get("ts")
            entry["last_close"] = row.get("CLOSE")
        if row.get("HIGH") is not None:
            entry["high"] = row["HIGH"] if entry["high"] is None else max(entry["high"], row["HIGH"])
        if row.get("LOW") is not None:
            entry["low"] = row["LOW"] if entry["low"] is None else min(entry["low"], row["LOW"])

    rows = []
    for entry in aggregated.values():
        entry.pop("_first_ms", None)
        entry.pop("_last_ms", None)
        rows.append(entry)
    rows.sort(key=lambda item: item["contract"])

    kept = rows[: guard.row_limit(limit)]
    return {
        "start_ms": start_ms,
        "end_ms": end_ms,
        "window": times.describe_window(start_ms, end_ms),
        "tables": tables,
        "row_count": len(kept),
        "truncated": len(kept) < len(rows),
        "scanned_rows": scanned["scanned"],
        "scan_truncated": scanned["truncated"],
        "skipped_out_of_range": scanned["skipped"],
        "summary": (
            f"{len(kept)} 个合约" if kept else f"{times.describe_window(start_ms, end_ms)} 内没有数据"
        ),
        "rows": [guard.json_safe(row) for row in kept],
        "notes": notes,
    }


def resolve_contracts(
    requested: list[str] | None, known: list[str]
) -> tuple[list[str] | None, dict[str, str]]:
    """把模型的口语化代码校正为库里的真实代码。

    只做两条保守规则：精确匹配（忽略大小写）> 补 `SP` / `SPOT` 后缀后再匹配。
    匹配不上就原样保留——宁可返回空，也不猜相似代码。第二返回值是映射说明，
    会回填给模型，方便它下次直接用正确代码。
    """
    if not requested:
        return None, {}
    lowered = {code.lower(): code for code in known}

    resolved: list[str] = []
    mapping: dict[str, str] = {}
    for raw in requested:
        code = str(raw).strip()
        if not code:
            continue
        hit = lowered.get(code.lower())
        if hit is None:
            for suffix in CONTRACT_SUFFIXES:
                candidate = f"{code.upper()}{suffix}"
                if candidate.lower() in lowered:
                    hit = lowered[candidate.lower()]
                    break
        if hit is None:
            if code not in resolved:
                resolved.append(code)
            continue
        if hit != code:
            mapping[code] = hit
        if hit not in resolved:
            resolved.append(hit)
    return (resolved or None), mapping


def summarize(rows: list[dict[str, Any]], start_ms: int, end_ms: int) -> str:
    """给模型的一句中文结论：多少行、哪些合约、价格怎么走的。"""
    if not rows:
        return f"{times.describe_window(start_ms, end_ms)} 内没有匹配的外汇行情数据。"
    contracts = sorted({row.get("CONTRACTCODE") for row in rows if row.get("CONTRACTCODE")})
    listed = "、".join(contracts[:5]) + ("等" if len(contracts) > 5 else "")
    first_ts = times.to_iso(rows[0].get("ts_ms"))
    last_ts = times.to_iso(rows[-1].get("ts_ms"))
    closes = [row["CLOSE"] for row in rows if row.get("CLOSE") is not None]
    parts = [f"{len(rows)} 行 K 线，{len(contracts)} 个合约（{listed}）",
             f"时间 {first_ts} ~ {last_ts}"]
    if closes:
        open_price, last_close = closes[0], closes[-1]
        change = last_close - open_price
        percent = (change / open_price * 100) if open_price else 0.0
        parts.append(f"收盘价 {open_price} → {last_close}（{change:+.5f}，{percent:+.2f}%）")
    return "；".join(parts)


def assert_order(order: str | None) -> str:
    value = (order or DEFAULT_ORDER).strip().lower()
    if value not in VALID_ORDERS:
        raise ValueError(f"order 只能是 asc / desc，收到 {order!r}")
    return value


def assert_buysell(value: str | None) -> str | None:
    """校验买卖方向。

    取值只有 BID / ASK / MID，写错时直接报错比返回空结果有用得多——
    模型拿到 "只能填 BID/ASK/MID" 就知道自己该改什么。
    """
    if value is None or not str(value).strip():
        return None
    text = str(value).strip().upper()
    allowed = config.buy_sell_values()
    if text not in allowed:
        raise ValueError(f"buysell 只能是 {'/'.join(allowed)}，收到 {value!r}")
    return text


def _collect_rows(
    tables: list[str],
    start_ms: int,
    end_ms: int,
    order: str = DEFAULT_ORDER,
    budget: int | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """扫描多张表并解码为结构化行，同时做时间窗兜底校验。

    `budget` 是整次调用的扫描行数上限（缺省用 `max_scanned_rows()`）。
    只做"发现"类工作时（如列合约）给一个小预算就够了，没必要读满窗口。

    `deadline` 是时间止损，透传到 `client.scan_table`：到点带着已读到的行返回，
    并在 `timed_out` 里标出来。
    """
    rows: list[dict[str, Any]] = []
    notes: list[str] = []
    scanned_total = 0
    truncated = False
    timed_out = False
    skipped = 0

    for table in tables:
        plan = scan_plan(table, start_ms, end_ms, deadline=deadline)
        if budget is not None:
            plan["limit"] = budget

        def _scan_once(table: str = table, plan: dict = plan) -> tuple[bool, dict[str, Any]]:
            # 取句柄放在闭包里：连接失效重试时会重新拿 handle
            handle = client.get_table(table)
            reverse = order == "desc" and client.supports_reverse_scan(handle)
            plan["reverse"] = reverse
            return reverse, client.scan_table(handle, plan)

        reverse, result = client.run_with_retry(_scan_once)
        if order == "desc" and not reverse:
            notes.append(f"{table}: 驱动不支持反向扫描，改为正向扫描后保留最新的 N 条")
        scanned_total += result["scanned"]
        truncated = truncated or result["truncated"]
        if result.get("timed_out"):
            timed_out = True
            notes.append(
                f"{table}: 扫描时间预算用尽，仅返回已读到的 {result['scanned']} 行"
            )
        for rowkey, cells in result["rows"]:
            row = metadata.fx_decode(rowkey, cells, config.column_family())
            ts = metadata.bar_timestamp(row)
            if ts is None or not (start_ms <= ts <= end_ms):
                skipped += 1
                continue
            row["table"] = table
            rows.append(row)

    return {
        "rows": rows,
        "notes": notes,
        "scanned": scanned_total,
        "truncated": truncated,
        "timed_out": timed_out,
        "skipped": skipped,
    }


def _passes_dimensions(
    row: dict[str, Any],
    contracts: list[str] | None,
    frequency: str | None,
    buysell: str | None,
) -> bool:
    if contracts and (row.get("CONTRACTCODE") or "") not in contracts:
        return False
    wanted_frequency = config.normalise_frequency(frequency)
    if wanted_frequency and (row.get("FREQUENCY") or "").upper() != wanted_frequency:
        return False
    if buysell and (row.get("BUYSELL") or "").upper() != buysell.upper():
        return False
    return True


def _trim(rows: list[dict[str, Any]], want_desc: bool, take: int) -> list[dict[str, Any]]:
    """取前 N 条并保持目标顺序。

    HBase 没有 SQL 的 LIMIT：正向扫描天然保住的是**最早**的 N 条，
    反向扫描保住的才是**最新**的 N 条。所以"问最近行情"要显式传 `order='desc'`，
    这一点写进了工具说明。
    """
    ordered = list(reversed(rows)) if want_desc else rows
    return ordered[:take]
