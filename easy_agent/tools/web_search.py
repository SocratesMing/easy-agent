"""联网搜索工具：支持 Tavily Search API 与内网 WebSearch 接口。

provider 由 config.yaml 的 ``web_search.provider`` 选择（默认 tavily，可选 internal）；
api_url / api_key 与 models 段一致，来自 ``${VAR}`` 占位符（实际取值在
``.env.{AGENT_ENV}``），复用同一套配置加载逻辑。未配置 api_key 时
``create_web_search_tool`` 返回 None，Agent 不注入本工具。

Tavily（默认，https://docs.tavily.com）：

    POST https://api.tavily.com/search
    Headers: Content-Type: application/json
             Authorization: Bearer <tvly-...>
    Body:    {query, topic, search_depth, max_results, time_range?,
              start_date?/end_date?, include_domains?, exclude_domains?}
    Resp:    {results: [{title, url, content, score, published_date}], request_id, ...}

内网 WebSearch（provider=internal，见《联网搜索接口文档》）：

    POST {api_url}
    Headers: Content-Type: application/json, Authorization: Bearer <api-key>
    Body:    {query, requestId, timeRange?, sites?, blockHosts?, userId?}
    Resp:    {success, code, msg, data: {requestId, resultCount, pageItems: [...]}}
"""

from __future__ import annotations

import logging
import re
import uuid
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from ..config import DEFAULT_WEB_SEARCH_API_URLS

logger = logging.getLogger("easy_agent.tools.web_search")

PROVIDER_TAVILY = "tavily"
PROVIDER_INTERNAL = "internal"
SUPPORTED_PROVIDERS = (PROVIDER_TAVILY, PROVIDER_INTERNAL)

# 接口文档限制：query 1~100 字符（超长服务端会截断，这里先行截断避免浪费配额）
MAX_QUERY_CHARS = 100
# 内网接口限制：sites / blockHosts 各最多 5 个域名；Tavily 侧宽松上限
INTERNAL_MAX_DOMAINS = 5
TAVILY_MAX_DOMAINS = 20
# Tavily max_results 上限 20
TAVILY_MAX_RESULTS = 20

TIME_RANGE_VALUES = ("NoLimit", "OneDay", "OneWeek", "OneMonth", "OneYear")
_DATE_RANGE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.\.\d{4}-\d{2}-\d{2}$")

# 统一的时间范围 → Tavily time_range 取值
_TAVILY_TIME_RANGE = {
    "OneDay": "day",
    "OneWeek": "week",
    "OneMonth": "month",
    "OneYear": "year",
}

# Tavily 常见 HTTP 错误的处置建议
_TAVILY_STATUS_HINTS = {
    400: "请求参数不合法，请调整检索词或参数后重试",
    401: "api-key 无效或未授权，请检查 WEB_SEARCH_API_KEY 配置",
    403: "api-key 无权访问该接口，请检查 Tavily 账户权限",
    429: "请求过于频繁（Rate Limit），请稍后重试",
    432: "已超出套餐用量限制，请稍后重试或检查 Tavily 账户配额",
    433: "账户配额或账单异常，请检查 Tavily 账户",
}

# 内网接口错误码 → 给模型/用户的处置建议（文档「错误码说明」）
_INTERNAL_ERROR_HINTS = {
    1001: "请求头缺少认证信息，多为服务端配置问题，请告知用户联系管理员",
    1002: "api-key 无效或未申请，请告知用户检查联网搜索配置",
    1003: "认证请求头格式有误（应形如 Bearer <api-key>），请告知用户检查配置",
    2001: "查询包含敏感信息（如手机号/身份证号/银行卡号），请改写查询词后重试",
    3001: "请求参数缺失（query / requestId），请调整参数后重试",
    9001: "服务端异常（请求体格式有误）",
    9002: "服务端未知异常",
    9003: "触发 QPM 限流（每分钟调用次数超限），请稍后重试",
}

