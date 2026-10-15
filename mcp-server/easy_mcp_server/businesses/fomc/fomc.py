"""美联储议息会议领域层（三张表：声明 / 报告 / 报告图片，只读）。

口径照 ``docs/MCP_docs/美联储议息会议_SQL清单_MCP信息.md``：

- ``meeting_month`` 是 ``YYYY-MM`` 字符串，声明表与报告表各有**月份唯一索引**，
  每月各一条
- 报告状态：``PROCESSING`` 处理中 / ``READY`` 生成完成 / ``FAILED`` 生成失败。
  **"最新已入库报告"可能是处理中或失败**；要读已完成内容必须取最新 ``READY``
- 报告与两期声明通过 ``current_statement_id`` / ``previous_statement_id`` 关联，
  **建表没有外键**，所以要用 ``LEFT JOIN`` 保留报告行，关联缺失要如实说明
- 图片表第二、三部分共用，用 ``section`` 区分：``2`` = 货币政策观点，
  ``3`` = 市场反应；按 ``display_order`` 升序展示。**market 反应必须用 section=3**，
  不能混入 section=2 的图
- ``comparison_json`` 是 ``LONGTEXT``，**工具层解析**；内部 ``source`` 里
  ``llm`` 才是模型结果，``local-demo`` 是本地模拟，不能当正式结论
- 图片列 ``image_data`` 是 ``MEDIUMBLOB``（最大约 16 MB）。**列表查询绝不取
  BLOB**；单张图片才按需读取，并以 base64 返回给客户端

本模块不做训练、不生成报告、不重算对比、不调用大模型，也不提供任何写入 SQL。
"""

from __future__ import annotations

import base64
import json
import os
import re
from typing import Any

from ...env import load_env
from ...querykit import assert_identifier

TABLES: dict[str, str] = {
    "statements": "fmqt_fomc_statements",
    "reports": "fmqt_fomc_reports",
    "images": "fmqt_fomc_report_images",
}

MODEL_NAME = "美联储议息会议"

#: 报告状态
STATUSES: tuple[str, ...] = ("PROCESSING", "READY", "FAILED")
STATUS_LABELS: dict[str, str] = {
    "PROCESSING": "正在生成声明对比；图片可单独查询",
    "READY": "报告生成完成，可读取声明对比",
    "FAILED": "生成失败；状态表没有具体失败原因字段",
}

#: 图片所属模块
SECTION_POLICY = 2
SECTION_MARKET = 3
SECTIONS: tuple[int, ...] = (SECTION_POLICY, SECTION_MARKET)
SECTION_LABELS: dict[int, str] = {
    SECTION_POLICY: "货币政策观点",
    SECTION_MARKET: "市场反应",
}

#: 声明对比的类别顺序（应用约定，DDL 不约束）
COMPARISON_CATEGORIES: tuple[str, ...] = (
    "经济状况-增长",
    "经济状况-就业",
    "经济状况-通胀",
    "经济状况-其他",
    "货币政策-目标",
    "货币政策-利率",
    "货币政策-缩表",
    "政策评估",
    "投票结果",
)

#: 声明对比内部字段
COMPARISON_FIELDS: tuple[str, ...] = (
    "category",
    "previous_html",
    "current_html",
    "source",
)

#: 图片列表返回的元数据字段（**不含 image_data**）
IMAGE_META_FIELDS: tuple[str, ...] = (
    "image_id",
    "report_id",
    "month",
    "section",
    "display_order",
    "file_name",
    "file_hash",
    "content_type",
    "created_at",
)

DEFAULT_REPORT_LIMIT = 24
MAX_REPORT_LIMIT = 120
MAX_COMPARISON_ITEMS = 200

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\u3000]+")


def schema_name() -> str:
    load_env()
    return os.environ.get("FOMC_SCHEMA", "").strip()


