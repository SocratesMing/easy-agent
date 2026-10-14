"""黄金价格多因子归因领域层（只读一张宽表 ``fmqt_gold_analysis_result``）。

口径要点（照 ``docs/MCP_docs/黄金价格驱动因子分析_SQL清单_MCP信息.md``）：

- 数值是**收益率口径的小数**（``0.0185`` = 1.85%），不是百分点、不是亿元
- 每行是「截至该日、近 30 个交易日」的**累计**贡献，含当日；**没有**单日贡献
- 同一 ``model_name`` + ``creation_time`` 至多一条；查询**必须带 model_name**，
  否则同一天会出现两行（回归 + 随机森林）导致数值翻倍
- 六个分组列相加 ≈ 模型拟合的累计收益，与金价真实涨跌幅有残差

``group`` 类入参是**列名**，无法用占位符保护，一律走 :data:`GROUPS` 白名单。
"""

from __future__ import annotations

import os
import re
from typing import Any

from ...env import load_env
from ...querykit import assert_identifier

TABLES: dict[str, str] = {"main": "fmqt_gold_analysis_result"}

#: 模型名（中文，来自业务页面选项）
MODELS: tuple[str, ...] = ("多元线性回归", "随机森林")
DEFAULT_MODEL = MODELS[0]

#: 六个因子分组：列名 -> 中文名。列名同时用作 ``group`` 入参的白名单。
GROUPS: dict[str, str] = {
    "comex_volume": "COMEX成交量",
    "fund_flow": "资金流向（SPDR 黄金 ETF 持有量）",
    "usd_factor": "美元因素（美元指数 + 美元兑日元 + 欧元兑美元）",
    "treasury_factor": "美债因素（10 年美债实际收益率 + 10 年盈亏通胀率）",
    "risk_sentiment": "风险情绪（WTI 原油 + VIX 波动率）",
    "commodity_factor": "商品（LME 铜）",
}
VALUE_COLUMNS: tuple[str, ...] = tuple(GROUPS)

DEFAULT_DAYS = 30
MAX_DAYS = 250
DEFAULT_LIMIT = 30
MAX_LIMIT = 50

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def schema_name() -> str:
    load_env()
    return os.environ.get("GOLD_SCHEMA", "").strip()


def table_ref(key: str = "main") -> str:
    if key not in TABLES:
        raise ValueError(f"未知数据表: {key!r}（可选 {', '.join(TABLES)}）")
    table = f"`{assert_identifier(TABLES[key])}`"
    schema = schema_name()
    return f"`{assert_identifier(schema)}`.{table}" if schema else table


def default_allowed_tables() -> tuple[str, ...]:
    """结构探索白名单缺省值：本场景唯一一张表。"""
    return tuple(TABLES.values())


def validate_model(value: str | None) -> str:
    """模型名白名单校验；留空取默认模型。"""
    text = str(value or "").strip()
    if not text:
        return DEFAULT_MODEL
    if text not in MODELS:
        raise ValueError(f"model 只能是 {' / '.join(MODELS)}（收到 {value!r}）")
    return text


def validate_group(value: str | None) -> str:
    """分组列名白名单校验。

    列名无法用预编译占位符保护，只能查表拒绝自由文本——文档里专门强调过这点。
    """
    text = str(value or "").strip()
    if text not in GROUPS:
        raise ValueError(
            f"group 只能是 {' / '.join(GROUPS)}（收到 {value!r}）"
        )
    return text


def validate_date(value: str | None, name: str = "creation_time") -> str | None:
    """校验 ``YYYY-MM-DD``；留空返回 None（表示取最新）。"""
    text = str(value or "").strip()
    if not text:
        return None
    if not _DATE_RE.fullmatch(text):
        raise ValueError(f"{name} 必须是 YYYY-MM-DD 格式（收到 {value!r}）")
    return text


def bounded_days(value: int | None) -> int:
    """回溯天数：1~250，缺省 30。"""
    requested = int(value) if value else DEFAULT_DAYS
    return max(1, min(requested, MAX_DAYS))


