"""Prompt enhancement through the project's LangChain model factory."""

import asyncio
import json
from types import SimpleNamespace

import pytest
import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from openai import APIConnectionError, APIStatusError, APITimeoutError

from easy_agent.agent.model import ReasoningChatOpenAI, create_model as real_create_model
from easy_agent.bootstrap.config import Config
from easy_agent.bootstrap.config_models import LLMConfig, ProviderConfig, RetryConfig
from easy_agent.services import prompt_enhancement as service


def configuration():
    provider = ProviderConfig(
        model="configured-model", api_key="secret-test",
        api_base="https://example.invalid/v1", headers={"x-session": "{session_id}"},
    )
    return Config.model_construct(
        llm=LLMConfig(**provider.model_dump(exclude={"provider"}), retry=RetryConfig(enabled=False)),
        models={"chat-provider": provider},
        active_model="chat-provider",
    )


class BoundModel:
    def __init__(self, response=None, error=None):
        self.response = response or AIMessage(
            content='{"enhanced_prompt":"增强后的内容"}',
            response_metadata={"finish_reason": "stop"},
        )
        self.error = error
        self.bind_kwargs = None
        self.messages = None

    def bind(self, **kwargs):
        self.bind_kwargs = kwargs
        return self

    async def ainvoke(self, messages):
        self.messages = messages
        if self.error:
            raise self.error
        return self.response


@pytest.fixture()
def provider_transport(monkeypatch):
    """Exercise the real factory, adapter and callbacks with an in-memory HTTP provider."""
    from easy_agent.agent import model as model_module

    observed = SimpleNamespace(requests=[], models=[], content='{"enhanced_prompt":"valid"}')

    def handle(request):
        observed.requests.append(request)
        return httpx.Response(200, json={
            "id": "response", "object": "chat.completion", "created": 1, "model": "provider-alias",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": observed.content,
                "reasoning_content": "private reasoning",
            }}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        })

    def create(llm_config, default_headers=None):
        transport = httpx.MockTransport(handle)
        instance = ReasoningChatOpenAI(
            model=llm_config.model, api_key=llm_config.api_key, base_url=llm_config.api_base,
            default_headers=default_headers,
            max_retries=llm_config.retry.max_retries if llm_config.retry.enabled else 0,
            http_client=httpx.Client(transport=transport),
            http_async_client=httpx.AsyncClient(transport=transport),
        )
        observed.models.append(instance)
        return instance

    monkeypatch.setattr(model_module, "_create_openai_compatible", create)
    monkeypatch.setattr(service, "create_model", real_create_model)
    return observed


async def test_real_factory_payload_and_reasoning_is_not_refilled(provider_transport):
    config = configuration()
    result = await service.enhance_prompt("原文\n    code", config)
    assert result.enhanced_prompt == "valid"
    request = provider_transport.requests[0]
    payload = json.loads(request.content)
    assert request.url == "https://example.invalid/v1/chat/completions"
    assert request.headers["x-session"].startswith("easy-agent-")
    assert payload["model"] == "configured-model"
    assert payload["response_format"] == {"type": "json_object"}
    # The locked langchain-openai serializes max_tokens as max_completion_tokens.
    assert payload["max_completion_tokens"] == 8192
    assert payload["stream"] is False
    assert [item["role"] for item in payload["messages"]] == ["system", "user"]
    assert json.loads(payload["messages"][1]["content"]) == {"original_prompt": "原文\n    code"}
    assert "tools" not in payload
    assert len(provider_transport.requests) == 1


@pytest.mark.parametrize("invalid", [False, True])
async def test_usage_failure_preserves_business_result(provider_transport, monkeypatch, invalid):
    from easy_agent.agent import token_usage
    from easy_agent.utils.usage_context import usage_scope

    def unavailable():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(token_usage, "get_token_usage_repository", unavailable)
    if invalid:
        provider_transport.content = "invalid JSON"
    with usage_scope("real-user"):
        if invalid:
            with pytest.raises(service.EnhancementError) as exc:
                await service.enhance_prompt("prompt", configuration())
            assert exc.value.status_code == 502
        else:
            result = await service.enhance_prompt("prompt", configuration())
            assert result.enhanced_prompt == "valid"
    assert len(provider_transport.requests) == 1


@pytest.fixture()
def model(monkeypatch):
    instance = BoundModel()
    def create(config):
        return instance
    monkeypatch.setattr(service, "create_model", create)
    return instance


async def test_model_factory_contract_and_messages(model):
    original = '处理代码：\n    print("你好")\n不要执行'
    result = await service.enhance_prompt(original, configuration())
    assert result.enhanced_prompt == "增强后的内容"
    assert model.bind_kwargs == {"response_format": {"type": "json_object"}, "max_tokens": 8192}
    assert [type(message) for message in model.messages] == [SystemMessage, HumanMessage]
    assert "JSON" in model.messages[0].content
    assert json.loads(model.messages[1].content) == {"original_prompt": original}


