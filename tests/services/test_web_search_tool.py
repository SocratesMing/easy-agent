"""联网搜索工具（easy_agent/tools/web_search.py）单元测试 —— 内网 WebSearch provider。

通过 httpx.MockTransport 拦截请求，验证：
- 请求体/请求头符合《联网搜索接口文档》
- 结果格式化、参数校验与错误码提示
- 未配置 api_key 时不构建工具（Agent 不注入）

Tavily provider 的用例见 test_web_search_tool_tavily.py。
"""

import json
from types import SimpleNamespace

import httpx
import pytest

from easy_agent.tools.web_search import (
    MAX_QUERY_CHARS,
    PROVIDER_INTERNAL,
    PROVIDER_TAVILY,
    WebSearchTool,
    create_web_search_tool,
    is_web_search_available,
    resolve_api_url,
    resolve_provider,
)

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _install_mock_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    def _client_factory(**kwargs):
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory)


def _web_search_config(**overrides):
    values = {
        "enabled": True,
        "provider": PROVIDER_INTERNAL,
        "api_url": "http://search.example/api/v1/webSearch",
        "api_key": "test-key",
        "search_depth": "basic",
        "topic": "general",
        "timeout_seconds": 5,
        "default_time_range": "NoLimit",
        "max_results": 10,
        "main_text_max_chars": 500,
    }
    values.update(overrides)
    return SimpleNamespace(web_search=SimpleNamespace(**values))


def _page_item(**overrides):
    item = {
        "title": "示例标题",
        "link": "https://news.example/a",
        "hostname": "news.example",
        "publishedTime": "2026-09-01T09:00:00Z",
        "mainText": "正文内容",
        "summary": "摘要内容",
    }
    item.update(overrides)
    return item


def _ok_response(items):
    return {
        "success": True,
        "code": 0,
        "msg": "SUCCESS",
        "data": {"requestId": "req-1", "resultCount": len(items), "pageItems": items},
    }


# ── 配置门控 ──────────────────────────────────────────────────────────


def test_create_tool_requires_api_key():
    assert create_web_search_tool(_web_search_config(api_key="")) is None
    assert create_web_search_tool(_web_search_config(api_key="  ")) is None
    assert create_web_search_tool(_web_search_config(enabled=False)) is None
    tool = create_web_search_tool(_web_search_config(), user_id="tester")
    assert isinstance(tool, WebSearchTool)
    assert tool.user_id == "tester"
    assert tool.provider == PROVIDER_INTERNAL


def test_is_web_search_available_without_section():
    assert is_web_search_available(SimpleNamespace()) is False


def test_unsupported_provider_is_unavailable():
    config = _web_search_config(provider="unknown")
    assert resolve_provider(config) == ""
    assert is_web_search_available(config) is False
    assert create_web_search_tool(config) is None


def test_default_api_url_follows_provider():
    """api_url 留空时按 provider 取默认地址。"""
    tavily = _web_search_config(provider=PROVIDER_TAVILY, api_url="")
    internal = _web_search_config(provider=PROVIDER_INTERNAL, api_url="")
    assert resolve_api_url(tavily) == "https://api.tavily.com/search"
    assert resolve_api_url(internal) == "http://28.221.28.7:10089/api/v1/webSearch"


# ── 请求构造 ──────────────────────────────────────────────────────────


async def test_request_matches_interface_doc(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["content_type"] = request.headers.get("Content-Type")
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_ok_response([_page_item()]))

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_web_search_config(), user_id="tester")

    result = await tool._arun(
        query="人工智能发展趋势",
        time_range="OneWeek",
        sites="gov.cn|news.cn",
        block_hosts="weixin.com",
    )

    assert captured["url"] == "http://search.example/api/v1/webSearch"
    assert captured["authorization"] == "Bearer test-key"
    assert captured["content_type"].startswith("application/json")
    payload = captured["json"]
    assert payload["query"] == "人工智能发展趋势"
    assert payload["timeRange"] == "OneWeek"
    assert payload["sites"] == "gov.cn|news.cn"
    assert payload["blockHosts"] == "weixin.com"
    assert payload["userId"] == "tester"
    # requestId 必填，且每次调用唯一
    assert len(payload["requestId"]) >= 1

    assert "共 1 条结果" in result
    assert "示例标题" in result
    assert "https://news.example/a" in result
    assert "摘要: 摘要内容" in result


