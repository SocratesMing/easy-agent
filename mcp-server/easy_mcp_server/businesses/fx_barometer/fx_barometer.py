"""外汇晴雨指数领域层（单表，一日一条，``result_json`` 存完整单日结果，只读）。

表 ``fmqt_fx_barometer_usdcny_history``：``signal_date`` 为主键，一行 = 一个交易日的
完整结果 JSON。业务只有 ``USDCNY``。口径照
``docs/MCP_docs/外汇晴雨指数_SQL清单_MCP信息.md``：

- **数据不是实时行情**：查范围时只报库里的起止日期，不能宣称"已更新到今日"；
- **某个交易日没有记录就返回空**，不能沿用另一日的预测顶替；
- **预测准备期**：``forecast`` 为 JSON ``null``（不是对象），此时 3M/6M 节点为
  ``NULL``——**不能生成虚构区间**。判定用
  ``JSON_TYPE(JSON_EXTRACT(result_json, '$.forecast')) = 'OBJECT'``，
  这样准备期的 ``null`` 不会被误判成"有预测"；
- **nodes 顺序固定为 3M、6M**（导入门禁保证），所以数组下标 0/1 分别对应两者；
- **分位区间是名义区间**（内层 25%–75%、外层 5%–95%），**没有经过实际覆盖率验证**；
- ``model.averageR2`` 是测试评分，**可为负，不是预测概率**——不得改写成"可信度"；
- ``model.annualMu`` / ``annualSigma`` 是**年化小数**，不是保证收益；
- ``result_json`` 里未知的扩展字段**以原 JSON 为准，不丢弃**；
- ``result_json.date`` 应与 ``signal_date`` 一致（工具层会核对并给出提示）；
- MySQL 可能返回原生 JSON 对象或字符串，**先解析再取值**，不把展示值回写覆盖原数据。

与 ``fxbar``（外汇 K 线，表 ``HSDC_HDS2_BAR_FX``）**不是同一个业务**，别混。

本模块不做写入、不训练模型、不重算分位，也不提供任何非 SELECT 语句。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from ...env import load_env
from ...querykit import assert_identifier

TABLES: dict[str, str] = {"main": "fmqt_fx_barometer_usdcny_history"}

MODEL_NAME = "外汇晴雨指数"

#: 业务仅这一个货币对
SYMBOL = "USDCNY"

#: 节点顺序由导入门禁固定：数组 0 = 3M，1 = 6M
NODE_LABELS: tuple[str, ...] = ("3M", "6M")
NODE_HORIZONS: tuple[int, ...] = (66, 132)

DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

DEFAULT_DASHBOARD_LIMIT = 250
MAX_DASHBOARD_LIMIT = 2000

#: 日期列表最多返回多少条（防一次拉爆；0 表示不限制由调用方决定）
DEFAULT_DATES_LIMIT = 5000

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: 单位与口径说明（原值返回，不换算）
UNITS: dict[str, str] = {
    "ohlc.open/high/low/close": "汇率（CNY/USD）",
    "nodes[].horizon": "交易日数（3M=66，6M=132）",
    "nodes[].p05/p25/median/p75/p95": "汇率分位数/中位数（名义区间，未经覆盖率验证）",
    "model.annualMu": "年化漂移（小数，不是保证收益）",
    "model.annualSigma": "年化波动率（小数）",
    "model.averageR2": "测试评分（可为负，不是预测概率）",
    "model.paths/seed": "蒙特卡洛路径数与随机种子（20000 / 42）",
}


def schema_name() -> str:
    load_env()
    return os.environ.get("FX_BAROMETER_SCHEMA", "").strip()


def table_ref(key: str = "main") -> str:
    if key not in TABLES:
        raise ValueError(f"未知数据表: {key!r}（可选 {', '.join(TABLES)}）")
    table = f"`{assert_identifier(TABLES[key])}`"
    schema = schema_name()
    return f"`{assert_identifier(schema)}`.{table}" if schema else table


def default_allowed_tables() -> tuple[str, ...]:
    """结构探索白名单缺省值：本场景唯一一张表。"""
    return tuple(TABLES.values())


# ── 参数校验 ────────────────────────────────────────────────────────────


def validate_date(value: str | None, name: str = "date") -> str | None:
    """校验 ``YYYY-MM-DD``；留空返回 None（表示取最新交易日）。"""
    text = str(value or "").strip()
    if not text:
        return None
    if not _DATE_RE.fullmatch(text):
        raise ValueError(f"{name} 必须是 YYYY-MM-DD 格式（收到 {value!r}）")
    return text


def validate_required_date(value: str | None, name: str = "date") -> str:
    resolved = validate_date(value, name)
    if resolved is None:
        raise ValueError(f"{name} 不能为空")
    return resolved


def validate_page(value: int | None) -> int:
    """页码：从 1 开始。"""
    resolved = int(value) if value is not None else DEFAULT_PAGE
    if resolved < 1:
        raise ValueError(f"page 必须 >= 1（收到 {value!r}）")
    return resolved


def validate_page_size(value: int | None) -> int:
    """每页条数：1~200。"""
    resolved = int(value) if value is not None else DEFAULT_PAGE_SIZE
    if resolved < 1 or resolved > MAX_PAGE_SIZE:
        raise ValueError(f"page_size 必须在 1 ~ {MAX_PAGE_SIZE} 之间（收到 {value!r}）")
    return resolved


def bounded_ohlc_limit(value: int | None) -> int:
    """仪表盘 OHLC 条数：1~2000，留空取 250。"""
    resolved = int(value) if value is not None else DEFAULT_DASHBOARD_LIMIT
    return max(1, min(resolved, MAX_DASHBOARD_LIMIT))


def validate_interval(day: str | None, name: str = "date") -> str:
    """``intervals`` 的日期是必填的——没有日期就没有"该日的研判"。"""
    return validate_required_date(day, name)


# ── JSON 解析 ───────────────────────────────────────────────────────────


def parse_json(value: Any) -> Any:
    """把 ``result_json`` 解析成对象；解不出来返回 None（不抛异常）。

    驱动可能返回已解析的 dict/list（原生 JSON 列）或 str/bytes，三种都兼容。
    """
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return None
    return None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def forecast_of(payload: Any) -> dict[str, Any] | None:
    """取 ``forecast``：只有是对象才算有预测（准备期是 JSON null）。"""
    if not isinstance(payload, dict):
        return None
    forecast = payload.get("forecast")
    return forecast if isinstance(forecast, dict) else None


def nodes_of(payload: Any) -> list[Any] | None:
    """取 ``forecast.nodes``：没有预测或不是数组时返回 None。"""
    forecast = forecast_of(payload)
    if forecast is None:
        return None
    nodes = forecast.get("nodes")
    return nodes if isinstance(nodes, list) else None


def node_labels_of(payload: Any) -> list[str]:
    """按固定契约给节点标注 3M / 6M（下标 0/1）。"""
    nodes = nodes_of(payload)
    if not nodes:
        return []
    labels = []
    for index in range(len(nodes)):
        labels.append(NODE_LABELS[index] if index < len(NODE_LABELS) else f"#{index}")
    return labels


def is_preparing(payload: Any) -> bool:
    """是否处于预测准备期（``forecast`` 不是对象）。"""
    return forecast_of(payload) is None


def result_payload(row: dict[str, Any]) -> dict[str, Any]:
    """把一个历史行整理成返回结构。

    原则：**完整 JSON 原样带上**（未知扩展字段不丢弃），另外给少量便捷视图；
    准备期只标 ``preparing=true``，**不补造区间**。
    """
    signal_date = row.get("signal_date")
    payload = parse_json(row.get("result_json"))
    out: dict[str, Any] = {
        "signal_date": str(signal_date) if signal_date is not None else None,
        "data": payload,
        "preparing": is_preparing(payload),
    }
    if isinstance(payload, dict):
        inner_date = payload.get("date")
        out["schema_version"] = _as_int(payload.get("schemaVersion"))
        out["symbol"] = payload.get("symbol")
        out["date"] = str(inner_date) if inner_date is not None else None
        # 主键与内层日期必须一致，不一致时明确提示（不静默）
        out["date_consistent"] = (
            str(inner_date) == str(signal_date) if inner_date is not None else None
        )
        ohlc = payload.get("ohlc")
        if isinstance(ohlc, dict):
            out["ohlc"] = {
                key: _as_float(ohlc.get(key))
                for key in ("open", "high", "low", "close")
            }
        nodes = nodes_of(payload)
        if nodes:
            out["nodes"] = nodes
            out["node_labels"] = node_labels_of(payload)
        forecast = forecast_of(payload)
        if forecast is not None:
            out["basis"] = forecast.get("basis")
        model = forecast.get("model") if isinstance(forecast, dict) else None
        if isinstance(model, dict):
            out["model"] = {
                "trainingDate": (
                    str(model["trainingDate"])
                    if model.get("trainingDate") is not None
                    else None
                ),
                "fitAsOf": (
                    str(model["fitAsOf"]) if model.get("fitAsOf") is not None else None
                ),
                "annualMu": _as_float(model.get("annualMu")),
                "annualSigma": _as_float(model.get("annualSigma")),
                "averageR2": _as_float(model.get("averageR2")),
                "sigmaMethod": model.get("sigmaMethod"),
                "paths": _as_int(model.get("paths")),
                "seed": _as_int(model.get("seed")),
                "refresh": model.get("refresh"),
                "latestRefit": model.get("latestRefit"),
            }
    return out


def status_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Q1：条数与起止日期。"""
    total = _as_int(row.get("total_count"))
    first = row.get("first_date")
    latest = row.get("latest_date")
    return {
        "total_count": total or 0,
        "first_date": str(first) if first is not None else None,
        "latest_date": str(latest) if latest is not None else None,
        "empty": (total or 0) == 0,
        "note": "只表示库里的覆盖范围，不代表数据已更新到今日（历史 JSON 是重算结果）",
    }


