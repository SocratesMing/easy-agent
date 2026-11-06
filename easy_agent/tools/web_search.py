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

from easy_agent.bootstrap.config import DEFAULT_WEB_SEARCH_API_URLS

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


class WebSearchArgs(BaseModel):
    query: str = Field(
        description=(
            "搜索关键词或问题，1~100 字符，越具体越好。"
            "不要用「最新新闻」这类泛化词，改用具体检索语句，例如「某上市公司 2026 年半年报 净利润」"
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


# ── 摘要清洗 ──────────────────────────────────────────────────────────
#: 网页抓取文本里，这些标记之后基本是侧栏 / 推荐 / 版权等非正文内容。
_SUMMARY_NOISE_MARKERS = (
    "更多精彩内容",
    "责任编辑",
    "【编辑",
    "相关推荐",
    "相关阅读",
    "推荐阅读",
    "热门推荐",
    "猜你喜欢",
    "延伸阅读",
    "扫一扫",
    "打开APP",
    "下载APP",
    "上一篇",
    "下一篇",
    "版权所有",
    "免责声明",
    "免责条款",
    "举报/反馈",
)
#: 侧栏小标题（如「#### 国内新闻精选：」），其后的推荐列表不是正文。
_SIDEBAR_HEADING_RE = re.compile(
    r"^#{2,6}\s*\S{0,12}?(?:精选|推荐|排行|热点|热门|相关|猜你|延伸|更多)",
    re.M,
)
#: 表格残留行（形如「| |」「| | |」），至少含一个竖线，不误伤正常空行。
_EMPTY_TABLE_ROW_RE = re.compile(r"^(?:\s*\|)+\s*$", re.M)
#: 页面控件 / 元信息段落的特征词（面包屑、分享按钮、字号切换等）。
_FRAGMENT_KEYWORDS = ("分享到", "字体：", "字体:", "打印本页", "关闭窗口", "字号：")
#: 纯时间戳行，可带「来源：xxx」尾巴（2026-11-05 17:01:40  来源：新华网）。
_TIMESTAMP_LINE_RE = re.compile(
    r"^\d{4}\s*[-/年.]?\s*\d{1,2}\s*[-/月.]?\s*\d{1,2}日?"
    r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\s*(?:来源[:：].*)?$"
)
#: 纯来源说明行（来源：新华网 / 文章来源：xxx）。
_SOURCE_LINE_RE = re.compile(r"^(?:来源|文章来源|作者)[:：]\s*\S{1,24}$")
#: 面包屑导航（新华网 > 时政 > 正文）。
_BREADCRUMB_RE = re.compile(r"^[^。！？\n]{0,40}(?:\s*[>»›]\s*[^。！？\n]{1,20}){1,4}$")
#: 摘要为空时给模型的占位说明（避免模型把「没有正文」当成「没有这条新闻」）。
_NO_SUMMARY_PLACEHOLDER = "（该页面未能抽取到正文，仅有标题与链接，不要据此推断内容）"

# ── 列表 / 聚合页 ─────────────────────────────────────────────────────
#: 侧栏「相关新闻 / 热门排行」与频道页会把多条新闻渲染成一行一条的短标题，
#: 行首带项目符号。模型会把这些标题当成一条条独立新闻，再统一挂上当前页面的
#: 链接——「每条新闻的来源都指向同一个地址」就是这么来的，渲染前必须剥掉。
#: 只认真正的项目符号，不认 markdown 的 ``-`` / ``*``，避免误删正文列表。
_HEADLINE_BULLET_RE = re.compile(r"^\s*[•·●○▪◆]\s*\S")
#: 标题一般不超过这个长度；超过说明是正文句子而不是标题。
_HEADLINE_MAX_CHARS = 40
#: 列表页页脚的入口片段（``>> 更多 娱乐八卦 文章``），本身不是正文；
#: 尾巴上的 ``[...]`` 是抽取服务插入的截断标记，一并去掉。
_MORE_LINK_RE = re.compile(r"(?:>>|»|›)\s*更多[^\n]{0,24}?文章\s*(?:\[\.{2,3}\])?\s*")
#: 抽取服务插入的行内截断标记
_TRUNCATION_MARK_RE = re.compile(r"\s*\[\.{2,3}\]\s*")
#: 列表 / 频道 / 关键词聚合页的 URL 特征：路径里没有文章 ID，只有栏目名或关键词页。
_LISTING_PATH_RE = re.compile(
    r"/(?:keywords?|tags?|topics?|channels?|list|categor(?:y|ies)|search|"
    r"entertainments?|index\.(?:html?|shtml?))(?:/|$)",
    re.I,
)
#: 列表页结果的摘要占位：不把一页几十条标题喂给模型，否则它还是会拆成多行、
#: 共用这一个链接。只保留「这是一页多标题」这个事实。
_LISTING_SUMMARY = "（该页为列表页，一页含多条互不相关的新闻标题，正文已省略）"
#: 渲染时给列表页结果加的一句提醒（模型据此判断不能直接当来源）。
_LISTING_NOTE = (
    "    注意: 该页是列表/频道/关键词聚合页，一页内有多条互不相关的标题，"
    "只能当检索线索，不能作为某条新闻的来源链接"
)


def _looks_like_page_fragment(para: str) -> bool:
    """判断一段是页面控件 / 元信息（面包屑、时间、来源、分享按钮）。

    只认明确特征，不按长度泛删——太短的正常摘要（如只有一句短语）必须
    保留，否则会连正文一起丢掉。
    """
    text = para.strip()
    if not text:
        return True
    if len(text) < 60 and text.startswith(_FRAGMENT_KEYWORDS):
        return True
    if _TIMESTAMP_LINE_RE.match(text):
        return True
    if _SOURCE_LINE_RE.match(text):
        return True
    if _BREADCRUMB_RE.match(text):
        return True
    return False


def _looks_like_nav_paragraph(para: str) -> bool:
    """判断一段文本是导航 / 栏目索引，而不是正文。

    导航段特征：一长串用空格分隔的短词、几乎没有句读标点。中文正文段落
    一定有逗号或句号，因此不会误伤；英文段落整体跳过（按 ASCII 占比判断）。
    """
    text = para.strip()
    if len(text) < 30:
        return False
    if sum(1 for ch in text if ch.isascii()) / len(text) > 0.5:
        return False
    if sum(text.count(ch) for ch in "。！？；，、") >= 2:
        return False
    tokens = text.split()
    if len(tokens) < 8:
        return False
    return sum(len(token) for token in tokens) / len(tokens) < 8


def _looks_like_headline(line: str) -> bool:
    """判断一行是不是「侧栏 / 频道页里的新闻标题」（不是正文句子）。"""
    text = _HEADLINE_BULLET_RE.sub("", line).strip()
    if not text or len(text) > _HEADLINE_MAX_CHARS:
        return False
    # 标题常以「！」「？」结尾，但不会有句号；带句号的更可能是正文列表项，不能删
    return "。" not in text


def _strip_headline_lists(text: str) -> str:
    """剥掉「一页多条标题」的列表块（侧栏推荐、频道列表、关键词聚合页）。

    判定：连续 ≥3 行都是短标题（行首带项目符号、长度 ≤40、无句末标点）。
    单条或两条项目符号不动——正文自己用的列表不能被误删。
    """
    lines = str(text or "").splitlines()
    if len(lines) < 3:
        return str(text or "")
    drop = [False] * len(lines)
    run: list[int] = []

    def _flush() -> None:
        if len(run) >= 3:
            for pos in run:
                drop[pos] = True
        run.clear()

    for idx, line in enumerate(lines):
        if _looks_like_headline(line):
            run.append(idx)
        else:
            _flush()
    _flush()
    if not any(drop):
        return str(text or "")
    return "\n".join(line for idx, line in enumerate(lines) if not drop[idx])


def _looks_like_listing_page(link: str, summary: str) -> bool:
    """判断某条结果是不是「列表 / 频道 / 关键词聚合页」。

    这类页面一个 URL 里装了几十条不同新闻的标题，模型照抄时会把这些标题
    拆成多行、共用同一个链接，读者点开自然对不上。渲染时标注出来，并在
    提示词层面禁止拿它当来源。
    """
    try:
        path = urlparse(str(link or "")).path or ""
    except ValueError:
        path = ""
    if path and _LISTING_PATH_RE.search(path):
        return True
    lines = [line.strip() for line in str(summary or "").splitlines() if line.strip()]
    if len(lines) < 8:
        return False
    short = [line for line in lines if len(line) <= 30 and not re.search(r"[。！？]", line)]
    return len(short) / len(lines) >= 0.6


def _clean_summary(text: str, title: str = "") -> str:
    """剥掉网页抓取文本里的导航、侧栏、推荐、版权等非正文内容。

    联网结果的 ``summary`` 是整页抽取文本，常常整段都是导航栏（新浪财经等
    尤其明显），或末尾接一段「国内新闻精选」侧栏列表。这些内容会被模型
    当成新闻事实引用，也会让不同文章的摘要高度相似、在去重时互相误判，
    因此渲染前先清洗一次。传入 ``title`` 时会顺带去掉摘要里重复的标题。
    """
    raw = str(text or "").strip()
    if not raw:
        return ""
    cut = len(raw)
    for marker in _SUMMARY_NOISE_MARKERS:
        pos = raw.find(marker)
        if pos != -1:
            cut = min(cut, pos)
    heading = _SIDEBAR_HEADING_RE.search(raw)
    if heading:
        cut = min(cut, heading.start())
    raw = raw[:cut]

    # 侧栏 / 频道页的「一行一条标题」列表连噪声标记都没有，单独剥一遍；
    # 不剥的话模型会把每条标题当成一条独立新闻，再统一挂上当前页面的链接。
    raw = _strip_headline_lists(raw)
    raw = _MORE_LINK_RE.sub("", raw)
    raw = _TRUNCATION_MARK_RE.sub(" ", raw)

    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", raw)]
    kept = [
        part
        for part in paragraphs
        if part
        and not _looks_like_nav_paragraph(part)
        and not _looks_like_page_fragment(part)
    ]
    title_key = _title_key(title)
    if title_key:
        # 页面顶部和正文开头常把标题重复两三遍，标题已经单独渲染过了
        kept = [
            part
            for part in kept
            if _title_key(re.sub(r"^#{1,6}\s*", "", part)) != title_key
        ]
    cleaned = _EMPTY_TABLE_ROW_RE.sub("", "\n\n".join(kept))
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


# ── 同事件去重 ────────────────────────────────────────────────────────
#: 摘要 3-gram Jaccard 相似度达到该值即视为同一事件的不同转载。
#: 实测：同一篇央行报告的多个转载 0.66；清洗后不相关新闻 <0.1。
_DEDUPE_SIMILARITY = 0.55
#: 清洗后摘要短于该长度时不做相似度判重：信息太少，相同反而可能是噪声
#: （如多条结果都只抓到「摘要内容」或空正文），误合并会丢真实结果。
_DEDUPE_MIN_SUMMARY_CHARS = 80
_TITLE_SITE_SUFFIX_RE = re.compile(r"\s*[-_|｜–—]\s*[^-_|｜–—]{1,14}$")
_TITLE_PUNCT_RE = re.compile(r"[\s！!，,。.：:；;、\"'“”‘’()（）《》【】\[\]]")


def _title_key(title: str) -> str:
    """标题归一化：去掉站名后缀、标点与空白，用于判重。"""
    text = _TITLE_SITE_SUFFIX_RE.sub("", str(title or "").strip())
    return _TITLE_PUNCT_RE.sub("", text)


def _shingles(text: str, size: int = 3) -> set[str]:
    """把文本切成 3-gram 字符片段集合（中文按字切，无需分词）。"""
    normalized = re.sub(r"\s+", "", text)
    if not normalized:
        return set()
    if len(normalized) <= size:
        return {normalized}
    return {normalized[i : i + size] for i in range(len(normalized) - size + 1)}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    inter = len(left & right)
    if not inter:
        return 0.0
    return inter / (len(left) + len(right) - inter)


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一事件的多个转载只保留一条，避免模型把各家细节混着标注。

    同一条新闻常被多家转述，摘要各截一段、内容互有缺失。模型看到几份
    「看起来一样」的来源时，会把 A 家的数字挂到 B 家的链接上，最终用户
    点开链接发现对不上。这里按「URL 相同 / 标题归一化相同 / 摘要相似度
    超过阈值」聚簇，每簇保留信息量（清洗后摘要长度）最大的一条。
    """
    prepared: list[tuple[dict[str, Any], str, set[str]]] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        link = str(item.get("link") or "").strip()
        if link and link in seen_urls:
            continue
        title_key = _title_key(item.get("title"))
        if title_key and title_key in seen_titles:
            continue
        if link:
            seen_urls.add(link)
        if title_key:
            seen_titles.add(title_key)
        summary = _clean_summary(
            item.get("summary") or item.get("mainText") or "", str(item.get("title") or "")
        )
        shingle = (
            _shingles(summary) if len(summary) >= _DEDUPE_MIN_SUMMARY_CHARS else set()
        )
        prepared.append((item, summary, shingle))

    kept: list[dict[str, Any]] = []
    kept_summaries: list[str] = []
    kept_shingles: list[set[str]] = []
    for item, summary, shingle in prepared:
        target = -1
        for idx, existing in enumerate(kept_shingles):
            if _jaccard(shingle, existing) >= _DEDUPE_SIMILARITY:
                target = idx
                break
        if target < 0:
            kept.append(item)
            kept_summaries.append(summary)
            kept_shingles.append(shingle)
        elif len(summary) > len(kept_summaries[target]):
            # 同一事件换用信息更完整的那条，位置仍按首次出现排
            kept[target] = item
            kept_summaries[target] = summary
            kept_shingles[target] = shingle
    return kept


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
        listing = bool(item.get("listing"))
        if listing:
            # 列表页的正文就是几十条别人的标题，喂给模型只会被拆成多行共用这个链接
            summary = _LISTING_SUMMARY
        else:
            # 先清洗（去掉导航/侧栏/版权/重复标题）再截断，让正文开头优先占据预算
            summary = _clean_summary(item.get("summary") or "", title)
            if not summary and main_text_max_chars:
                summary = _clean_summary(item.get("mainText") or "", title)
            if summary and main_text_max_chars and len(summary) > main_text_max_chars:
                # Tavily 的 content 可能是整页正文（尤其 PDF），必须截断避免撑爆上下文
                summary = summary[:main_text_max_chars] + "…"
            elif not summary:
                summary = _NO_SUMMARY_PLACEHOLDER

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
        if listing:
            lines.append(_LISTING_NOTE)
    return "\n".join(lines)


def _tavily_result_to_page_item(result: dict[str, Any]) -> dict[str, Any]:
    """把 Tavily results[] 归一化为统一的结果结构。"""
    link = str(result.get("url") or "").strip()
    try:
        hostname = urlparse(link).netloc
    except ValueError:
        hostname = ""
    content = str(result.get("content") or "").strip()
    title = str(result.get("title") or "").strip()
    return {
        "title": title,
        "link": link,
        "hostname": hostname,
        "publishedTime": _format_published(result.get("published_date")),
        "summary": content,
        # 列表 / 频道 / 关键词聚合页：一页多条新闻，不能当单条新闻的来源。
        # 用清洗后的文本判断——真正的文章页也可能拖一大段侧栏标题，
        # 直接看原文会把它误判成列表页。
        "listing": _looks_like_listing_page(link, _clean_summary(content, title)),
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

        raw_count = len(items)
        items = _dedupe_items(items)
        # 列表页排在最后：它只剩「一页多标题」这个事实，别占掉文章页的展示名额
        items.sort(key=lambda entry: bool(entry.get("listing")))
        formatted = _format_items(items, self.max_results, self.main_text_max_chars)
        shown = min(len(items), self.max_results)
        logger.info(
            "Tavily 搜索完成 | request_id=%s | query=%s | 返回 %s 条（同事件合并后 %s 条，展示 %s 条）",
            request_id, query, raw_count, len(items), shown,
        )
        merged = raw_count - len(items)
        return (
            f"联网搜索完成（来源 Tavily"
            + (f"，requestId: {request_id}" if request_id else "")
            + f"，共 {raw_count} 条结果"
            + (f"，其中 {merged} 条与其它结果是同一事件，已合并" if merged else "")
            + f"，展示前 {shown} 条）：\n\n{formatted}"
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

        raw_count = len(items)
        items = _dedupe_items(items)
        # 列表页排在最后：它只剩「一页多标题」这个事实，别占掉文章页的展示名额
        items.sort(key=lambda entry: bool(entry.get("listing")))
        formatted = _format_items(items, self.max_results, self.main_text_max_chars)
        shown = min(len(items), self.max_results)
        logger.info(
            "联网搜索完成 | requestId=%s | query=%s | 返回 %s 条（同事件合并后 %s 条，展示 %s 条）",
            resp_request_id, query, raw_count, len(items), shown,
        )
        merged = raw_count - len(items)
        return (
            f"联网搜索完成（requestId: {resp_request_id}，共 {raw_count} 条结果"
            + (f"，其中 {merged} 条与其它结果是同一事件，已合并" if merged else "")
            + f"，展示前 {shown} 条）：\n\n{formatted}"
        )
