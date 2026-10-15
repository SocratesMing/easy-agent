"""美国 CPI 预测 MCP 工具（四张表，三个 JSON 文本列，只读）。

挂载路径：``/mcp/us_cpi/``

工具划分与 ``docs/MCP_docs/美国CPI预测_SQL清单_MCP信息.md`` 第四节一致。
返回契约：**无数据不是错误**——列表给空 ``items``，单条给 ``found=False``；
只有参数非法才返回 ``ok=False``。

三条在工具层固化的口径：

1. **先定位报告**：除官方历史外，先把 ``report_id`` 解析成一个确定的值
   （给了就用它，否则取最新 ready 报告），**后续子查询共用同一个 id**，
   不在子查询之间重新选最新版本；
2. 三个预测数组是 LONGTEXT，**先解析成数组再在 Python 里筛选排序**
   （不用 ``JSON_TABLE``）；
3. ``yoy`` / ``nsa_mom`` / ``sa_mom`` / ``weight`` 是**百分数**（0.4 表示 0.4%），
   原样返回不换算；``gap`` 与 ``correction`` 是百分点。

并发：MCP SDK 对同步工具函数是直接调用、不丢线程池，而 pymysql 是阻塞调用，
因此涉 IO 的工具都写成 ``async def`` + ``anyio.to_thread.run_sync``。
"""

from __future__ import annotations

import logging
from typing import Any

import anyio
from mcp.server.fastmcp import FastMCP

from ...context import current_username
from ...querykit import json_safe, policy_from_env, readonly_query
from . import us_cpi

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "US CPI forecast assistant, read-only over four tables. Call describe_us_cpi_schema "
    "before querying. yoy, nsa_mom, sa_mom and weight are PERCENTAGES (0.4 means 0.4%), "
    "not 0-1 ratios - never multiply them by 100. gap (model minus market) and the daily "
    "correction are in percentage points. Every tool except the official history first "
    "resolves one report: an explicit report_id if given, otherwise the newest report "
    "with status 'ready'; that same report_id is used for all sub-queries in one call, "
    "never re-selected. The three forecast arrays are LONGTEXT columns: they are parsed "
    "and filtered here, so group and item names must match the stored text exactly - a "
    "name that is absent returns an empty list and a warning, and names are never "
    "renamed (for example '核心服务'). main and far are different records and must not "
    "be merged. In the daily scenario, baseline is that scenario's own start-of-month "
    "year-over-year baseline, NOT the headline SA month-over-month baseline; "
    "event_sources is stored per group per day and must never be summed across groups. "
    "The market median is taken as provided by the source file and is never recomputed. "
    "model_generated_at is when the model ran, not when the strategy was written. "
    "Nulls stay null and a real zero stays zero."
)


def _policy():
    """结构探索的可见性策略（缺省收敛到本场景四张表）。"""
    return policy_from_env("US_CPI_", default_allowed=us_cpi.default_allowed_tables())