def dates_payload(rows: list[dict[str, Any]], limit: int) -> dict[str, Any]:
    """Q2：交易日列表 + 预测准备期标记。"""
    items = [
        {
            "signal_date": (
                str(row["signal_date"]) if row.get("signal_date") is not None else None
            ),
            "has_forecast": bool(_as_int(row.get("has_forecast"))),
        }
        for row in rows
    ]
    first_forecast = next(
        (item["signal_date"] for item in items if item["has_forecast"]), None
    )
    return {
        "count": len(items),
        "first_forecast_date": first_forecast,
        "truncated": len(items) >= limit,
        "items": items,
    }


def interval_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Q10/Q11：3M/6M 节点与模型参数。"""
    basis = row.get("basis")
    node_3m = parse_json(row.get("node_3m"))
    node_6m = parse_json(row.get("node_6m"))
    model = parse_json(row.get("model_json"))
    signal_date = row.get("signal_date")
    preparing = node_3m is None and node_6m is None
    out: dict[str, Any] = {
        "signal_date": str(signal_date) if signal_date is not None else None,
        "preparing": preparing,
        "basis": basis,
        "nodes": [node_3m, node_6m],
        "node_labels": list(NODE_LABELS),
        "model": model,
    }
    if preparing:
        out["note"] = (
            "该日处于预测准备期（forecast 为空），没有 3M/6M 节点——不生成虚构区间"
        )
    return out


def ohlc_series(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把若干行压成 OHLC 序列（用于 dashboard 的 K 线）。"""
    series: list[dict[str, Any]] = []
    for row in rows:
        payload = parse_json(row.get("result_json"))
        ohlc = payload.get("ohlc") if isinstance(payload, dict) else None
        signal_date = row.get("signal_date")
        series.append(
            {
                "signal_date": str(signal_date) if signal_date is not None else None,
                "open": _as_float(ohlc.get("open")) if isinstance(ohlc, dict) else None,
                "high": _as_float(ohlc.get("high")) if isinstance(ohlc, dict) else None,
                "low": _as_float(ohlc.get("low")) if isinstance(ohlc, dict) else None,
                "close": _as_float(ohlc.get("close")) if isinstance(ohlc, dict) else None,
            }
        )
    return series