# 联网搜索开启时追加到系统提示词的行为约束
WEB_SEARCH_SYSTEM_PROMPT = """## 联网搜索

- 用户已开启「联网搜索」，你可以调用 `web_search` 工具检索公开网页信息。
- 涉及实时/时效性内容（新闻、政策、行情、价格、版本号、人物动态、近期事件）或你不确定的事实时，先调用 `web_search` 核实再回答。
- 检索结果来自公网，可能不完整或不准确；关键结论请交叉验证，并在回答中给出来源链接。
- 单轮回答的检索次数尽量控制在 1~3 次：先想清楚检索词再调用，结果不足时更换关键词，不要机械重复相同查询。
- 工具报错或结果为空时如实告知用户，不要编造结果。
"""


class WebSearchArgs(BaseModel):
    query: str = Field(
        description=(
            "搜索关键词或问题，1~100 字符，越具体越好。"
            "不要用「最新新闻」这类泛化词，改用具体检索语句，例如「中信银行 2026 年半年报 净利润」"
        )
    )
    time_range: str = Field(
        default="",
        description=(
            "时间范围（可选），取值：NoLimit（不限，默认）/ OneDay（一天内）/ OneWeek（一周内）/ "
            "OneMonth（一月内）/ OneYear（一年内），或 YYYY-MM-DD..YYYY-MM-DD 区间。"
            "用户提到「最近/今天/本周/今年」等时间范围时传入"
        ),
    )
    sites: str = Field(
        default="",
        description="仅检索指定站点域名（可选），多个用 | 分隔，例如 'gov.cn|news.cn'",
    )
    block_hosts: str = Field(
        default="",
        description="屏蔽指定站点域名（可选），多个用 | 分隔，例如 'weixin.com|toutiao.com'",
    )


def resolve_provider(config: Any) -> str:
    """返回规范化的 provider 名；配置缺失或不支持时返回空串。"""
    ws = getattr(config, "web_search", None)
    provider = str(getattr(ws, "provider", "") or "").strip().lower()
    return provider if provider in SUPPORTED_PROVIDERS else ""


def resolve_api_url(config: Any) -> str:
    """返回实际请求地址：优先配置的 api_url，留空时取 provider 默认地址。"""
    provider = resolve_provider(config)
    if not provider:
        return ""
    ws = getattr(config, "web_search", None)
    return str(getattr(ws, "api_url", "") or "").strip() or DEFAULT_WEB_SEARCH_API_URLS[provider]


def is_web_search_available(config: Any) -> bool:
    """联网搜索是否可用：功能开启、api_key 已配置且 provider/地址合法。"""
    ws = getattr(config, "web_search", None)
    if ws is None or not getattr(ws, "enabled", False):
        return False
    if not str(getattr(ws, "api_key", "") or "").strip():
        return False
    return bool(resolve_provider(config) and resolve_api_url(config))


def create_web_search_tool(config: Any, user_id: str = "") -> "WebSearchTool | None":
    """按配置构建联网搜索工具；未配置时返回 None（调用方不注入）。"""
    if not is_web_search_available(config):
        return None
    ws = config.web_search
    return WebSearchTool(
        provider=resolve_provider(config),
        api_url=resolve_api_url(config),
        api_key=str(ws.api_key).strip(),
        search_depth=str(getattr(ws, "search_depth", "basic") or "basic"),
        topic=str(getattr(ws, "topic", "general") or "general"),
        timeout_seconds=float(getattr(ws, "timeout_seconds", 30.0) or 30.0),
        default_time_range=str(getattr(ws, "default_time_range", "NoLimit") or "NoLimit"),
        max_results=max(1, int(getattr(ws, "max_results", 10) or 10)),
        main_text_max_chars=max(0, int(getattr(ws, "main_text_max_chars", 500) or 0)),
        user_id=user_id or "",
    )


def _normalize_domains(value: str, max_domains: int) -> tuple[list[str], str]:
    """规范化 sites / blockHosts：去空白、去协议/路径，校验数量上限。"""
    raw = str(value or "").strip()
    if not raw:
        return [], ""
    domains: list[str] = []
    for item in raw.split("|"):
        # 模型可能误传完整 URL（https://news.cn/xxx），统一还原为域名
        domain = re.sub(r"^[A-Za-z][A-Za-z0-9+.-]*://", "", item.strip())
        domain = domain.split("/", 1)[0].strip()
        if domain:
            domains.append(domain)
    if len(domains) > max_domains:
        return [], f"最多支持 {max_domains} 个域名，当前传入 {len(domains)} 个"
    return domains, ""