async def _query(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return await anyio.to_thread.run_sync(readonly_query, sql, params)


def _base() -> dict[str, Any]:
    return {"model_name": us_cpi.MODEL_NAME}


async def _resolve_report(report_id: Any = None) -> dict[str, Any] | None:
    """解析出确定的报告行；没有 ready 报告时返回 None。"""
    resolved = us_cpi.validate_report_id(report_id)
    rows = await _query(*us_cpi.resolve_report_sql(resolved))
    return rows[0] if rows else None


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    """报告元信息（report_id / 各来源月份）。"""
    return {
        "report_id": row.get("report_id"),
        "forecast_month": (
            str(row["forecast_month"]) if row.get("forecast_month") else None
        ),
        "official_latest_month": (
            str(row["official_latest_month"])
            if row.get("official_latest_month")
            else None
        ),
        "market_month": str(row["market_month"]) if row.get("market_month") else None,
        "strategy_month": (
            str(row["strategy_month"]) if row.get("strategy_month") else None
        ),
        "generated_at": str(row["generated_at"]) if row.get("generated_at") else None,
        "imported_at": str(row["imported_at"]) if row.get("imported_at") else None,
    }


async def _domain_query(
    domain: str,
    tool: str,
    username: str,
    build_query,
    format_rows,
) -> dict[str, Any]:
    """Run a fixed parameterized domain query and return a consistent MCP result."""
    try:
        sql, params = build_query()
    except ValueError as e:
        return {"ok": False, "error": str(e)}

    try:
        rows = await _query(sql, params)
    except Exception as e:
        logger.warning(f"[us_cpi] {tool} 失败 | 域={domain} | 用户: {username} | {e}")
        return {"ok": False, "error": str(e)}

    try:
        result = format_rows(rows)
    except Exception as e:
        logger.warning(
            f"[us_cpi] {tool} 结果处理失败 | 域={domain} | 用户: {username} | {e}"
        )
        return {"ok": False, "error": "查询结果处理失败"}
    result.setdefault("ok", True)
    logger.info(f"[us_cpi] {tool} | 域={domain} | 用户: {username} | 行数={len(rows)}")
    return json_safe(result)


def _empty_items(warnings: list[str] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {**_base(), "count": 0, "items": []}
    if warnings:
        out["warnings"] = warnings
    return out


def build() -> FastMCP:
    mcp = FastMCP(
        "美国CPI预测",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── 口径 ────────────────────────────────────────────────────────────

    @mcp.tool()
    def describe_us_cpi_schema() -> dict[str, Any]:
        """Return the data dictionary for US CPI forecasts: the four tables, the report
        resolution rule, the percentage units and the pitfalls. Call this before any
        query.
        """
        username = current_username()
        logger.info(f"[us_cpi] describe_us_cpi_schema | 用户: {username}")
        return {"ok": True, **us_cpi.schema_help()}

    # ── 场景 1：报告版本 ────────────────────────────────────────────────

    @mcp.tool()
    async def get_cpi_reports(
        report_id: int | None = None,
        forecast_month: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Report versions and the month each source belongs to: model forecast month,
        latest official month at generation time, market month and strategy month.
        report_id optional; forecast_month filters the model month; limit 1-50,
        default 1 (newest first). Months may legitimately differ between sources - they
        are reported as stored and never forced to agree.
        """
        username = current_username()
        return await _domain_query(
            "us_cpi",
            "get_cpi_reports",
            username,
            lambda: us_cpi.reports_sql(report_id, forecast_month, limit),
            lambda rows: {
                **_base(),
                "count": len(rows),
                "items": [_meta(row) for row in rows],
            },
        )

    # ── 场景 2：组级预测 ────────────────────────────────────────────────

    @mcp.tool()
    async def get_cpi_group_forecasts(
        report_id: int | None = None,
        forecast_month: str | None = None,
        scope: str = "main",
        group: str | None = None,
    ) -> dict[str, Any]:
        """Group-level forecasts (总体 / 核心 / 食品 / 能源 and whatever other group
        names the source stored), with weight, YoY, NSA and SA month-over-month. All
        values are percentages. scope: main (default) / far / all - main and far are
        separate records and are never merged. forecast_month omitted means the
        report's model month for scope=main, and all stored months for far/all. group
        matches the stored Chinese name exactly; an unknown name returns an empty list
        with a warning rather than a renamed substitute. No baseline or model confidence
        is returned here.
        """
        username = current_username()

        async def run() -> dict[str, Any]:
            try:
                chosen_scope = us_cpi.validate_scope(scope)
                wanted_month = us_cpi.validate_month(forecast_month, "forecast_month")
            except ValueError as e:
                return {"ok": False, "error": str(e)}

            report = await _resolve_report(report_id)
            if report is None:
                return _empty_items(["没有 status='ready' 的报告"])
            resolved_id = report.get("report_id")

            rows = await _query(*us_cpi.prediction_arrays_sql(resolved_id))
            if not rows:
                return _empty_items(["该报告没有可读取的预测数组"])
            items = us_cpi.parse_json_text(rows[0].get("groups_json"))

            if chosen_scope != "all":
                items = [
                    it
                    for it in items
                    if str(it.get("record_type") or "").strip().lower() == chosen_scope
                ]
            if wanted_month:
                items = [
                    it
                    for it in items
                    if str(it.get("forecast_month") or "") == wanted_month
                ]
            elif chosen_scope == us_cpi.RECORD_MAIN:
                # main 且未给月份：用报告自身的模型预测月
                month = str(report.get("forecast_month") or "")
                items = [
                    it for it in items if str(it.get("forecast_month") or "") == month
                ]

            wanted_group = str(group or "").strip()
            warning: list[str] = []
            if wanted_group:
                matched = [
                    it
                    for it in items
                    if str(it.get("group_name") or "").strip() == wanted_group
                ]
                if not matched:
                    available = sorted(
                        {str(it.get("group_name")) for it in items if it.get("group_name")}
                    )
                    warning.append(
                        f"未提供该组名：{wanted_group}；本报告中可选："
                        f"{', '.join(available) if available else '（无）'}"
                    )
                items = matched

            items.sort(
                key=lambda it: (
                    str(it.get("forecast_month") or ""),
                    str(it.get("group_name") or ""),
                    str(it.get("record_type") or ""),
                )
            )
            out: dict[str, Any] = {
                **_base(),
                "report": _meta(report),
                "scope": chosen_scope,
                "forecast_month": wanted_month,
                "group": wanted_group or None,
                "count": len(items),
                "items": [us_cpi.group_rows(it) for it in items],
            }
            if warning:
                out["warnings"] = warning
            return out

        try:
            result = await run()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"[us_cpi] get_cpi_group_forecasts 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(
            f"[us_cpi] get_cpi_group_forecasts | 用户: {username} | "
            f"条目={result.get('count', 0)}"
        )
        return json_safe({"ok": True, **result})

    # ── 场景 3：分项明细 ────────────────────────────────────────────────

    @mcp.tool()
    async def get_cpi_item_forecasts(
        report_id: int | None = None,
        group: str | None = None,
        item: str | None = None,
    ) -> dict[str, Any]:
        """Line-item detail for the report's model month: item name, group, weight,
        method and the YoY / NSA / SA figures (all percentages). Sorted by weight
        descending then item name ascending, with null weights last. group and item match
        the stored names exactly. Returns the number of items actually stored - there is
        no fixed count of 26, and missing entries are not invented. Use report_id to
        look at an older version.
        """
        username = current_username()

        async def run() -> dict[str, Any]:
            report = await _resolve_report(report_id)
            if report is None:
                return _empty_items(["没有 status='ready' 的报告"])
            resolved_id = report.get("report_id")
            month = str(report.get("forecast_month") or "")

            rows = await _query(*us_cpi.prediction_arrays_sql(resolved_id))
            if not rows:
                return _empty_items(["该报告没有可读取的预测数组"])
            items = us_cpi.parse_json_text(rows[0].get("leaves_json"))
            items = [it for it in items if str(it.get("forecast_month") or "") == month]

            wanted_group = str(group or "").strip()
            wanted_item = str(item or "").strip()
            warnings: list[str] = []
            if wanted_group:
                items = [
                    it
                    for it in items
                    if str(it.get("group_name") or "").strip() == wanted_group
                ]
                if not items:
                    warnings.append(f"该报告中没有所属组为「{wanted_group}」的分项")
            if wanted_item:
                items = [
                    it
                    for it in items
                    if str(it.get("item_name") or "").strip() == wanted_item
                ]
                if not items:
                    warnings.append(f"该报告中没有名为「{wanted_item}」的分项")

            items.sort(
                key=lambda it: (
                    us_cpi._as_float(it.get("weight")) is None,
                    -(us_cpi._as_float(it.get("weight")) or 0.0),
                    str(it.get("item_name") or ""),
                )
            )
            out: dict[str, Any] = {
                **_base(),
                "report": _meta(report),
                "forecast_month": month or None,
                "group": wanted_group or None,
                "item": wanted_item or None,
                "count": len(items),
                "items": [us_cpi.leaf_rows(it) for it in items],
            }
            if warnings:
                out["warnings"] = warnings
            return out

        try:
            result = await run()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"[us_cpi] get_cpi_item_forecasts 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(
            f"[us_cpi] get_cpi_item_forecasts | 用户: {username} | "
            f"条目={result.get('count', 0)}"
        )
        return json_safe({"ok": True, **result})

    # ── 场景 4：逐日演变 ────────────────────────────────────────────────

    @mcp.tool()
    async def get_cpi_daily_evolution(
        report_id: int | None = None,
        group: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        include_future: bool = False,
    ) -> dict[str, Any]:
        """Day-by-day forecast for the report's target month: YoY, the scenario-specific
        start-of-month baseline, and the cumulative correction in percentage points.
        Dates default to the first and last day of the target month and must fall inside
        it; start must not be later than end. group filters 总体 / 核心 / 食品 / 能源.
        include_future defaults to false, so display-dated future rows are excluded.
        Missing dates or groups are never filled in. Note: baseline here is this
        scenario's own YoY baseline, NOT the headline SA MoM baseline, and event_sources
        is duplicated per group per day - never sum event counts across groups.
        """
        username = current_username()

        async def run() -> dict[str, Any]:
            report = await _resolve_report(report_id)
            if report is None:
                return _empty_items(["没有 status='ready' 的报告"])
            resolved_id = report.get("report_id")
            month = str(report.get("forecast_month") or "")

            start = us_cpi.validate_date(start_date, "start_date") or (
                us_cpi.month_start(month) if month else None
            )
            end = us_cpi.validate_date(end_date, "end_date") or (
                us_cpi.month_end(month) if month else None
            )
            if start and end and start > end:
                return {"ok": False, "error": f"start_date 不能晚于 end_date（{start} > {end}）"}

            rows = await _query(*us_cpi.prediction_arrays_sql(resolved_id))
            if not rows:
                return _empty_items(["该报告没有可读取的预测数组"])
            items = us_cpi.parse_json_text(rows[0].get("daily_json"))
            items = [it for it in items if str(it.get("target_month") or "") == month]

            wanted_group = str(group or "").strip()
            if wanted_group:
                items = [
                    it
                    for it in items
                    if str(it.get("group_name") or "").strip() == wanted_group
                ]
            if start:
                items = [
                    it for it in items if str(it.get("observation_date") or "") >= start
                ]
            if end:
                items = [
                    it for it in items if str(it.get("observation_date") or "") <= end
                ]
            if not include_future:
                items = [
                    it
                    for it in items
                    if us_cpi._as_int(it.get("is_future_display")) in (0, None)
                ]

            items.sort(
                key=lambda it: (
                    str(it.get("observation_date") or ""),
                    str(it.get("group_name") or ""),
                )
            )
            return {
                **_base(),
                "report": _meta(report),
                "target_month": month or None,
                "group": wanted_group or None,
                "start_date": start,
                "end_date": end,
                "include_future": bool(include_future),
                "count": len(items),
                "items": [us_cpi.daily_rows(it) for it in items],
            }

        try:
            result = await run()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"[us_cpi] get_cpi_daily_evolution 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(
            f"[us_cpi] get_cpi_daily_evolution | 用户: {username} | "
            f"条目={result.get('count', 0)}"
        )
        return json_safe({"ok": True, **result})

    # ── 场景 5：机构预测与同月模型比较 ──────────────────────────────────

    @mcp.tool()
    async def get_cpi_market_expectations(
        report_id: int | None = None,
        forecast_month: str | None = None,
        median_only: bool = False,
        institution: str | None = None,
    ) -> dict[str, Any]:
        """Institution forecasts plus the model's own 总体 / 核心 figures for the same
        month, and the model-minus-market gaps in percentage points.
        forecast_month defaults to the report's market_month. median_only restricts to
        the median supplied by the source file (it is never recomputed here). institution
        matches the stored name exactly. Rows are ordered with the median first.
        If the model value for that month is missing, both the model value and the gap
        are null and a warning explains it. A gap is a comparison of stored values, not
        a trading signal.
        """
        username = current_username()

        async def run() -> dict[str, Any]:
            report = await _resolve_report(report_id)
            if report is None:
                return _empty_items(["没有 status='ready' 的报告"])
            resolved_id = report.get("report_id")
            effective = us_cpi.validate_month(forecast_month, "forecast_month") or (
                str(report.get("market_month") or "") or None
            )

            rows = await _query(
                *us_cpi.market_sql(resolved_id, effective, bool(median_only), institution)
            )
            warnings: list[str] = []

            # 同报告的组级预测里取 main + 该月的「总体」「核心」
            model_values: dict[str, dict[str, float | None]] = {}
            array_rows = await _query(*us_cpi.prediction_arrays_sql(resolved_id))
            if array_rows and effective:
                groups = us_cpi.parse_json_text(array_rows[0].get("groups_json"))
                for item in groups:
                    if str(item.get("record_type") or "").strip().lower() != us_cpi.RECORD_MAIN:
                        continue
                    if str(item.get("forecast_month") or "") != effective:
                        continue
                    name = str(item.get("group_name") or "").strip()
                    if name in us_cpi.MODEL_COMPARE_GROUPS:
                        model_values[name] = {
                            "yoy": us_cpi._as_float(item.get("yoy")),
                            "sa_mom": us_cpi._as_float(item.get("sa_mom")),
                        }
            missing = [
                name
                for name in us_cpi.MODEL_COMPARE_GROUPS
                if name not in model_values
            ]
            if missing and rows:
                warnings.append(
                    f"模型在 {effective} 缺少 {', '.join(missing)} 的 main 组级预测，"
                    f"对应的模型值与 gap 为 null"
                )

            def _gap(model: float | None, market: float | None) -> float | None:
                if model is None or market is None:
                    return None
                return round(model - market, 6)

            items = []
            for row in rows:
                headline_yoy = us_cpi._as_float(row.get("headline_yoy"))
                headline_mom = us_cpi._as_float(row.get("headline_mom"))
                core_yoy = us_cpi._as_float(row.get("core_yoy"))
                core_mom = us_cpi._as_float(row.get("core_mom"))
                m_head = model_values.get("总体", {})
                m_core = model_values.get("核心", {})
                items.append(
                    {
                        "report_id": row.get("report_id"),
                        "forecast_month": (
                            str(row["forecast_month"])
                            if row.get("forecast_month")
                            else None
                        ),
                        "institution_name": row.get("institution_name"),
                        "is_median": us_cpi._as_int(row.get("is_median")),
                        "headline_yoy": headline_yoy,
                        "headline_mom": headline_mom,
                        "core_yoy": core_yoy,
                        "core_mom": core_mom,
                        "model_headline_yoy": m_head.get("yoy"),
                        "model_headline_mom": m_head.get("sa_mom"),
                        "model_core_yoy": m_core.get("yoy"),
                        "model_core_mom": m_core.get("sa_mom"),
                        "headline_yoy_gap": _gap(m_head.get("yoy"), headline_yoy),
                        "headline_mom_gap": _gap(m_head.get("sa_mom"), headline_mom),
                        "core_yoy_gap": _gap(m_core.get("yoy"), core_yoy),
                        "core_mom_gap": _gap(m_core.get("sa_mom"), core_mom),
                    }
                )

            if not rows:
                warnings.append("该报告在该月份没有市场/机构预测记录")

            out: dict[str, Any] = {
                **_base(),
                "report": _meta(report),
                "effective_month": effective,
                "median_only": bool(median_only),
                "institution": str(institution or "").strip() or None,
                "count": len(items),
                "items": items,
                "units": {
                    "yoy / mom": "百分数（0.4 表示 0.4%）",
                    "gap": "百分点（模型减市场）",
                },
            }
            if warnings:
                out["warnings"] = warnings
            return out

        try:
            result = await run()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(
                f"[us_cpi] get_cpi_market_expectations 失败 | 用户: {username} | {e}"
            )
            return {"ok": False, "error": str(e)}
        logger.info(
            f"[us_cpi] get_cpi_market_expectations | 用户: {username} | "
            f"条目={result.get('count', 0)}"
        )
        return json_safe({"ok": True, **result})

    # ── 场景 6：策略建议原文 ────────────────────────────────────────────

    @mcp.tool()
    async def get_cpi_strategy_advice(
        report_id: int | None = None, forecast_month: str | None = None
    ) -> dict[str, Any]:
        """The stored strategy text and the month it belongs to. forecast_month defaults
        to the report's strategy_month (which comes from the import parameters and is
        NOT inferred from the model month). The text is returned verbatim - no new
        strategy is generated and no historical commentary in it is re-extracted as a
        model confidence score. model_generated_at is when the model ran, not when the
        strategy was written. A month with no stored strategy returns an empty list.
        """
        username = current_username()

        async def run() -> dict[str, Any]:
            report = await _resolve_report(report_id)
            if report is None:
                return _empty_items(["没有 status='ready' 的报告"])
            effective = us_cpi.validate_month(forecast_month, "forecast_month") or (
                str(report.get("strategy_month") or "") or None
            )
            rows = await _query(*us_cpi.strategy_sql(report.get("report_id"), effective))
            items = [
                {
                    "report_id": row.get("report_id"),
                    "forecast_month": (
                        str(row["forecast_month"]) if row.get("forecast_month") else None
                    ),
                    "content": row.get("content"),
                    "strategy_sha256": row.get("strategy_sha256"),
                    "model_generated_at": (
                        str(row["model_generated_at"])
                        if row.get("model_generated_at")
                        else None
                    ),
                    "imported_at": (
                        str(row["imported_at"]) if row.get("imported_at") else None
                    ),
                }
                for row in rows
            ]
            out: dict[str, Any] = {
                **_base(),
                "report": _meta(report),
                "effective_month": effective,
                "count": len(items),
                "items": items,
            }
            if not items:
                out["warnings"] = ["该月份没有已存策略建议原文"]
            return out

        try:
            result = await run()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"[us_cpi] get_cpi_strategy_advice 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(
            f"[us_cpi] get_cpi_strategy_advice | 用户: {username} | "
            f"条目={result.get('count', 0)}"
        )
        return json_safe({"ok": True, **result})

    # ── 场景 7：官方历史 ────────────────────────────────────────────────

    @mcp.tool()
    async def get_cpi_official_history(
        indicator: str | None = None,
        start_month: str | None = None,
        end_month: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Official history: SA month-over-month, the NSA index (original base) and the
        year-over-year rate derived from the same month a year earlier. This is
        independent of any report, so report_id is NOT accepted.
        end_month omitted means the latest month with data for the indicator;
        start_month omitted means 11 months back from the end month. Newest first.
        limit 1-5000, default 1000 - when more rows exist than requested, 'truncated'
        is set to true. Indicator names are matched as stored in the workbook after
        removing the metric suffix.
        """
        username = current_username()

        async def run() -> dict[str, Any]:
            start = us_cpi.validate_month(start_month, "start_month")
            end = us_cpi.validate_month(end_month, "end_month")
            if not end:
                latest_rows = await _query(*us_cpi.official_latest_month_sql(indicator))
                end = (
                    str(latest_rows[0]["latest_month"])
                    if latest_rows and latest_rows[0].get("latest_month")
                    else None
                )
            if start and end and start > end:
                return {"ok": False, "error": f"start_month 不能晚于 end_month（{start} > {end}）"}
            if not start and end:
                start = us_cpi.shift_months(end, -(us_cpi.OFFICIAL_DEFAULT_MONTHS - 1))

            size = us_cpi.bounded_limit(
                limit, us_cpi.DEFAULT_OFFICIAL_LIMIT, us_cpi.MAX_OFFICIAL_LIMIT
            )
            rows = await _query(
                *us_cpi.official_history_sql(indicator, start, end, limit)
            )
            truncated = len(rows) > size
            items = [us_cpi.official_row(row) for row in rows[:size]]
            out: dict[str, Any] = {
                **_base(),
                "indicator": str(indicator or "").strip() or None,
                "start_month": start,
                "end_month": end,
                "truncated": truncated,
                "count": len(items),
                "items": items,
                "units": {
                    "sa_mom": "百分数（0.4 表示 0.4%）",
                    "nsa_yoy": "百分数（同比，由去年同期指数算出）",
                    "nsa_index": "指数，保持原基期，未换算",
                },
            }
            if not items:
                out["warnings"] = ["该区间没有官方历史记录"]
            return out

        try:
            result = await run()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"[us_cpi] get_cpi_official_history 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(
            f"[us_cpi] get_cpi_official_history | 用户: {username} | "
            f"条目={result.get('count', 0)}"
        )
        return json_safe({"ok": True, **result})

    return mcp
