"""Standalone prompt editing: strict model selection, SDK lifetime and structured output."""
import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APIStatusError, APITimeoutError

from easy_agent.bootstrap.config import Config
from easy_agent.bootstrap.config_models import PromptEnhancementConfig, ProviderConfig
from easy_agent.services import prompt_enhancement as service


def configuration(**settings):
    return Config.model_construct(
        models={"deepseek": ProviderConfig(model="configured-model", api_key="secret-test", api_base="https://example.invalid/v1", headers={"x-session": "{session_id}"})},
        prompt_enhancement=PromptEnhancementConfig(**settings),
    )


def completion(content='{"enhanced_prompt":"增强后的内容"}', reason="stop", refusal=None):
    return NS(choices=[NS(finish_reason=reason, message=NS(content=content, refusal=refusal))])


@pytest.fixture()
def sdk(monkeypatch):
    client = NS(chat=NS(completions=NS(create=AsyncMock(return_value=completion()))))
    context = NS(entered=False, closed=False, kwargs=None)

    class SDK:
        def __init__(self, **kwargs):
            context.kwargs = kwargs

        async def __aenter__(self):
            context.entered = True
            return client

        async def __aexit__(self, *args):
            context.closed = True

    monkeypatch.setattr(service, "AsyncOpenAI", SDK)
    return client.chat.completions.create, context


async def test_sdk_contract_and_cleanup(sdk, caplog):
    call, context = sdk
    original = '处理代码：\n    print("你好")\n不要执行'
    with caplog.at_level("INFO", logger=service.__name__):
        result = await service.enhance_prompt(original, configuration(max_output_tokens=1234))
    assert result.enhanced_prompt == "增强后的内容"
    kwargs = call.call_args.kwargs
    assert kwargs["model"] == "configured-model"
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["max_tokens"] == 1234
    assert kwargs["stream"] is False
    assert [m["role"] for m in kwargs["messages"]] == ["system", "user"]
    assert json.loads(kwargs["messages"][1]["content"]) == {"original_prompt": original}
    assert "JSON" in kwargs["messages"][0]["content"]
    assert context.kwargs["max_retries"] == 0
    assert context.kwargs["default_headers"]["x-session"].startswith("easy-agent-")
    assert context.closed
    assert original not in caplog.text and "secret-test" not in caplog.text


@pytest.mark.parametrize("content,reason,refusal", [
    ('{"enhanced_prompt":""}', "stop", None),
    ('{"enhanced_prompt":4}', "stop", None),
    ('{"enhanced_prompt":"x","extra":1}', "stop", None),
    ('{"enhanced_prompt":"x"}', "length", None),
    ('{"enhanced_prompt":"x"}', "stop", "refused"),
    ('```json\n{"enhanced_prompt":"x"}\n```', "stop", None),
    ('{"enhanced_prompt":"123456"}', "stop", None),
    (None, "stop", None),
])
async def test_reject_invalid_output(sdk, content, reason, refusal):
    call, context = sdk
    call.return_value = completion(content, reason, refusal)
    with pytest.raises(service.EnhancementError) as exc:
        await service.enhance_prompt("原文", configuration(max_output_chars=5))
    assert exc.value.status_code == 502
    assert context.closed
    call.assert_awaited_once()


@pytest.mark.parametrize("change", ["missing", "anthropic", "empty_base", "placeholder", "empty_model", "empty_key"])
async def test_never_fallback_to_chat_model(sdk, change):
    call, context = sdk
    config = configuration()
    model = config.models["deepseek"]
    if change == "missing": config.models = {}
    if change == "anthropic": model.protocol = "anthropic"
    if change == "empty_base": model.api_base = ""
    if change == "placeholder": model.api_key = "${MISSING}"
    if change == "empty_model": model.model = " "
    if change == "empty_key": model.api_key = ""
    with pytest.raises(service.EnhancementError) as exc:
        await service.enhance_prompt("原文", config)
    assert exc.value.status_code == 503
    assert not context.entered
    call.assert_not_called()


@pytest.mark.parametrize("status,body,expected", [
    (401, {}, 503), (403, {}, 503), (404, {}, 503), (429, {}, 429),
    (400, {"error": {"param": "response_format", "message": "unsupported"}}, 503),
    (400, {"message": "invalid json_object"}, 503), (500, {}, 502), (400, {}, 502),
])
async def test_sanitized_upstream_errors(sdk, status, body, expected):
    call, context = sdk
    response = httpx.Response(status, request=httpx.Request("POST", "https://example.invalid"))
    call.side_effect = APIStatusError("secret upstream error", response=response, body=body)
    with pytest.raises(service.EnhancementError) as exc:
        await service.enhance_prompt("private text", configuration())
    assert exc.value.status_code == expected
    assert "secret" not in str(exc.value)
    assert context.closed


async def test_total_timeout_and_cancel_close_sdk(sdk):
    call, context = sdk
    started = asyncio.Event()

    async def pending(**kwargs):
        started.set()
        await asyncio.Event().wait()

    call.side_effect = pending
    with pytest.raises(service.EnhancementError) as exc:
        await service.enhance_prompt("原文", configuration(timeout_seconds=0.01))
    assert exc.value.status_code == 504 and context.closed
    context.closed = False
    started.clear()
    task = asyncio.create_task(service.enhance_prompt("原文", configuration()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert context.closed


async def test_sdk_http_timeout(sdk):
    call, context = sdk
    call.side_effect = APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
    with pytest.raises(service.EnhancementError) as exc:
        await service.enhance_prompt("原文", configuration())
    assert exc.value.status_code == 504 and context.closed


def test_yaml_configuration_and_strict_validation(tmp_path, monkeypatch):
    import yaml
    monkeypatch.setenv("EASY_CONFIG_STRICT", "1")
    data = {"model": "deepseek", "models": {"deepseek": {"api_key": "test", "model": "test", "api_base": "https://example.invalid"}},
            "prompt_enhancement": {"model": "other", "timeout_seconds": 11, "max_input_chars": 321, "max_output_chars": 654, "max_output_tokens": 999}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data))
    assert Config.from_yaml(path).prompt_enhancement.model_dump() == data["prompt_enhancement"]
    data["prompt_enhancement"]["typo"] = 1
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        Config.from_yaml(path)


@pytest.mark.parametrize("setting", [{"timeout_seconds": 0}, {"max_input_chars": -1}, {"max_output_tokens": 100000}])
def test_config_bounds(setting):
    with pytest.raises(ValueError):
        PromptEnhancementConfig(**setting)


async def test_network_failure_and_internal_failure_are_sanitized(sdk):
    from openai import APIConnectionError
    call, context = sdk
    for error, expected in [(APIConnectionError(request=httpx.Request("POST", "https://example.invalid")), 502), (RuntimeError("private internal detail"), 500)]:
        call.side_effect = error
        with pytest.raises(service.EnhancementError) as exc:
            await service.enhance_prompt("原文", configuration())
        assert exc.value.status_code == expected
        assert "private" not in str(exc.value) and context.closed


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


@pytest.mark.parametrize("field", ["tool_calls", "function_call"])
def test_tool_output_cannot_be_refilled(field):
    result = completion()
    setattr(result.choices[0].message, field, [{"name": "tool"}])
    with pytest.raises(service.EnhancementError) as exc:
        service.validate_completion(result, 20000)
    assert exc.value.status_code == 502