def _normalize_time_range(value: str, default: str) -> str:
    """校验 timeRange：非法值返回空串（由调用方兜底/提示）。"""
    candidate = str(value or "").strip()
    if not candidate:
        candidate = default
    if candidate in TIME_RANGE_VALUES or _DATE_RANGE_RE.match(candidate):
        return candidate
    return ""


def _format_published(value: Any) -> str:
    """统一发布时间格式：Tavily 返回 RFC2822（Fri, 18 Sep 2026 07:09:11 GMT）。"""
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return text[:64]
    if parsed is None:
        return text[:64]
    return parsed.strftime("%Y-%m-%d %H:%M %Z").strip()


def _format_items(items: list[dict[str, Any]], max_results: int, main_text_max_chars: int) -> str:
    """把归一化后的结果渲染为模型易读的文本（控制上下文体积）。"""
    lines: list[str] = []
    for idx, item in enumerate(items[:max_results], start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "(无标题)").strip()
        link = str(item.get("link") or "").strip()
        hostname = str(item.get("hostname") or "").strip()
        published = str(item.get("publishedTime") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if not summary and main_text_max_chars:
            main_text = str(item.get("mainText") or "").strip()
            if main_text:
                summary = main_text[:main_text_max_chars]
                if len(main_text) > main_text_max_chars:
                    summary += "…"
        elif summary and main_text_max_chars and len(summary) > main_text_max_chars:
            # Tavily 的 content 可能是整页正文（尤其 PDF），必须截断避免撑爆上下文
            summary = summary[:main_text_max_chars] + "…"

        lines.append(f"[{idx}] {title}")
        if link:
            lines.append(f"    链接: {link}")
        meta = " | ".join(
            part
            for part in (
                f"站点: {hostname}" if hostname else "",
                f"发布时间: {published}" if published else "",
            )
            if part
        )
        if meta:
            lines.append(f"    {meta}")
        if summary:
            lines.append(f"    摘要: {summary}")
    return "\n".join(lines)


def _tavily_result_to_page_item(result: dict[str, Any]) -> dict[str, Any]:
    """把 Tavily results[] 归一化为统一的结果结构。"""
    link = str(result.get("url") or "").strip()
    try:
        hostname = urlparse(link).netloc
    except ValueError:
        hostname = ""
    return {
        "title": str(result.get("title") or "").strip(),
        "link": link,
        "hostname": hostname,
        "publishedTime": _format_published(result.get("published_date")),
        "summary": str(result.get("content") or "").strip(),
    }


def _extract_tavily_error(response: httpx.Response) -> str:
    """从 Tavily 错误响应中提取 detail.error 文本（形如 {"detail": {"error": "..."}}）。"""
    try:
        data = response.json()
    except ValueError:
        return ""
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, dict):
            return str(detail.get("error") or "").strip()[:200]
        if isinstance(detail, str):
            return detail.strip()[:200]
        if data.get("error"):
            return str(data["error"]).strip()[:200]
    return ""


