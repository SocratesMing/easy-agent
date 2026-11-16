"""大模型请求 / 响应报文日志：DEBUG 级别，日志级别为 debug 时才记录（EASY_LOG_LEVEL=debug）。

报文在 httpx 层截获（请求钩子 + 响应体边转发边累积），记到 ``easy_agent.llm_wire``；
钩子总是挂上，每次请求按级别判断，非 DEBUG 时不记录也不包装响应体。
"""

from __future__ import annotations

import json
import logging

import httpx

from easy_agent.agent import model as model_mod

WIRE = "easy_agent.llm_wire"


def _request() -> httpx.Request:
    body = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "答复", "reasoning_content": "上一轮思考"},
            {"role": "user", "content": "追问"},
        ],
    }
    return httpx.Request(
        "POST",
        "https://llm.test/v1/chat/completions",
        headers={"Authorization": "Bearer sk-secret-1234567890"},
        content=json.dumps(body, ensure_ascii=False).encode(),
    )


def _response(request: httpx.Request) -> httpx.Response:
    sse = 'data: {"choices":[{"delta":{"reasoning_content":"想","content":"答"}}]}\n\ndata: [DONE]\n\n'
    return httpx.Response(
        200, request=request, headers={"content-type": "text/event-stream"}, content=sse.encode()
    )


def _wire_records(caplog) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == WIRE]


def test_nothing_is_logged_below_debug(caplog):
    caplog.set_level(logging.INFO, logger=WIRE)
    request = _request()
    response = _response(request)
    original = response.iter_bytes

    model_mod._sync_request_hook(request)
    model_mod._sync_response_hook(response)

    assert not model_mod.llm_wire_log_enabled()
    assert _wire_records(caplog) == []
    assert response.iter_bytes == original  # 非 DEBUG 时不包装响应体


def test_request_context_is_logged_at_debug_with_secrets_masked(caplog):
    caplog.set_level(logging.DEBUG, logger=WIRE)

    model_mod._sync_request_hook(_request())

    [record] = _wire_records(caplog)
    assert record.levelno == logging.DEBUG
    message = record.getMessage()
    assert message.startswith("[LLM_REQUEST] POST https://llm.test/v1/chat/completions")
    body = json.loads(message.split(" | body=", 1)[1])
    assert body["messages"][1]["reasoning_content"] == "上一轮思考"
    assert "sk-secret-1234567890" not in message
    assert "Bear***7890" in message


def test_raw_response_is_logged_at_debug_after_the_stream_is_read(caplog):
    caplog.set_level(logging.DEBUG, logger=WIRE)
    request = _request()
    response = _response(request)

    model_mod._sync_response_hook(response)
    streamed = b"".join(response.iter_bytes())

    assert b"[DONE]" in streamed  # 边转发边累积，不影响 SDK 读到的内容
    [record] = _wire_records(caplog)
    assert record.levelno == logging.DEBUG
    message = record.getMessage()
    assert message.startswith("[LLM_RESPONSE] 200 POST https://llm.test/v1/chat/completions")
    assert '"reasoning_content":"想"' in message


def test_create_model_always_installs_hooks(monkeypatch):
    """级别是每次请求时判断的：模型创建时不是 DEBUG 也要挂上钩子。"""
    from langchain_openai import ChatOpenAI

    created = ChatOpenAI(model="m", api_key="k", base_url="http://wire-log.test/v1")
    installed = []
    monkeypatch.setattr(model_mod, "_create_openai_compatible", lambda *a, **k: created)
    monkeypatch.setattr(model_mod, "install_request_logging", installed.append)
    config = model_mod.Config.load()
    provider = next(iter(config.models.values()))
    monkeypatch.setattr(provider, "api_key", "k")
    monkeypatch.setattr(provider, "protocol", "openai")

    model_mod.create_model(config, next(iter(config.models)))

    assert installed == [created]