def table_ref(key: str) -> str:
    """三张表的带库名引用（``key`` 取 :data:`TABLES` 的键）。"""
    if key not in TABLES:
        raise ValueError(f"未知数据表: {key!r}（可选 {', '.join(TABLES)}）")
    if schema_name():
        return f"`{assert_identifier(schema_name())}`.`{assert_identifier(TABLES[key])}`"
    return f"`{assert_identifier(TABLES[key])}`"


def statements_ref() -> str:
    return table_ref("statements")


def reports_ref() -> str:
    return table_ref("reports")


def images_ref() -> str:
    return table_ref("images")


def default_allowed_tables() -> tuple[str, ...]:
    """结构探索白名单缺省值：本场景三张表。"""
    return tuple(TABLES.values())


# ── 参数校验 ────────────────────────────────────────────────────────────


def validate_month(value: str | None, name: str = "month") -> str | None:
    """校验 ``YYYY-MM``；留空返回 None（由各工具决定取哪个"最新"）。"""
    text = str(value or "").strip()
    if not text:
        return None
    if not _MONTH_RE.fullmatch(text):
        raise ValueError(f"{name} 必须是 YYYY-MM 格式且月份在 01~12（收到 {value!r}）")
    return text


def validate_required_month(value: str | None, name: str = "month") -> str:
    resolved = validate_month(value, name)
    if resolved is None:
        raise ValueError(f"{name} 不能为空")
    return resolved


def validate_status(value: str | None) -> str | None:
    """报告状态：只接受三个枚举值。"""
    text = str(value or "").strip().upper()
    if not text:
        return None
    if text not in STATUSES:
        raise ValueError(f"status 只支持 {', '.join(STATUSES)}（收到 {value!r}）")
    return text


def validate_section(value: int) -> int:
    """图片模块：只接受 2（货币政策观点）/ 3（市场反应）。"""
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"section 必须是整数（收到 {value!r}）") from None
    if resolved not in SECTIONS:
        raise ValueError(f"section 只支持 {', '.join(str(s) for s in SECTIONS)}")
    return resolved


def _validate_positive_id(value: Any, name: str) -> int:
    try:
        resolved = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} 必须是正整数（收到 {value!r}）") from None
    if resolved <= 0:
        raise ValueError(f"{name} 必须是正整数（收到 {value!r}）")
    return resolved


def validate_image_id(value: Any) -> int:
    return _validate_positive_id(value, "image_id")


def validate_report_id(value: Any) -> int:
    return _validate_positive_id(value, "report_id")


def validate_category(value: str | None) -> str | None:
    """比较类别：留空表示不过滤；给了就必须是已知类别。"""
    text = str(value or "").strip()
    if not text:
        return None
    if text not in COMPARISON_CATEGORIES:
        raise ValueError(
            f"category 只支持：{', '.join(COMPARISON_CATEGORIES)}（收到 {value!r}）"
        )
    return text


def bounded_limit(
    value: int | None,
    default: int = DEFAULT_REPORT_LIMIT,
    maximum: int = MAX_REPORT_LIMIT,
) -> int:
    requested = int(value) if value is not None else default
    return max(1, min(requested, maximum))


# ── 字段解析 ────────────────────────────────────────────────────────────


def decode_json(value: Any) -> Any:
    """``comparison_json`` 是 LONGTEXT：解码成对象；解不出来返回 None。"""
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