def schema_help() -> dict[str, Any]:
    """数据字典：结构、单位、陷阱。"""
    return {
        "model_name": MODEL_NAME,
        "table": TABLES["main"],
        "schema": schema_name() or None,
        "symbol": SYMBOL,
        "storage": (
            "一日一条：signal_date 为主键，result_json 存该交易日完整结果"
            "（ohlc + forecast.nodes[3M,6M] + forecast.model）"
        ),
        "node_contract": {
            "order": list(NODE_LABELS),
            "horizons": list(NODE_HORIZONS),
            "note": "数组下标 0/1 分别对应 3M/6M，由导入门禁固定",
        },
        "units": dict(UNITS),
        "status_rule": (
            "库里覆盖范围不等于实时：数据非实时行情，历史 JSON 是重算研判，"
            "不是当时发布的档案"
        ),
        "preparing_rule": (
            "forecast 为 JSON null 即预测准备期，此时 3M/6M 节点为 null——"
            "返回空并标注，不补造区间"
        ),
        "missing_date_rule": (
            "某个交易日无记录就返回空，不沿用另一日的预测顶替"
        ),
        "not_available": (
            "分位区间是名义区间（未做覆盖率验证）；averageR2 是测试评分不是预测概率；"
            "annualMu/annualSigma 是年化小数不是保证收益；3M/6M 走势不是交易指令"
        ),
        "pitfalls": [
            "数据不是实时行情：不要用最新一期冒充今日汇率",
            "准备期 forecast 为 null，误判成「有预测」会凭空造出区间",
            "nodes 顺序固定 3M/6M，不要按 label 之外的顺序解读",
            "内层 25%–75%、外层 5%–95% 是名义分位区间，不是验证过的覆盖率",
            "averageR2 可为负，不是概率、不是可信度",
            "result_json 里未知扩展字段以原 JSON 为准，不要丢弃",
            "result_json.date 应与 signal_date 一致，不一致要如实说明",
            "MA20 需要额外前 19 条行情热身，只有 N 条不能推算历史均线",
        ],
    }


# ── SQL 构造（每条对应 SQL 清单的一个章节）────────────────────────────