async def test_default_time_range_and_query_truncation(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_ok_response([]))

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(
        _web_search_config(default_time_range="OneYear"), user_id="tester"
    )

    long_query = "长" * (MAX_QUERY_CHARS + 50)
    await tool._arun(query=long_query)

    payload = captured["json"]
    # 文档限制 query 1~100 字符：超长先截断，避免服务端截断浪费配额
    assert len(payload["query"]) == MAX_QUERY_CHARS
    assert payload["timeRange"] == "OneYear"
    # 未传 sites/blockHosts 时不应出现在请求体中
    assert "sites" not in payload
    assert "blockHosts" not in payload


async def test_invalid_params_rejected_before_http(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("参数非法时不应发起 HTTP 请求")

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_web_search_config(), user_id="tester")

    assert "query 不能为空" in await tool._arun(query="  ")
    assert "最多支持 5 个域名" in await tool._arun(
        query="q", sites="|".join(f"s{i}.com" for i in range(6))
    )
    assert "最多支持 5 个域名" in await tool._arun(
        query="q", block_hosts="|".join(f"s{i}.com" for i in range(6))
    )
    assert "time_range 取值不合法" in await tool._arun(query="q", time_range="本周")


async def test_date_range_time_range_allowed(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_ok_response([]))

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_web_search_config())

    await tool._arun(query="q", time_range="2024-12-30..2025-12-30")

    assert captured["json"]["timeRange"] == "2024-12-30..2025-12-30"


async def test_domains_normalized_from_urls(monkeypatch):
    """模型误传完整 URL 时按域名发送（接口只接受域名）。"""
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_ok_response([]))

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_web_search_config())

    await tool._arun(
        query="q",
        sites="https://news.cn/index | http://gov.cn",
        block_hosts="weixin.com/",
    )

    assert captured["json"]["sites"] == "news.cn|gov.cn"
    assert captured["json"]["blockHosts"] == "weixin.com"


# ── 结果与错误 ────────────────────────────────────────────────────────


async def test_summary_fallback_to_main_text(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_ok_response([
                _page_item(summary="", mainText="正文" * 200),
            ]),
        )

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_web_search_config(main_text_max_chars=10))

    result = await tool._arun(query="q")

    assert "摘要: " + "正文" * 5 + "…" in result


async def test_result_count_limited_by_max_results(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_ok_response([_page_item(title=f"标题{i}") for i in range(5)]),
        )

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_web_search_config(max_results=2))

    result = await tool._arun(query="q")

    assert "共 5 条结果" in result
    assert "展示前 2 条" in result
    assert "标题1" in result
    assert "标题3" not in result


async def test_empty_result_is_not_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_response([]))

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_web_search_config())

    result = await tool._arun(query="q")

    assert not result.startswith("错误：")
    assert "未找到相关结果" in result


async def test_business_error_code_hint(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"success": False, "code": 9003, "msg": "QPM限流", "data": None},
        )

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_web_search_config())

    result = await tool._arun(query="q")

    assert result.startswith("错误：")
    assert "9003" in result
    assert "限流" in result


async def test_http_and_timeout_errors(monkeypatch):
    def handler_500(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _install_mock_transport(monkeypatch, handler_500)
    tool = create_web_search_tool(_web_search_config())
    assert (await tool._arun(query="q")).startswith("错误：")

    def handler_timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    _install_mock_transport(monkeypatch, handler_timeout)
    result = await tool._arun(query="q")
    assert result.startswith("错误：")
    assert "超时" in result