def html_to_text(value: Any) -> str | None:
    """把比较项的 HTML 提成纯文本（去掉标签，保留文字内容）。

    只做去标签与空白归一，不做富文本渲染——工具层不保证还原版式。
    """
    if value is None:
        return None
    text = str(value)
    if not text:
        return None
    text = _TAG_RE.sub("", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    lines = [_WS_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip() or None


def comparison_items(value: Any, category: str | None = None) -> list[dict[str, Any]]:
    """把 ``comparison_json`` 转成比较项列表（可按类别过滤）。

    非数组时返回空列表；每项缺失的字段保持 null。
    """
    raw = decode_json(value)
    items: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        # 兼容 {"items": [...]} 形态
        inner = raw.get("items")
        raw = inner if isinstance(inner, list) else [raw]
    if not isinstance(raw, list):
        return []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        item = {field: entry.get(field) for field in COMPARISON_FIELDS}
        item["category"] = str(item["category"]) if item["category"] is not None else None
        item["previous_text"] = html_to_text(item.get("previous_html"))
        item["current_text"] = html_to_text(item.get("current_html"))
        if category and item.get("category") != category:
            continue
        items.append(item)
    # 按已知类别顺序稳定排序，未知类别排在后面（保持相对顺序）
    order = {name: idx for idx, name in enumerate(COMPARISON_CATEGORIES)}
    items.sort(key=lambda it: order.get(str(it.get("category")), len(order)))
    return items[:MAX_COMPARISON_ITEMS]


def image_meta(row: dict[str, Any]) -> dict[str, Any]:
    """图片元数据：**不含 BLOB**，附访问地址与可选的 base64 长度说明。"""
    out: dict[str, Any] = {}
    for field in IMAGE_META_FIELDS:
        value = row.get(field)
        if field in ("image_id", "report_id", "section", "display_order"):
            out[field] = int(value) if value is not None else None
        elif field == "created_at":
            out[field] = str(value) if value is not None else None
        elif field == "month":
            out[field] = str(value) if value is not None else None
        else:
            out[field] = value
    section = out.get("section")
    if section in SECTION_LABELS:
        out["section_label"] = SECTION_LABELS[section]
    image_id = out.get("image_id")
    if image_id:
        out["path"] = f"{image_path_prefix()}/{image_id}"
        if image_base_url():
            out["url"] = f"{image_base_url()}{image_path_prefix()}/{image_id}"
    return out


def image_path_prefix() -> str:
    load_env()
    return os.environ.get("FOMC_IMAGE_PATH_PREFIX", "/api/images").strip() or "/api/images"


def image_base_url() -> str:
    """图片对外访问的根地址；留空表示只给相对路径。

    图片是 ``MEDIUMBLOB``，列表工具不返回二进制，只给可拼地址；
    单张工具才按需读 BLOB 并以 base64 返回。
    """
    load_env()
    return os.environ.get("FOMC_IMAGE_BASE_URL", "").strip().rstrip("/")


def image_payload(row: dict[str, Any]) -> dict[str, Any]:
    """单张图片：读 BLOB 并转 base64（MCP 返回 JSON，不能直接给二进制）。"""
    data = row.get("image_data")
    if isinstance(data, memoryview):
        data = data.tobytes()
    if isinstance(data, bytearray):
        data = bytes(data)
    encoded: str | None = None
    size: int | None = None
    if isinstance(data, bytes):
        encoded = base64.b64encode(data).decode("ascii")
        size = len(data)
    payload = image_meta(row)
    payload["image_base64"] = encoded
    payload["byte_size"] = size
    payload["note"] = (
        "image_data 是二进制，这里以 base64 返回；需要理解图中观点请交给支持图像"
        "理解的能力读取，不能从文件名推断正文"
    )
    return payload


def schema_help() -> dict[str, Any]:
    """数据字典：表关系、状态口径、陷阱。"""
    return {
        "model_name": MODEL_NAME,
        "tables": dict(TABLES),
        "schema": schema_name() or None,
        "structure": (
            "声明表 fmqt_fomc_statements（每月一条原文）+ 报告表 fmqt_fomc_reports"
            "（每月一条，含 comparison_json 与状态）+ 图片表 fmqt_fomc_report_images"
            "（section=2 货币政策观点 / section=3 市场反应，按 report_id 关联）"
        ),
        "month_format": "YYYY-MM（声明表与报告表各有月份唯一索引，每月各一条）",
        "statuses": dict(STATUS_LABELS),
        "latest_rule": (
            "「最新已入库报告」可能是 PROCESSING 或 FAILED；读已完成内容必须取最新"
            " READY 报告（latest_ready）"
        ),
        "sections": {str(k): v for k, v in SECTION_LABELS.items()},
        "comparison_categories": list(COMPARISON_CATEGORIES),
        "comparison_note": (
            "comparison_json 是 LONGTEXT，工具层解析；source 为 llm 才是模型结果，"
            "local-demo 是本地模拟结果，不能当正式结论"
        ),
        "image_note": (
            "image_data 是 MEDIUMBLOB（单张最大约 16MB）：列表工具只返回元数据与"
            "访问地址，单张工具才读 BLOB 并转 base64"
        ),
        "not_available": (
            "状态表没有失败原因字段；图片内容没有配套文字字段（观点只能靠图像理解）；"
            "报告不存在 / 处理中 / 生成失败三者必须分开说明，不能统一答成「政策没有变化」"
        ),
        "pitfalls": [
            "「最新报告」不等于「最新已完成报告」：PROCESSING/FAILED 时声明对比不可读",
            "报告表与声明表之间没有外键，关联缺失要如实说明，不能当作正常对比",
            "市场反应必须用 section=3，混入 section=2 会把货币政策观点图当市场反应",
            "comparison_json 里 source=local-demo 是模拟结果，不得描述成模型正式输出",
            "previous_statement_id 指向的上一期不等于上一自然月",
        ],
    }


# ── SQL 构造 ────────────────────────────────────────────────────────────


def latest_statement_month_sql() -> tuple[str, tuple[Any, ...]]:
    """1.1：最新已入库声明月份。"""
    return (f"SELECT MAX(`meeting_month`) AS month FROM {statements_ref()}", ())


def latest_report_month_sql(ready_only: bool = False) -> tuple[str, tuple[Any, ...]]:
    """1.2：最新已入库报告月份；``ready_only=True`` 时只算 READY。"""
    where = " WHERE `status` = 'READY'" if ready_only else ""
    return (f"SELECT MAX(`meeting_month`) AS month FROM {reports_ref()}{where}", ())


def reports_sql(limit: int | None = None, status: str | None = None) -> tuple[str, tuple[Any, ...]]:
    """1.3：最近 N 期报告列表（倒序，只读元数据不读 JSON）。"""
    size = bounded_limit(limit)
    resolved_status = validate_status(status)
    where = " WHERE `status` = %s" if resolved_status else ""
    params: tuple[Any, ...] = (resolved_status,) if resolved_status else ()
    return (
        f"SELECT `id` AS report_id, `meeting_month` AS month, `status`, "
        f"`current_statement_id`, `previous_statement_id`, `created_at`, `updated_at` "
        f"FROM {reports_ref()}{where} ORDER BY `meeting_month` DESC LIMIT {size}",
        params,
    )


def statement_sql(month: str) -> tuple[str, tuple[Any, ...]]:
    """1.4：某月份声明原文（不含正文以外的隐含口径）。"""
    resolved = validate_required_month(month)
    return (
        f"SELECT `id` AS statement_id, `meeting_month` AS month, `file_name`, "
        f"`file_hash`, `content`, `created_at`, `updated_at` "
        f"FROM {statements_ref()} WHERE `meeting_month` = %s",
        (resolved,),
    )


def previous_statement_sql(month: str) -> tuple[str, tuple[Any, ...]]:
    """1.5：指定月份之前最近一份已入库声明（不等同上一自然月）。"""
    resolved = validate_required_month(month)
    return (
        f"SELECT `id` AS statement_id, `meeting_month` AS month, `file_name`, "
        f"`file_hash`, `content` FROM {statements_ref()} "
        f"WHERE `meeting_month` < %s ORDER BY `meeting_month` DESC LIMIT 1",
        (resolved,),
    )


def statement_pair_sql(month: str) -> tuple[str, tuple[Any, ...]]:
    """1.6：某报告实际关联的两期声明（报告行用 LEFT JOIN 保留）。"""
    resolved = validate_required_month(month)
    return (
        f"SELECT r.`id` AS report_id, r.`meeting_month` AS month, r.`status`, "
        f"r.`current_statement_id`, c.`meeting_month` AS current_month, "
        f"c.`file_name` AS current_file_name, c.`content` AS current_content, "
        f"r.`previous_statement_id`, p.`meeting_month` AS previous_month, "
        f"p.`file_name` AS previous_file_name, p.`content` AS previous_content "
        f"FROM {reports_ref()} AS r "
        f"LEFT JOIN {statements_ref()} AS c ON c.`id` = r.`current_statement_id` "
        f"LEFT JOIN {statements_ref()} AS p ON p.`id` = r.`previous_statement_id` "
        f"WHERE r.`meeting_month` = %s",
        (resolved,),
    )


def comparison_sql(month: str) -> tuple[str, tuple[Any, ...]]:
    """1.7：某月份**已完成**的声明对比（只取 READY）。"""
    resolved = validate_required_month(month)
    return (
        f"SELECT `id` AS report_id, `meeting_month` AS month, `status`, "
        f"`comparison_json`, `updated_at` FROM {reports_ref()} "
        f"WHERE `meeting_month` = %s AND `status` = 'READY'",
        (resolved,),
    )


def report_status_sql(month: str) -> tuple[str, tuple[Any, ...]]:
    """4.1：某月份报告生成状态。"""
    resolved = validate_required_month(month)
    return (
        f"SELECT `id` AS report_id, `meeting_month` AS month, `status`, `updated_at` "
        f"FROM {reports_ref()} WHERE `meeting_month` = %s",
        (resolved,),
    )


def latest_ready_report_sql() -> tuple[str, tuple[Any, ...]]:
    """4.2：最新已完成报告及模型结果。"""
    return (
        f"SELECT `id` AS report_id, `meeting_month` AS month, "
        f"`current_statement_id`, `previous_statement_id`, `comparison_json`, "
        f"`status`, `updated_at` FROM {reports_ref()} WHERE `status` = 'READY' "
        f"ORDER BY `meeting_month` DESC LIMIT 1",
        (),
    )


def images_sql(section: int, month: str | None = None, report_id: int | None = None) -> tuple[str, tuple[Any, ...]]:
    """2.1 / 3.1 / 3.3：图片**元数据列表**（绝不取 BLOB）。"""
    resolved_section = validate_section(section)
    columns = (
        "i.`id` AS image_id, i.`report_id`, r.`meeting_month` AS month, i.`section`, "
        "i.`display_order`, i.`file_name`, i.`file_hash`, i.`content_type`, "
        "i.`created_at`"
    )
    if report_id is not None:
        resolved_report = validate_report_id(report_id)
        return (
            f"SELECT {columns} FROM {reports_ref()} AS r "
            f"JOIN {images_ref()} AS i ON i.`report_id` = r.`id` "
            f"WHERE i.`report_id` = %s AND i.`section` IN (2, 3) "
            f"ORDER BY i.`section` ASC, i.`display_order` ASC, i.`id` ASC",
            (resolved_report,),
        )
    resolved_month = validate_required_month(month)
    return (
        f"SELECT {columns} FROM {reports_ref()} AS r "
        f"JOIN {images_ref()} AS i ON i.`report_id` = r.`id` "
        f"WHERE r.`meeting_month` = %s AND i.`section` = %s "
        f"ORDER BY i.`display_order` ASC, i.`id` ASC",
        (resolved_month, resolved_section),
    )


def image_sql(image_id: int, section: int) -> tuple[str, tuple[Any, ...]]:
    """2.2 / 3.2：单张图片（含 BLOB，仅此处读取）。"""
    resolved_id = validate_image_id(image_id)
    resolved_section = validate_section(section)
    return (
        f"SELECT `id` AS image_id, `report_id`, `section`, `display_order`, "
        f"`file_name`, `file_hash`, `content_type`, `image_data` "
        f"FROM {images_ref()} WHERE `id` = %s AND `section` = %s",
        (resolved_id, resolved_section),
    )