def range_sql() -> tuple[str, tuple[Any, ...]]:
    """Q1：条数与起止日期。"""
    return (
        f"SELECT COUNT(*) AS total_count, MIN(`signal_date`) AS first_date, "
        f"MAX(`signal_date`) AS latest_date FROM {table_ref()}",
        (),
    )


def dates_sql(limit: int = DEFAULT_DATES_LIMIT) -> tuple[str, tuple[Any, ...]]:
    """Q2：全部交易日 + 是否有预测（准备期不算有预测）。

    ``JSON_TYPE(...) = 'OBJECT'`` 而不是 ``IS NOT NULL``：准备期的 JSON ``null``
    在 ``JSON_EXTRACT`` 下不是 SQL NULL，用它判断会把准备期误判成有预测。
    """
    size = max(1, int(limit))
    return (
        f"SELECT `signal_date`, "
        f"JSON_TYPE(JSON_EXTRACT(`result_json`, '$.forecast')) = 'OBJECT' "
        f"AS has_forecast FROM {table_ref()} ORDER BY `signal_date` LIMIT {size}",
        (),
    )


def result_sql(day: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """Q3/Q4：指定日期完整 JSON；留空取最新。"""
    date = validate_date(day)
    if date:
        return (
            f"SELECT `signal_date`, `result_json` FROM {table_ref()} "
            f"WHERE `signal_date` = %s ORDER BY `signal_date` DESC LIMIT 1",
            (date,),
        )
    return (
        f"SELECT `signal_date`, `result_json` FROM {table_ref()} "
        f"ORDER BY `signal_date` DESC LIMIT 1",
        (),
    )


def range_count_sql(
    start_date: str | None = None, end_date: str | None = None
) -> tuple[str, tuple[Any, ...]]:
    """Q5：闭区间总数（用于分页 total）。"""
    return _range_query(
        "SELECT COUNT(*) AS total_count FROM {table}",
        start_date,
        end_date,
    )


def history_sql(
    start_date: str | None = None,
    end_date: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """Q6：闭区间倒序分页，返回完整历史 JSON。"""
    resolved_page = validate_page(page)
    size = validate_page_size(page_size)
    offset = (resolved_page - 1) * size
    # 非空条件直接写进 WHERE，不生成恒真的 NULL 分支
    conditions: list[str] = []
    params: list[Any] = []
    start = validate_date(start_date, "start_date")
    end = validate_date(end_date, "end_date")
    if start:
        conditions.append("`signal_date` >= %s")
        params.append(start)
    if end:
        conditions.append("`signal_date` <= %s")
        params.append(end)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return (
        f"SELECT `signal_date`, `result_json` FROM {table_ref()} {where} "
        f"ORDER BY `signal_date` DESC LIMIT {size} OFFSET {offset}",
        tuple(params),
    )


def _range_query(
    template: str, start_date: str | None, end_date: str | None
) -> tuple[str, tuple[Any, ...]]:
    conditions: list[str] = []
    params: list[Any] = []
    start = validate_date(start_date, "start_date")
    end = validate_date(end_date, "end_date")
    if start:
        conditions.append("`signal_date` >= %s")
        params.append(start)
    if end:
        conditions.append("`signal_date` <= %s")
        params.append(end)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    return (
        f"{template.format(table=table_ref())} {where}".strip(),
        tuple(params),
    )


def dashboard_sql(day: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """Q7/Q8：选定日及之前的全部历史（升序）。

    一次 SELECT 取回历史与选定行，避免多次查询夹进不同批次的数据。
    K 线**不含选定日之后的行情**；3M/6M 取选定行自身的 forecast。
    """
    date = validate_date(day)
    if date:
        return (
            f"SELECT `signal_date`, `result_json` FROM {table_ref()} "
            f"WHERE `signal_date` <= %s ORDER BY `signal_date`",
            (date,),
        )
    return (
        f"SELECT `signal_date`, `result_json` FROM {table_ref()} "
        f"ORDER BY `signal_date`",
        (),
    )


def intervals_sql(day: str) -> tuple[str, tuple[Any, ...]]:
    """Q10/Q11：指定日期的 3M/6M 节点与完整模型 JSON。"""
    date = validate_interval(day)
    return (
        f"SELECT `signal_date`, "
        f"JSON_UNQUOTE(JSON_EXTRACT(`result_json`, '$.forecast.basis')) AS basis, "
        f"JSON_EXTRACT(`result_json`, '$.forecast.nodes[0]') AS node_3m, "
        f"JSON_EXTRACT(`result_json`, '$.forecast.nodes[1]') AS node_6m, "
        f"JSON_EXTRACT(`result_json`, '$.forecast.model') AS model_json "
        f"FROM {table_ref()} WHERE `signal_date` = %s",
        (date,),
    )
