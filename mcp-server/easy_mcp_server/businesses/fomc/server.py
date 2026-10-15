"""美联储议息会议 MCP 工具（三张表，含图片二进制，只读）。

挂载路径：``/mcp/fomc/``

工具划分与 ``docs/MCP_docs/美联储议息会议_SQL清单_MCP信息.md`` 第 5.2 节一致。
返回契约：**无数据不是错误**——单条工具给 ``found=false`` / ``data=null``，
列表给空 ``items`` / 零计数；只有参数非法才返回 ``ok=False``。

三条在工具层固化的口径：

1. **"最新月份"分三种**（文档 §5.2）：声明原文取最新声明月；声明对比 / 两期声明 /
   完整报告取最新 **READY** 报告月；状态与图片列表取最新**已入库**报告月；
2. 报告状态 ``PROCESSING`` / ``READY`` / ``FAILED`` 分开处理，**不能把空结果
   解释成"政策没有变化"**；
3. ``image_data`` 是 BLOB —— **列表工具绝不取**，只有单张图片工具才读并转 base64。

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
from . import fomc

logger = logging.getLogger("easy-mcp-server")

INSTRUCTIONS = (
    "FOMC meeting assistant, read-only over three tables: statements, reports and "
    "report images. Call describe_fomc_schema before querying. month is YYYY-MM. "
    "'Latest month' differs by tool: statement text uses the latest statement month, "
    "comparison / statement pair / full report use the latest READY report month, and "
    "status / image lists use the newest report month regardless of status. Always read "
    "the report status first: PROCESSING means the comparison is still being generated "
    "(images may still be readable), READY means the comparison is available, FAILED "
    "means generation failed and the table stores no reason. Never answer 'no result' "
    "as 'policy did not change' - distinguish no report, in progress and failed. "
    "comparison_json is a text field parsed here; its source field is 'llm' only for "
    "real model output, 'local-demo' means a local mock and must never be presented as "
    "a model conclusion. Images are stored as BLOBs: list tools return metadata and an "
    "address only, and the single-image tool returns base64. Image content has no text "
    "field, so never infer views from file names - read the image itself. The reports "
    "table has no foreign keys, so a missing linked statement is a real data gap, not a "
    "valid empty comparison."
)


def _policy():
    """结构探索的可见性策略（缺省收敛到本场景三张表）。"""
    return policy_from_env("FOMC_", default_allowed=fomc.default_allowed_tables())


async def _query(sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    return await anyio.to_thread.run_sync(readonly_query, sql, params)


def _base() -> dict[str, Any]:
    return {"model_name": fomc.MODEL_NAME}


async def _resolve_month(kind: str) -> str | None:
    """按用途解析"最新月份"。``kind``：statement / report / ready_report。"""
    if kind == "statement":
        rows = await _query(*fomc.latest_statement_month_sql())
    elif kind == "report":
        rows = await _query(*fomc.latest_report_month_sql(False))
    else:  # ready_report
        rows = await _query(*fomc.latest_report_month_sql(True))
    if not rows:
        return None
    value = rows[0].get("month")
    return str(value) if value is not None else None


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
        logger.warning(f"[fomc] {tool} 失败 | 域={domain} | 用户: {username} | {e}")
        return {"ok": False, "error": str(e)}

    try:
        result = format_rows(rows)
    except Exception as e:
        logger.warning(
            f"[fomc] {tool} 结果处理失败 | 域={domain} | 用户: {username} | {e}"
        )
        return {"ok": False, "error": "查询结果处理失败"}
    result.setdefault("ok", True)
    logger.info(f"[fomc] {tool} | 域={domain} | 用户: {username} | 行数={len(rows)}")
    return json_safe(result)


def _not_found(month: str | None = None, reason: str = "no_record") -> dict[str, Any]:
    out: dict[str, Any] = {**_base(), "found": False, "data": None, "reason": reason}
    if month:
        out["month"] = month
    return out


def _report_list(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """报告列表：附状态中文说明，方便区分处理中/失败。"""
    items = []
    for row in rows:
        status = row.get("status")
        items.append(
            {
                "report_id": row.get("report_id"),
                "month": str(row["month"]) if row.get("month") else None,
                "status": status,
                "status_note": fomc.STATUS_LABELS.get(str(status), None),
                "current_statement_id": row.get("current_statement_id"),
                "previous_statement_id": row.get("previous_statement_id"),
                "created_at": str(row["created_at"]) if row.get("created_at") else None,
                "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
            }
        )
    return {**_base(), "count": len(items), "items": items}


def _images(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """图片列表：只给元数据与地址，绝不返回 BLOB。"""
    items = [fomc.image_meta(row) for row in rows]
    return {
        **_base(),
        "count": len(items),
        "items": items,
        "note": "这里只有元数据；要图片内容请用单张图片工具（返回 base64）",
    }


def _split_images(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """跨模块图片：按 section 分成两组返回。"""
    policy_view, market = [], []
    for row in rows:
        item = fomc.image_meta(row)
        if item.get("section") == fomc.SECTION_POLICY:
            policy_view.append(item)
        elif item.get("section") == fomc.SECTION_MARKET:
            market.append(item)
    return {
        **_base(),
        "section2_images": policy_view,
        "section3_images": market,
        "count": len(policy_view) + len(market),
    }


def build() -> FastMCP:
    mcp = FastMCP(
        "美联储议息会议",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        stateless_http=True,
    )

    # ── 口径 ────────────────────────────────────────────────────────────

    @mcp.tool()
    def describe_fomc_schema() -> dict[str, Any]:
        """Return the data dictionary for FOMC meetings: the three tables, month format,
        report statuses, image sections and the pitfalls. Call this before any query.
        """
        username = current_username()
        logger.info(f"[fomc] describe_fomc_schema | 用户: {username}")
        return {"ok": True, **fomc.schema_help()}

    # ── 通用 ────────────────────────────────────────────────────────────

    @mcp.tool()
    async def get_latest_fomc_month(source: str = "ready_report") -> dict[str, Any]:
        """Latest available month. source: 'statement' = latest statement month,
        'report' = newest report month (may be PROCESSING or FAILED), 'ready_report'
        = latest READY report month (default). Returns null when nothing is stored.
        """
        username = current_username()
        kind = str(source or "ready_report").strip().lower()
        if kind not in ("statement", "report", "ready_report"):
            return {
                "ok": False,
                "error": "source 只支持 statement / report / ready_report",
            }
        try:
            month = await _resolve_month(kind)
        except Exception as e:
            logger.warning(f"[fomc] get_latest_fomc_month 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(
            f"[fomc] get_latest_fomc_month | 用户: {username} | source={kind} | {month}"
        )
        return json_safe({"ok": True, **_base(), "source": kind, "month": month})

    @mcp.tool()
    async def list_fomc_reports(
        limit: int | None = None, status: str | None = None
    ) -> dict[str, Any]:
        """Recent reports with status, newest first (metadata only, no JSON or BLOB).
        limit: 1-120, default 24. status optional: PROCESSING / READY / FAILED.
        """
        username = current_username()
        return await _domain_query(
            "fomc",
            "list_fomc_reports",
            username,
            lambda: fomc.reports_sql(limit, status),
            _report_list,
        )

    @mcp.tool()
    async def get_fomc_report_status(month: str | None = None) -> dict[str, Any]:
        """Generation status of one report. month omitted means the newest report month
        (which may still be PROCESSING or FAILED - that is intentional). Distinguish
        PROCESSING / READY / FAILED; a missing row means no report for that month.
        """
        username = current_username()

        async def run() -> dict[str, Any]:
            resolved = fomc.validate_month(month) or await _resolve_month("report")
            if not resolved:
                return {**_base(), "found": False, "data": None, "reason": "no_report"}
            rows = await _query(*fomc.report_status_sql(resolved))
            if not rows:
                return {**_base(), "found": False, "month": resolved, "data": None}
            row = rows[0]
            status = row.get("status")
            return {
                **_base(),
                "found": True,
                "month": resolved,
                "report_id": row.get("report_id"),
                "status": status,
                "status_note": fomc.STATUS_LABELS.get(str(status)),
                "comparison_ready": str(status) == "READY",
                "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
            }

        try:
            result = await run()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"[fomc] get_fomc_report_status 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(f"[fomc] get_fomc_report_status | 用户: {username}")
        return json_safe({"ok": True, **result})

    # ── FOMC 声明对比 ───────────────────────────────────────────────────

    @mcp.tool()
    async def get_fomc_statement(month: str | None = None) -> dict[str, Any]:
        """Full statement text for one meeting month. month omitted means the latest
        STATEMENT month (not the latest report month).
        """
        username = current_username()

        async def run() -> dict[str, Any]:
            resolved = fomc.validate_month(month) or await _resolve_month("statement")
            if not resolved:
                return _not_found(None, "no_statement")
            rows = await _query(*fomc.statement_sql(resolved))
            if not rows:
                return _not_found(resolved, "no_statement")
            row = rows[0]
            return {
                **_base(),
                "found": True,
                "month": resolved,
                "data": {
                    "statement_id": row.get("statement_id"),
                    "file_name": row.get("file_name"),
                    "file_hash": row.get("file_hash"),
                    "content": row.get("content"),
                    "created_at": str(row["created_at"]) if row.get("created_at") else None,
                    "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
                },
            }

        try:
            result = await run()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"[fomc] get_fomc_statement 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(f"[fomc] get_fomc_statement | 用户: {username}")
        return json_safe({"ok": True, **result})

    @mcp.tool()
    async def get_previous_fomc_statement(month: str) -> dict[str, Any]:
        """The nearest earlier statement stored in the database. month is REQUIRED.
        This is NOT the previous calendar month; for a report's own two linked
        statements use get_fomc_statement_pair instead.
        """
        username = current_username()
        return await _domain_query(
            "fomc",
            "get_previous_fomc_statement",
            username,
            lambda: fomc.previous_statement_sql(month),
            lambda rows: (
                _not_found(month, "no_previous_statement")
                if not rows
                else {
                    **_base(),
                    "found": True,
                    "month": month,
                    "data": {
                        "statement_id": rows[0].get("statement_id"),
                        "month_of_statement": (
                            str(rows[0]["month"]) if rows[0].get("month") else None
                        ),
                        "file_name": rows[0].get("file_name"),
                        "file_hash": rows[0].get("file_hash"),
                        "content": rows[0].get("content"),
                    },
                }
            ),
        )

    @mcp.tool()
    async def get_fomc_statement_pair(month: str | None = None) -> dict[str, Any]:
        """The two statements actually linked by one report (current and previous), read
        through the report's own IDs. month omitted means the latest READY report month.
        The tables have no foreign keys, so a missing side is a real data gap - it is
        reported as such and never treated as a valid comparison.
        """
        username = current_username()

        async def run() -> dict[str, Any]:
            resolved = fomc.validate_month(month) or await _resolve_month("ready_report")
            if not resolved:
                return _not_found(None, "no_ready_report")
            rows = await _query(*fomc.statement_pair_sql(resolved))
            if not rows:
                return _not_found(resolved, "no_report")
            row = rows[0]
            return {
                **_base(),
                "found": True,
                "month": resolved,
                "report_id": row.get("report_id"),
                "status": row.get("status"),
                "current": {
                    "statement_id": row.get("current_statement_id"),
                    "month": str(row["current_month"]) if row.get("current_month") else None,
                    "file_name": row.get("current_file_name"),
                    "content": row.get("current_content"),
                },
                "previous": {
                    "statement_id": row.get("previous_statement_id"),
                    "month": str(row["previous_month"]) if row.get("previous_month") else None,
                    "file_name": row.get("previous_file_name"),
                    "content": row.get("previous_content"),
                },
                "linked_missing": (
                    row.get("current_content") is None
                    or row.get("previous_content") is None
                ),
            }

        try:
            result = await run()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"[fomc] get_fomc_statement_pair 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(f"[fomc] get_fomc_statement_pair | 用户: {username}")
        return json_safe({"ok": True, **result})

    @mcp.tool()
    async def get_fomc_comparison(
        month: str | None = None, category: str | None = None
    ) -> dict[str, Any]:
        """The completed statement comparison for one month: previous vs current per
        category, with HTML stripped to plain text. month omitted means the latest READY
        report month; category optional filters to one category. Only READY reports are
        returned - check get_fomc_report_status first if it is empty. The source field
        is kept: 'llm' means model output, 'local-demo' is a local mock and must not be
        presented as a model conclusion.
        """
        username = current_username()

        async def run() -> dict[str, Any]:
            resolved = fomc.validate_month(month) or await _resolve_month("ready_report")
            if not resolved:
                return _not_found(None, "no_ready_report")
            wanted = fomc.validate_category(category)
            rows = await _query(*fomc.comparison_sql(resolved))
            if not rows:
                return _not_found(resolved, "no_ready_report")
            row = rows[0]
            items = fomc.comparison_items(row.get("comparison_json"), wanted)
            return {
                **_base(),
                "found": True,
                "month": resolved,
                "report_id": row.get("report_id"),
                "status": row.get("status"),
                "updated_at": str(row["updated_at"]) if row.get("updated_at") else None,
                "category": wanted,
                "item_count": len(items),
                "items": items,
            }

        try:
            result = await run()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"[fomc] get_fomc_comparison 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(
            f"[fomc] get_fomc_comparison | 用户: {username} | "
            f"条目={result.get('item_count', 0)}"
        )
        return json_safe({"ok": True, **result})

    # ── 美联储货币政策观点（section=2）─────────────────────────────────

    @mcp.tool()
    async def list_fomc_policy_images(month: str | None = None) -> dict[str, Any]:
        """Metadata list of the monetary-policy-view images for one month, in display
        order. month omitted means the newest report month. Only metadata plus an
        address is returned - the BLOB is never loaded here; use
        get_fomc_policy_image for the actual image.
        """
        username = current_username()

        async def run() -> dict[str, Any]:
            resolved = fomc.validate_month(month) or await _resolve_month("report")
            if not resolved:
                return _images([])
            rows = await _query(*fomc.images_sql(fomc.SECTION_POLICY, month=resolved))
            out = _images(rows)
            out["month"] = resolved
            out["section"] = fomc.SECTION_POLICY
            out["section_label"] = fomc.SECTION_LABELS[fomc.SECTION_POLICY]
            return out

        try:
            result = await run()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"[fomc] list_fomc_policy_images 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(
            f"[fomc] list_fomc_policy_images | 用户: {username} | "
            f"图片={result.get('count', 0)}"
        )
        return json_safe({"ok": True, **result})

    @mcp.tool()
    async def get_fomc_policy_image(image_id: int) -> dict[str, Any]:
        """One monetary-policy-view image by id, returned as base64 (the column is a
        BLOB, so it cannot be sent as raw bytes). image_id must be a positive integer
        and must belong to section 2. To understand the view in the image, the image
        itself has to be read by an image-capable model - there is no text field with
        the view content, and file names must not be used to infer it.
        """
        username = current_username()
        try:
            resolved_id = fomc.validate_image_id(image_id)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return await _domain_query(
            "fomc",
            "get_fomc_policy_image",
            username,
            lambda: fomc.image_sql(resolved_id, fomc.SECTION_POLICY),
            lambda rows: (
                _not_found(None, "image_not_found")
                if not rows
                else {
                    **_base(),
                    "found": True,
                    "data": fomc.image_payload(rows[0]),
                }
            ),
        )

    # ── 市场反应（section=3）────────────────────────────────────────────

    @mcp.tool()
    async def list_fomc_market_images(month: str | None = None) -> dict[str, Any]:
        """Metadata list of the market-reaction images for one month, in display order.
        month omitted means the newest report month. Section 3 only - section 2 images
        are the monetary-policy views and must not be mixed in. Metadata only; use
        get_fomc_market_image for the actual image.
        """
        username = current_username()

        async def run() -> dict[str, Any]:
            resolved = fomc.validate_month(month) or await _resolve_month("report")
            if not resolved:
                return _images([])
            rows = await _query(*fomc.images_sql(fomc.SECTION_MARKET, month=resolved))
            out = _images(rows)
            out["month"] = resolved
            out["section"] = fomc.SECTION_MARKET
            out["section_label"] = fomc.SECTION_LABELS[fomc.SECTION_MARKET]
            return out

        try:
            result = await run()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"[fomc] list_fomc_market_images 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(
            f"[fomc] list_fomc_market_images | 用户: {username} | "
            f"图片={result.get('count', 0)}"
        )
        return json_safe({"ok": True, **result})

    @mcp.tool()
    async def get_fomc_market_image(image_id: int) -> dict[str, Any]:
        """One market-reaction image by id, returned as base64. image_id must be a
        positive integer and must belong to section 3. Same caveat as the policy
        images: there is no text field, so never infer the content from the file name.
        """
        username = current_username()
        try:
            resolved_id = fomc.validate_image_id(image_id)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return await _domain_query(
            "fomc",
            "get_fomc_market_image",
            username,
            lambda: fomc.image_sql(resolved_id, fomc.SECTION_MARKET),
            lambda rows: (
                _not_found(None, "image_not_found")
                if not rows
                else {
                    **_base(),
                    "found": True,
                    "data": fomc.image_payload(rows[0]),
                }
            ),
        )

    # ── 完整报告（组合）─────────────────────────────────────────────────

    @mcp.tool()
    async def get_fomc_report(month: str | None = None) -> dict[str, Any]:
        """Assembled report for one month: status, the two linked statements, the
        comparison (only when READY) and both image groups. month omitted means the
        latest READY report month. This is a composite tool over one report - when the
        report is PROCESSING or FAILED it still returns the status and image lists and
        states clearly that the comparison is not available.
        """
        username = current_username()

        async def run() -> dict[str, Any]:
            resolved = fomc.validate_month(month) or await _resolve_month("ready_report")
            if not resolved:
                return _not_found(None, "no_ready_report")

            status_rows = await _query(*fomc.report_status_sql(resolved))
            if not status_rows:
                return _not_found(resolved, "no_report")
            status_row = status_rows[0]
            status = status_row.get("status")
            report_id = status_row.get("report_id")

            out: dict[str, Any] = {
                **_base(),
                "found": True,
                "month": resolved,
                "report_id": report_id,
                "status": status,
                "status_note": fomc.STATUS_LABELS.get(str(status)),
                "comparison_ready": str(status) == "READY",
            }

            pair_rows = await _query(*fomc.statement_pair_sql(resolved))
            if pair_rows:
                row = pair_rows[0]
                out["statements"] = {
                    "current": {
                        "statement_id": row.get("current_statement_id"),
                        "month": (
                            str(row["current_month"]) if row.get("current_month") else None
                        ),
                        "file_name": row.get("current_file_name"),
                    },
                    "previous": {
                        "statement_id": row.get("previous_statement_id"),
                        "month": (
                            str(row["previous_month"])
                            if row.get("previous_month")
                            else None
                        ),
                        "file_name": row.get("previous_file_name"),
                    },
                    "linked_missing": (
                        row.get("current_content") is None
                        or row.get("previous_content") is None
                    ),
                }

            comparison: list[dict[str, Any]] | None = None
            if str(status) == "READY":
                comp_rows = await _query(*fomc.comparison_sql(resolved))
                if comp_rows:
                    comparison = fomc.comparison_items(
                        comp_rows[0].get("comparison_json")
                    )
            out["comparison"] = comparison
            out["comparison_note"] = (
                None
                if comparison is not None
                else "该报告尚未生成完成（PROCESSING / FAILED），声明对比不可用；"
                "图片可单独查询"
            )

            if report_id:
                img_rows = await _query(
                    *fomc.images_sql(fomc.SECTION_POLICY, report_id=report_id)
                )
                out["images"] = _split_images(img_rows)

            return out

        try:
            result = await run()
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            logger.warning(f"[fomc] get_fomc_report 失败 | 用户: {username} | {e}")
            return {"ok": False, "error": str(e)}
        logger.info(f"[fomc] get_fomc_report | 用户: {username}")
        return json_safe({"ok": True, **result})

    return mcp