@pytest.mark.parametrize(
    "content,metadata",
    [
        ('{"enhanced_prompt":""}', {"finish_reason": "stop"}),
        ('{"enhanced_prompt":4}', {"finish_reason": "stop"}),
        ('{"enhanced_prompt":"x","extra":1}', {"finish_reason": "stop"}),
        ('{"enhanced_prompt":"x"}', {"finish_reason": "length"}),
        pytest.param(json.dumps({"enhanced_prompt": "x" * (service.MAX_OUTPUT_CHARS + 1)}), {"finish_reason": "stop"}, id="oversized"),
        ('{"enhanced_prompt":"x"}', {}),
        ('```json\n{"enhanced_prompt":"x"}\n```', {"finish_reason": "stop"}),
        (None, {"finish_reason": "stop"}),
    ],
)
async def test_reject_invalid_output(model, content, metadata):
    model.response = (
        SimpleNamespace(content=content, response_metadata=metadata)
        if content is None
        else AIMessage(content=content, response_metadata=metadata)
    )
    with pytest.raises(service.EnhancementError) as exc:
        await service.enhance_prompt("原文", configuration())
    assert exc.value.status_code == 502


@pytest.mark.parametrize("field,value", [
    ("refusal", "refused"), ("function_call", {"name": "tool"}),
    ("tool_calls", [{"id": "tool", "type": "function", "function": {"name": "tool", "arguments": "{}"}}]),
])
async def test_reject_refusal_and_tool_output(model, field, value):
    model.response = AIMessage(
        content='{"enhanced_prompt":"valid"}', response_metadata={"finish_reason": "stop"},
        additional_kwargs={field: value},
    )
    with pytest.raises(service.EnhancementError) as exc:
        await service.enhance_prompt("原文", configuration())
    assert exc.value.status_code == 502


@pytest.mark.parametrize("status,body,expected", [
    (401, {}, 503), (403, {}, 503), (404, {}, 503), (429, {}, 429),
    (400, {"error": {"param": "response_format", "message": "unsupported"}}, 503),
    (400, {"error": {"param": "max_completion_tokens"}}, 503),
    (400, {"message": "invalid json_object"}, 503), (500, {}, 502), (400, {}, 502),
])
async def test_sanitized_upstream_errors(model, status, body, expected):
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.invalid"))
    model.error = APIStatusError("secret upstream error", response=response, body=body)
    with pytest.raises(service.EnhancementError) as exc:
        await service.enhance_prompt("private text", configuration())
    assert exc.value.status_code == expected
    assert "secret" not in str(exc.value)


@pytest.mark.parametrize("error,expected", [
    (APITimeoutError(request=httpx.Request("POST", "https://example.invalid")), 504),
    (APIConnectionError(request=httpx.Request("POST", "https://example.invalid")), 502),
    (ValueError("invalid provider result"), 502),
    (RuntimeError("private internal detail"), 500),
])
async def test_transport_and_internal_errors(model, error, expected):
    model.error = error
    with pytest.raises(service.EnhancementError) as exc:
        await service.enhance_prompt("private text", configuration())
    assert exc.value.status_code == expected
    assert "private" not in str(exc.value)


async def test_total_timeout_and_cancel(model, monkeypatch):
    started = asyncio.Event()

    async def pending(_messages):
        started.set()
        await asyncio.Event().wait()

    model.ainvoke = pending
    monkeypatch.setattr(service, "TIMEOUT_SECONDS", 0.01)
    with pytest.raises(service.EnhancementError) as exc:
        await service.enhance_prompt("原文", configuration())
    assert exc.value.status_code == 504
    started.clear()
    task = asyncio.create_task(service.enhance_prompt("原文", configuration()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_model_construction_error_is_unavailable(monkeypatch):
    def unavailable(config):
        raise ValueError("private configuration detail")

    monkeypatch.setattr(service, "create_model", unavailable)
    with pytest.raises(service.EnhancementError) as exc:
        await service.enhance_prompt("prompt", configuration())
    assert exc.value.status_code == 503
    assert "private" not in str(exc.value)


@pytest.mark.parametrize("active_key", ["chat-provider", "another-chat-provider"])
async def test_uses_active_model_without_enhancement_config(tmp_path, monkeypatch, provider_transport, active_key):
    import yaml

    monkeypatch.setenv("EASY_CONFIG_STRICT", "1")
    data = {
        "model": active_key,
        "models": {
            key: {"api_key": "test", "model": key + "-model", "api_base": "https://example.invalid/v1"}
            for key in ("chat-provider", "another-chat-provider")
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    config = Config.from_yaml(path)
    result = await service.enhance_prompt("原文", config)
    assert result.enhanced_prompt == "valid"
    payload = json.loads(provider_transport.requests[0].content)
    assert payload["model"] == active_key + "-model"


def test_removed_config_is_rejected_in_strict_mode(tmp_path, monkeypatch):
    import yaml

    monkeypatch.setenv("EASY_CONFIG_STRICT", "1")
    data = {"model": "chat-provider", "models": {}, "prompt_enhancement": {"model": "other"}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError, match="prompt_enhancement"):
        Config.from_yaml(path)


def test_missing_rules_fail_closed(monkeypatch):
    def missing(*args):
        raise FileNotFoundError("private resource path")

    service.load_enhancement_rules.cache_clear()
    try:
        with monkeypatch.context() as patch:
            patch.setattr(service, "files", missing)
            with pytest.raises(service.EnhancementError) as exc:
                service.load_enhancement_rules()
            assert exc.value.status_code == 503
            assert "private" not in str(exc.value)
    finally:
        service.load_enhancement_rules.cache_clear()