class WebSearchTool(BaseTool):
    """联网搜索工具（异步 HTTP 调用，错误以文本返回给模型，不抛异常）。"""

    name: str = "web_search"
    description: str = (
        "联网搜索：检索公开网页信息，返回标题、链接、站点、发布时间与摘要。"
        "适用于新闻、政策、行情等时效性问题；不适用于企业工商信息、航班、股票基金等专业数据，"
        "这些场景的结果可能不准，需向用户说明。"
    )
    args_schema: type = WebSearchArgs

    provider: str = PROVIDER_TAVILY
    api_url: str = ""
    api_key: str = ""
    search_depth: str = "basic"
    topic: str = "general"
    timeout_seconds: float = 30.0
    default_time_range: str = "NoLimit"
    max_results: int = 10
    main_text_max_chars: int = 500
    user_id: str = ""

    def _run(self, **kwargs) -> str:
        raise NotImplementedError("此工具仅支持异步调用，请使用 _arun")

    async def _arun(
        self,
        query: str,
        time_range: str = "",
        sites: str = "",
        block_hosts: str = "",
    ) -> str:
        query = str(query or "").strip()
        if not query:
            return "错误：query 不能为空，请提供具体的检索语句。"
        if len(query) > MAX_QUERY_CHARS:
            query = query[:MAX_QUERY_CHARS]

        effective_time_range = _normalize_time_range(time_range, self.default_time_range)
        if not effective_time_range:
            return (
                f"错误：time_range 取值不合法（{time_range}）。"
                f"可选：{'/'.join(TIME_RANGE_VALUES)} 或 YYYY-MM-DD..YYYY-MM-DD。"
            )

        if self.provider == PROVIDER_TAVILY:
            return await self._search_tavily(query, effective_time_range, sites, block_hosts)
        return await self._search_internal(query, effective_time_range, sites, block_hosts)

    async def _post(self, payload: dict[str, Any]) -> tuple[httpx.Response | None, str]:
        """发起搜索请求；网络类错误转换为可读文本（返回 (None, 错误文案)）。"""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(self.api_url, json=payload, headers=headers)
            return response, ""
        except httpx.TimeoutException:
            logger.warning("联网搜索超时 | url=%s", self.api_url)
            return None, f"错误：联网搜索超时（超过 {self.timeout_seconds:g} 秒），请稍后重试或改用其它信息源。"
        except httpx.HTTPError as exc:
            logger.warning("联网搜索请求失败 | %s: %s", type(exc).__name__, exc)
            return None, f"错误：联网搜索请求失败（{type(exc).__name__}），请稍后重试。"

    # ── Tavily ────────────────────────────────────────────────────────

    async def _search_tavily(
        self, query: str, time_range: str, sites: str, block_hosts: str
    ) -> str:
        domains, error = _normalize_domains(sites, TAVILY_MAX_DOMAINS)
        if error:
            return f"错误：sites 参数不合法（{error}）。"
        blocked, error = _normalize_domains(block_hosts, TAVILY_MAX_DOMAINS)
        if error:
            return f"错误：blockHosts 参数不合法（{error}）。"

        payload: dict[str, Any] = {
            "query": query,
            "search_depth": self.search_depth or "basic",
            "topic": self.topic or "general",
            "max_results": max(1, min(self.max_results, TAVILY_MAX_RESULTS)),
            "include_answer": False,
        }
        # 时间过滤：Tavily 的 start_date/end_date（区间）与发布时间字段仅在 news 下生效，
        # 因此带时间约束时统一改用 news，保证过滤确实生效且结果带发布时间。
        if time_range != "NoLimit":
            payload["topic"] = "news"
            if ".." in time_range:
                start_date, end_date = time_range.split("..", 1)
                payload["start_date"] = start_date
                payload["end_date"] = end_date
            else:
                payload["time_range"] = _TAVILY_TIME_RANGE[time_range]
        if domains:
            payload["include_domains"] = domains
        if blocked:
            payload["exclude_domains"] = blocked

        response, error = await self._post(payload)
        if response is None:
            return error

        if response.status_code != 200:
            detail = _extract_tavily_error(response)
            hint = _TAVILY_STATUS_HINTS.get(response.status_code, "请稍后重试")
            logger.warning(
                "Tavily 搜索 HTTP 异常 | status=%s | detail=%s", response.status_code, detail
            )
            suffix = f"，{detail}" if detail else ""
            return f"错误：联网搜索失败（HTTP {response.status_code}{suffix}）。（{hint}）"

        try:
            data = response.json()
        except ValueError:
            logger.warning("Tavily 返回非 JSON: %s", response.text[:200])
            return "错误：联网搜索失败，服务返回格式异常（非 JSON），请稍后重试。"
        if not isinstance(data, dict):
            return "错误：联网搜索失败，服务返回格式异常，请稍后重试。"

        raw_results = data.get("results") if isinstance(data.get("results"), list) else []
        items = [
            _tavily_result_to_page_item(item)
            for item in raw_results
            if isinstance(item, dict)
        ]
        request_id = str(data.get("request_id") or "").strip()
        if not items:
            logger.info("Tavily 搜索无结果 | query=%s", query)
            return (
                "联网搜索未找到相关结果"
                + (f"（requestId: {request_id}）" if request_id else "")
                + "。请尝试更换更具体的检索词。"
            )

        formatted = _format_items(items, self.max_results, self.main_text_max_chars)
        shown = min(len(items), self.max_results)
        logger.info(
            "Tavily 搜索完成 | request_id=%s | query=%s | 返回 %s 条（展示 %s 条）",
            request_id, query, len(items), shown,
        )
        return (
            f"联网搜索完成（来源 Tavily"
            + (f"，requestId: {request_id}" if request_id else "")
            + f"，共 {len(items)} 条结果，展示前 {shown} 条）：\n\n{formatted}"
        )

    # ── 内网 WebSearch 接口（provider=internal） ──────────────────────

    async def _search_internal(
        self, query: str, time_range: str, sites: str, block_hosts: str
    ) -> str:
        normalized_sites, sites_error = _normalize_domains(sites, INTERNAL_MAX_DOMAINS)
        if sites_error:
            return f"错误：sites 参数不合法（{sites_error}）。"
        normalized_block, block_error = _normalize_domains(block_hosts, INTERNAL_MAX_DOMAINS)
        if block_error:
            return f"错误：blockHosts 参数不合法（{block_error}）。"

        request_id = str(uuid.uuid1())
        payload: dict[str, Any] = {
            "query": query,
            "requestId": request_id,
            "timeRange": time_range,
        }
        if normalized_sites:
            payload["sites"] = "|".join(normalized_sites)
        if normalized_block:
            payload["blockHosts"] = "|".join(normalized_block)
        if self.user_id:
            payload["userId"] = self.user_id

        response, error = await self._post(payload)
        if response is None:
            return error

        body_snippet = response.text[:200] if response.text else ""
        if response.status_code != 200:
            logger.warning(
                "联网搜索 HTTP 异常 | requestId=%s | status=%s | body=%s",
                request_id, response.status_code, body_snippet,
            )
            return f"错误：联网搜索失败（HTTP {response.status_code}），请稍后重试。"

        try:
            data = response.json()
        except ValueError:
            logger.warning("联网搜索返回非 JSON | requestId=%s | body=%s", request_id, body_snippet)
            return "错误：联网搜索失败，服务返回格式异常（非 JSON），请稍后重试。"
        if not isinstance(data, dict):
            return "错误：联网搜索失败，服务返回格式异常，请稍后重试。"

        if not data.get("success"):
            code = data.get("code")
            msg = str(data.get("msg") or "未知错误")
            try:
                hint = _INTERNAL_ERROR_HINTS.get(int(code), "")
            except (TypeError, ValueError):
                hint = ""
            logger.warning("联网搜索业务失败 | requestId=%s | code=%s | msg=%s", request_id, code, msg)
            return f"错误：联网搜索失败（code={code}，msg={msg}）" + (f"（{hint}）" if hint else "")

        result_data = data.get("data") if isinstance(data.get("data"), dict) else {}
        items = result_data.get("pageItems") if isinstance(result_data.get("pageItems"), list) else []
        resp_request_id = result_data.get("requestId") or request_id
        if not items:
            logger.info("联网搜索无结果 | requestId=%s | query=%s", resp_request_id, query)
            return f"联网搜索未找到相关结果（requestId: {resp_request_id}）。请尝试更换更具体的检索词。"

        formatted = _format_items(items, self.max_results, self.main_text_max_chars)
        shown = min(len(items), self.max_results)
        logger.info(
            "联网搜索完成 | requestId=%s | query=%s | 返回 %s 条（展示 %s 条）",
            resp_request_id, query, len(items), shown,
        )
        return (
            f"联网搜索完成（requestId: {resp_request_id}，共 {len(items)} 条结果，展示前 {shown} 条）：\n\n"
            f"{formatted}"
        )
