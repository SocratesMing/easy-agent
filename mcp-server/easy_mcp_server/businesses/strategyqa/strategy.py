"""策略问数领域层（表 `fmut2_strategy_manage`）。

**权限过滤**（两个条件都是硬性的，调用方无法放松）：

1. 行级：`AUTHOR = 当前登录账号`，只看自己创建的策略
2. 可见性：`AUTH_VIEW = 1`，源库标记为 0（不可查看）的记录对**任何人都不返回**，
   包括作者本人

因此主应用的用户名必须与表中的 `AUTHOR` 一致；不一致时查询结果为空，
`summarize` 会明确指出"账号名下没有策略"以便排查。

几个容易踩的点，都在这里统一兜住：

1. `CREATE_TIME` / `UPDATE_TIME` 是**毫秒时间戳**（如 1789952000000），
   不是 `YYYYMMDDHHMMSS`、也不是秒
2. `PROFIT_LOSS_CHART` 是 text，可能很大 —— **列表查询绝不 SELECT 它**，
   详情查询也不默认带出
3. `STRATEGY_TAGS` 是逗号分隔字符串（"股票,多因子,增强"），只能 LIKE 模糊匹配
4. `TOTAL_YIELD` 是比例（0.155 表示 15.5%），不是百分数
5. `REPORT_TYPE` 可能为 NULL，比较/排序时要注意
6. `AUTH_VIEW` 仅作为信息返回，不参与过滤（隔离靠 AUTHOR）
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Iterable

from ...env import load_env
from ...querykit import max_rows

DEFAULT_TABLE = "fmut2_strategy_manage"

# 列表与详情都不带 PROFIT_LOSS_CHART（大字段）
LIST_COLUMNS: tuple[str, ...] = (
    "STRATEGY_ID",
    "STRATEGY_NAME",
    "STRATEGY_TAGS",
    "STRATEGY_TYPE",
    "AUTHOR",
    "RP_NUM",
    "STRATEGY_SOURCE",
    "AUTH_VIEW",
    "REPORT_TYPE",
    "TOTAL_YIELD",
    "BANK_ID",
    "CREATE_TIME",
    "UPDATE_TIME",
)

DETAIL_EXTRA_COLUMNS: tuple[str, ...] = (
    "STRATEGY_DESC",
    "STRATEGY_ADDR",
    "REPORT_ID",
)

STRATEGY_TYPES: tuple[str, ...] = ("自营", "做市")
REPORT_TYPE_LABELS: dict[int, str] = {1: "回测", 2: "仿真", 3: "实盘"}

# 排序字段白名单：模型只能传这些 key，避免 ORDER BY 注入
ORDER_BY_COLUMNS: dict[str, str] = {
    "update_time": "UPDATE_TIME",
    "create_time": "CREATE_TIME",
    "total_yield": "TOTAL_YIELD",
    "rp_num": "RP_NUM",
    "strategy_id": "STRATEGY_ID",
}

DEFAULT_ROW_LIMIT = 50


def table_name() -> str:
    load_env()
    return os.environ.get("STRATEGY_TABLE", "").strip() or DEFAULT_TABLE


def row_limit(limit: int | None = None, default: int = DEFAULT_ROW_LIMIT) -> int:
    return max(1, min(int(limit) if limit else default, max_rows()))


# ── 时间（毫秒时间戳） ──────────────────────────────────────────────────


def format_millis(value: Any) -> str:
    """把毫秒时间戳渲染成 `YYYY-MM-DD HH:MM:SS`（本地时区）。"""
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return ""
    if millis <= 0:
        return ""
    # 兼容误传秒级时间戳的情况（小于 1e11 视为秒）
    seconds = millis / 1000 if millis > 100_000_000_000 else millis
    return datetime.fromtimestamp(seconds).strftime("%Y-%m-%d %H:%M:%S")


def _add_readable_time(row: dict[str, Any]) -> dict[str, Any]:
    row["create_time_text"] = format_millis(row.get("CREATE_TIME"))
    row["update_time_text"] = format_millis(row.get("UPDATE_TIME"))
    report_type = row.get("REPORT_TYPE")
    row["report_type_text"] = (
        REPORT_TYPE_LABELS.get(int(report_type), str(report_type))
        if report_type is not None
        else None
    )
    return row


# ── SQL 构造（全部强制带 AUTHOR 条件） ──────────────────────────────────


def _like_params(keyword: str) -> tuple[str, list[str]]:
    clause = "(STRATEGY_ID LIKE %s OR STRATEGY_NAME LIKE %s OR STRATEGY_TAGS LIKE %s)"
    value = f"%{keyword}%"
    return clause, [value, value, value]


def list_strategies_sql(
    viewer: str,
    keyword: str | None = None,
    strategy_type: str | None = None,
    source: str | None = None,
    report_type: int | None = None,
    order_by: str = "update_time",
    descending: bool = True,
    limit: int | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """当前账号可查看的策略清单。

    `viewer` 是必填的登录账号，会被强制作为第一个过滤条件（`AUTHOR = %s`），
    并叠加 `AUTH_VIEW = 1`。两个条件都不可通过参数放松。
    """
    # 两个硬条件固定放在最前，便于测试与审计；AUTH_VIEW 是常量，不占参数位
    conditions: list[str] = ["AUTHOR = %s", "AUTH_VIEW = 1"]
    params: list[Any] = [str(viewer)]

    if keyword and keyword.strip():
        clause, likes = _like_params(keyword.strip())
        conditions.append(clause)
        params.extend(likes)
    if strategy_type and strategy_type.strip():
        conditions.append("STRATEGY_TYPE = %s")
        params.append(strategy_type.strip())
    if source and source.strip():
        conditions.append("STRATEGY_SOURCE = %s")
        params.append(source.strip())
    if report_type is not None:
        conditions.append("REPORT_TYPE = %s")
        params.append(int(report_type))

    where = f" WHERE {' AND '.join(conditions)}"
    order_column = ORDER_BY_COLUMNS.get((order_by or "").strip().lower(), "UPDATE_TIME")
    direction = "DESC" if descending else "ASC"
    sql = (
        f"SELECT {', '.join(LIST_COLUMNS)} FROM {table_name()}{where} "
        f"ORDER BY {order_column} {direction} LIMIT {row_limit(limit)}"
    )
    return sql, tuple(params)


def get_strategy_sql(viewer: str, strategy_id: str) -> tuple[str, tuple[Any, ...]]:
    """当前账号可查看的某个策略详情（同样叠加 AUTHOR 与 AUTH_VIEW 条件）。"""
    columns = LIST_COLUMNS + DETAIL_EXTRA_COLUMNS
    sql = (
        f"SELECT {', '.join(columns)} FROM {table_name()} "
        f"WHERE AUTHOR = %s AND AUTH_VIEW = 1 AND STRATEGY_ID = %s LIMIT 1"
    )
    return sql, (str(viewer), str(strategy_id).strip())


def decorate(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """补可读时间与枚举文案，便于模型直接引用。"""
    return [_add_readable_time(dict(row)) for row in rows]


def summarize(rows: list[dict[str, Any]], viewer: str) -> str:
    if not rows:
        # 带上账号名 + 说明两种可能：
        # 1) 账号与 AUTHOR 不一致（映射问题）2) 记录被 AUTH_VIEW=0 过滤掉了
        return (
            f"账号 {viewer} 名下没有可查看的策略"
            "（可能确实没有，或对应记录被 AUTH_VIEW=0 标记为不可查看）。"
        )
    types: dict[str, int] = {}
    for row in rows:
        key = str(row.get("STRATEGY_TYPE") or "未标注")
        types[key] = types.get(key, 0) + 1
    detail = "、".join(f"{k} {v} 个" for k, v in types.items())
    return f"账号 {viewer} 名下共 {len(rows)} 个策略：{detail}。"


def schema_help() -> dict[str, Any]:
    """给模型看的口径说明：字段语义、枚举、时间格式与可见性规则。"""
    return {
        "table": table_name(),
        "columns": {
            "STRATEGY_ID": "策略ID（主键，如 QL001）",
            "STRATEGY_NAME": "策略名称",
            "STRATEGY_TAGS": "标签，逗号分隔（如 '股票,多因子,增强'），只能 LIKE 匹配",
            "STRATEGY_TYPE": "策略类别：自营 / 做市",
            "AUTHOR": "策略作者；**查询结果只包含当前登录账号自己创建的策略**",
            "RP_NUM": "回测报告数量",
            "STRATEGY_DESC": "描述（详情才返回）",
            "STRATEGY_ADDR": "策略地址（详情才返回）",
            "STRATEGY_SOURCE": "策略来源编码：LOC / REM / WS（源库枚举，无中文对照）",
            "AUTH_VIEW": "源库可见标记：0 表示不可查看（对任何人都不返回），1 表示可查看",
            "REPORT_TYPE": "报告类型：1 回测 / 2 仿真 / 3 实盘，可能为 NULL",
            "PROFIT_LOSS_CHART": "损益表（text 大字段，本服务不返回）",
            "TOTAL_YIELD": "最大收益率，**比例值**（0.155 = 15.5%）",
            "REPORT_ID": "报告 id（详情才返回）",
            "BANK_ID": "用户所属机构",
            "CREATE_TIME": "创建时间：毫秒时间戳（如 1789952000000）",
            "UPDATE_TIME": "更新时间：毫秒时间戳",
        },
        "enums": {
            "strategy_type": list(STRATEGY_TYPES),
            "report_type": {str(k): v for k, v in REPORT_TYPE_LABELS.items()},
            "strategy_source": ["LOC", "REM", "WS"],
        },
        "time": "CREATE_TIME / UPDATE_TIME 是毫秒时间戳，过滤示例："
        "UPDATE_TIME >= 1789952000000（用整数比较，不要转成字符串）",
        "visibility": "两层过滤：1) 只能看到 AUTHOR 等于当前登录账号的策略；"
        "2) AUTH_VIEW=0 的记录对任何人都不返回（包括作者本人）",
        "notes": [
            "PROFIT_LOSS_CHART 是大字段，本服务不返回",
            "TOTAL_YIELD 是比例，展示成百分比要 ×100",
            "STRATEGY_TAGS 是逗号分隔字符串，精确匹配某标签用 LIKE '%标签%'",
            "按作者筛选没有意义——结果始终只有自己",
        ],
        "typical_questions": {
            "我有哪些策略": "list_strategies()",
            "按类型筛选": "list_strategies(strategy_type='自营')",
            "收益率最高的策略": "list_strategies(order_by='total_yield')",
            "某个策略的详情": "get_strategy(strategy_id='QL001')",
        },
    }