def bounded_limit(value: int | None) -> int:
    """返回条数：1~50，缺省 30。"""
    requested = int(value) if value else DEFAULT_LIMIT
    return max(1, min(requested, MAX_LIMIT))


# ── SQL 构造（照文档 1.x 各条） ─────────────────────────────────────────


def latest_date_sql(model: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """1.1 / 1.2：最新数据日期（可限定模型）。"""
    if model is None or not str(model).strip():
        return (
            f"SELECT MAX(`creation_time`) AS d FROM {table_ref()}",
            (),
        )
    return (
        f"SELECT MAX(`creation_time`) AS d FROM {table_ref()} WHERE `model_name` = %s",
        (validate_model(model),),
    )


def prev_date_sql(model: str | None, date: str | None) -> tuple[str, tuple[Any, ...]]:
    """1.3：给定日期之前最近的一个交易日。"""
    target = validate_date(date)
    if target is None:
        raise ValueError("creation_time 不能为空（环比需要明确基准日）")
    return (
        f"SELECT MAX(`creation_time`) AS d FROM {table_ref()} "
        "WHERE `model_name` = %s AND `creation_time` < %s",
        (validate_model(model), target),
    )


def dates_sql(model: str | None = None, days: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """1.4：最近 N 个交易日（倒序）。"""
    return (
        f"SELECT DISTINCT `creation_time` AS d FROM {table_ref()} "
        f"WHERE `model_name` = %s ORDER BY d DESC LIMIT {bounded_days(days)}",
        (validate_model(model),),
    )


def history_sql(model: str | None = None, limit: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """1.5：最近 N 条归因明细（倒序）。"""
    columns = ", ".join(f"`{col}`" for col in VALUE_COLUMNS)
    return (
        f"SELECT `creation_time`, {columns} FROM {table_ref()} "
        f"WHERE `model_name` = %s ORDER BY `creation_time` DESC LIMIT {bounded_limit(limit)}",
        (validate_model(model),),
    )


def attribution_sql(model: str | None, date: str | None) -> tuple[str, tuple[Any, ...]]:
    """1.6：某日各分组累计贡献（核心查询）。date 留空则由调用方先取最新日。"""
    columns = ", ".join(f"`{col}`" for col in VALUE_COLUMNS)
    target = validate_date(date)
    return (
        f"SELECT `creation_time`, {columns} FROM {table_ref()} "
        "WHERE `model_name` = %s AND `creation_time` = %s",
        (validate_model(model), target),
    )


def compare_models_sql(date: str | None) -> tuple[str, tuple[Any, ...]]:
    """1.7：同日两模型对照。"""
    columns = ", ".join(f"`{col}`" for col in VALUE_COLUMNS)
    return (
        f"SELECT `model_name`, `creation_time`, {columns} FROM {table_ref()} "
        "WHERE `creation_time` = %s ORDER BY `model_name`",
        (validate_date(date),),
    )


def group_trend_sql(
    model: str | None = None, days: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    """1.8：近 N 日分组贡献趋势（宽表转长表，MySQL 无 UNPIVOT 用 UNION ALL）。"""
    target_model = validate_model(model)
    window = bounded_days(days)
    parts = [
        f"SELECT `creation_time`, '{col}' AS group_key, `{col}` AS value "
        f"FROM {table_ref()} WHERE `model_name` = %s"
        for col in VALUE_COLUMNS
    ]
    sql = (
        "SELECT t.`creation_time`, t.group_key, t.value FROM (\n  "
        + "\n  UNION ALL\n  ".join(parts)
        + f"\n) t JOIN (SELECT `creation_time` FROM {table_ref()} "
        f"WHERE `model_name` = %s ORDER BY `creation_time` DESC LIMIT {window}) d "
        "ON d.`creation_time` = t.`creation_time` "
        "ORDER BY t.`creation_time` ASC, t.group_key"
    )
    return sql, (target_model,) * (len(parts) + 1)


def group_series_sql(
    model: str | None, group: str, days: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    """1.9：近 N 日单个分组走势（列名走白名单）。"""
    column = assert_identifier(validate_group(group))
    return (
        f"SELECT `creation_time`, `{column}` AS value FROM {table_ref()} "
        f"WHERE `model_name` = %s ORDER BY `creation_time` DESC LIMIT {bounded_days(days)}",
        (validate_model(model),),
    )


def model_coverage_sql() -> tuple[str, tuple[Any, ...]]:
    """1.10：各模型记录条数与覆盖区间。"""
    return (
        f"SELECT `model_name`, COUNT(*) AS rows_count, "
        f"MIN(`creation_time`) AS first_date, MAX(`creation_time`) AS last_date "
        f"FROM {table_ref()} GROUP BY `model_name`",
        (),
    )


def group_change_sql(
    model: str | None = None, days: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    """1.11：相邻两日环比变化（MySQL 8 窗口函数 LAG）。

    注意是**窗口累计值之间的差**，不等于"当天该因子的单日贡献"。
    """
    window = bounded_days(days)
    changes = ",\n       ".join(
        f"ROUND(`{col}` - LAG(`{col}`) OVER (ORDER BY `creation_time`), 4) AS `{col}_chg`"
        for col in VALUE_COLUMNS
    )
    return (
        f"SELECT `creation_time`,\n       {changes} FROM {table_ref()} "
        f"WHERE `model_name` = %s ORDER BY `creation_time` DESC LIMIT {window}",
        (validate_model(model),),
    )


def group_stats_sql(
    model: str | None = None, days: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    """1.12：近 N 日各分组的最小 / 最大 / 均值（用于给当日数值定高低）。"""
    window = bounded_days(days)
    stats = ",\n  ".join(
        f"ROUND(MIN(`{col}`), 4) AS `{col}_min`, "
        f"ROUND(MAX(`{col}`), 4) AS `{col}_max`, "
        f"ROUND(AVG(`{col}`), 4) AS `{col}_avg`"
        for col in VALUE_COLUMNS
    )
    return (
        f"SELECT\n  {stats}\nFROM (\n  SELECT * FROM {table_ref()} "
        f"WHERE `model_name` = %s ORDER BY `creation_time` DESC LIMIT {window}\n) t",
        (validate_model(model),),
    )


def total_contribution_sql(
    model: str | None = None, days: int | None = None
) -> tuple[str, tuple[Any, ...]]:
    """1.13：六组合计（模型拟合的累计收益）。"""
    window = bounded_days(days)
    total = " + ".join(f"`{col}`" for col in VALUE_COLUMNS)
    return (
        f"SELECT `creation_time`, ROUND({total}, 4) AS total_contribution "
        f"FROM {table_ref()} WHERE `model_name` = %s "
        f"ORDER BY `creation_time` DESC LIMIT {window}",
        (validate_model(model),),
    )


def schema_help() -> dict[str, Any]:
    """数据字典：口径、单位、枚举与陷阱。"""
    return {
        "table": TABLES["main"],
        "schema": schema_name() or None,
        "models": list(MODELS),
        "default_model": DEFAULT_MODEL,
        "groups": {col: GROUPS[col] for col in VALUE_COLUMNS},
        "date_rule": "creation_time 为 YYYY-MM-DD；留空取该模型最新交易日",
        "units": "所有数值均为收益率口径的小数（0.0185 表示 1.85%），非百分点、非亿元",
        "window_rule": (
            "每行是截至该日、近 30 个交易日的累计贡献（含当日）；表内没有单日贡献，"
            "相邻两行的差只是累计值之差"
        ),
        "scale": {"default_days": DEFAULT_DAYS, "max_days": MAX_DAYS, "max_limit": MAX_LIMIT},
        "pitfalls": [
            "查询必须带 model_name：同一天有两个模型各一行，漏掉会让数值翻倍",
            "六组合计是模型拟合的累计收益，与金价真实涨跌幅存在残差，不可当作涨跌幅",
            "同日两模型数值不同属于正常（不同模型对同一窗口的解释力不同）",
        ],
    }
