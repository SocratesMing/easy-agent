"""联网搜索工具（easy_agent/tools/web_search.py）单元测试 —— Tavily provider。

通过 httpx.MockTransport 拦截请求，验证：
- 请求头（Authorization: Bearer）与请求体参数映射（time_range / topic / domains）
- 响应解析与结果格式化（含发布时间格式化）
- HTTP 错误码提示与未配置降级
"""

import json
from types import SimpleNamespace

import httpx

from easy_agent.tools.web_search import (
    PROVIDER_TAVILY,
    create_web_search_tool,
)

_REAL_ASYNC_CLIENT = httpx.AsyncClient


def _install_mock_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    def _client_factory(**kwargs):
        kwargs.pop("transport", None)
        return _REAL_ASYNC_CLIENT(transport=transport, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client_factory)


def _tavily_config(**overrides):
    values = {
        "enabled": True,
        "provider": PROVIDER_TAVILY,
        "api_url": "",
        "api_key": "tvly-test-key",
        "search_depth": "basic",
        "topic": "general",
        "timeout_seconds": 5,
        "default_time_range": "NoLimit",
        "max_results": 5,
        "main_text_max_chars": 500,
    }
    values.update(overrides)
    return SimpleNamespace(web_search=SimpleNamespace(**values))


def _tavily_response(results):
    return {
        "query": "q",
        "answer": None,
        "images": [],
        "results": results,
        "response_time": 0.7,
        "request_id": "req-tavily-1",
    }


def _result(**overrides):
    item = {
        "title": "AI 未来趋势分析",
        "url": "https://techcrunch.com/ai-trends",
        "content": "本文探讨了 AI 在 2026 年的发展方向。",
        "score": 0.95,
        "published_date": "Fri, 18 Sep 2026 07:09:11 GMT",
    }
    item.update(overrides)
    return item


async def test_request_mapping(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_tavily_response([_result()]))

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_tavily_config())

    result = await tool._arun(
        query="人工智能发展趋势",
        time_range="OneWeek",
        sites="https://gov.cn/x | news.cn",
        block_hosts="weixin.com",
    )

    assert captured["url"] == "https://api.tavily.com/search"
    assert captured["authorization"] == "Bearer tvly-test-key"
    payload = captured["json"]
    assert payload["query"] == "人工智能发展趋势"
    assert payload["search_depth"] == "basic"
    assert payload["max_results"] == 5
    assert payload["include_answer"] is False
    # 带时间范围 → 用 news topic 且映射为 Tavily 的 time_range
    assert payload["topic"] == "news"
    assert payload["time_range"] == "week"
    assert payload["include_domains"] == ["gov.cn", "news.cn"]
    assert payload["exclude_domains"] == ["weixin.com"]

    assert "共 1 条结果" in result
    assert "AI 未来趋势分析" in result
    assert "https://techcrunch.com/ai-trends" in result
    assert "techcrunch.com" in result
    assert "2026-09-18 07:09 UTC" in result
    assert "摘要: 本文探讨了 AI 在 2026 年的发展方向。" in result


async def test_no_limit_keeps_general_topic(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_tavily_response([]))

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_tavily_config(topic="finance"))

    await tool._arun(query="q")

    payload = captured["json"]
    assert payload["topic"] == "finance"
    assert "time_range" not in payload
    assert "start_date" not in payload
    assert "include_domains" not in payload


async def test_date_range_maps_to_start_end_date(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_tavily_response([]))

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_tavily_config())

    await tool._arun(query="q", time_range="2026-08-01..2026-08-31")

    payload = captured["json"]
    assert payload["topic"] == "news"
    assert payload["start_date"] == "2026-08-01"
    assert payload["end_date"] == "2026-08-31"
    assert "time_range" not in payload


async def test_max_results_capped_at_tavily_limit(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["json"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json=_tavily_response([]))

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_tavily_config(max_results=50))

    await tool._arun(query="q")

    assert captured["json"]["max_results"] == 20


async def test_too_many_domains_rejected(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("参数非法时不应发起 HTTP 请求")

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_tavily_config())

    result = await tool._arun(query="q", sites="|".join(f"s{i}.com" for i in range(21)))

    assert result.startswith("错误：")
    assert "最多支持 20 个域名" in result


async def test_unauthorized_error_hint(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"detail": {"error": "Unauthorized: missing or invalid API key."}}
        )

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_tavily_config())

    result = await tool._arun(query="q")

    assert result.startswith("错误：")
    assert "401" in result
    assert "WEB_SEARCH_API_KEY" in result
    assert "Unauthorized" in result


async def test_empty_results_is_not_error(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_tavily_response([]))

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_tavily_config())

    result = await tool._arun(query="q")

    assert not result.startswith("错误：")
    assert "未找到相关结果" in result
    assert "req-tavily-1" in result


async def test_published_date_invalid_kept_as_is(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_tavily_response([_result(published_date="2026-09-01")])
        )

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_tavily_config())

    result = await tool._arun(query="q")

    assert "发布时间: 2026-09-01" in result


async def test_long_content_is_truncated(monkeypatch):
    """Tavily content 可能是整页正文，必须按 main_text_max_chars 截断。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_tavily_response([_result(content="正文" * 300)])
        )

    _install_mock_transport(monkeypatch, handler)
    tool = create_web_search_tool(_tavily_config(main_text_max_chars=50))

    result = await tool._arun(query="q")

    assert "摘要: " + "正文" * 25 + "…" in result
    assert "正文" * 26 not in result
