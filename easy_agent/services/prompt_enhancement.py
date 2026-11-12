"""无会话副作用的提示词编辑服务；单次 SDK 调用，严格结构化输出。"""

import asyncio
import json
import logging
import time
from functools import lru_cache
from importlib.resources import files

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError

from easy_agent.agent.model import build_request_headers
from easy_agent.agent.token_usage import capture_sdk_usage, write_usage_event
from easy_agent.bootstrap.config import Config
from easy_agent.utils.blocking import run_blocking

logger = logging.getLogger(__name__)

_MESSAGES = {
    500: "提示词增强失败，请稍后重试",
    503: "提示词增强暂不可用，请联系管理员检查模型配置",
    429: "请求过于频繁，请稍后重试",
    504: "提示词增强超时，请重试",
    502: "提示词增强失败，请重试",
}


class EnhancementError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(_MESSAGES[status_code])


class EnhancedPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enhanced_prompt: StrictStr


@lru_cache(maxsize=1)
def load_enhancement_rules() -> str:
    """只读随包发布的固定资源；不接受外部提示词目录覆盖。"""
    try:
        text = files("easy_agent").joinpath("config/prompts/prompt_enhancement.md").read_text(encoding="utf-8").strip()
        if text:
            return text
    except (OSError, UnicodeError):
        pass
    raise EnhancementError(503)


def _configured_model(config: Config):
    model = config.models.get(config.prompt_enhancement.model)
    if model is None or model.protocol != "openai":
        raise EnhancementError(503)
    for value in (model.model, model.api_key, model.api_base):
        if not isinstance(value, str) or not value.strip() or "${" in value:
            raise EnhancementError(503)
    # 这里没有调用 resolve_llm_config，缺失模型不能回退到聊天模型。
    return model


def validate_completion(completion, max_chars: int) -> EnhancedPrompt:
    try:
        choice = completion.choices[0]
        message = choice.message
        if (
            choice.finish_reason != "stop"
            or getattr(message, "refusal", None)
            or getattr(message, "tool_calls", None)
            or getattr(message, "function_call", None)
        ):
            raise ValueError("incomplete/refused")
        if not isinstance(message.content, str) or not message.content.strip():
            raise ValueError("empty content")
        result = EnhancedPrompt.model_validate(json.loads(message.content))
        result.enhanced_prompt = result.enhanced_prompt.strip()
        if not result.enhanced_prompt or len(result.enhanced_prompt) > max_chars:
            raise ValueError("invalid size")
        return result
    except (AttributeError, IndexError, TypeError, ValueError, ValidationError):
        raise EnhancementError(502) from None


def _upstream_status(exc: APIStatusError) -> int:
    if exc.status_code in (401, 403, 404):
        return 503
    if exc.status_code == 429:
        return 429
    # 只把明确的协议/参数不兼容归为配置错误；其他 400 是生成失败。
    body = exc.body if isinstance(exc.body, dict) else {}
    error = body.get("error", body)
    if isinstance(error, dict):
        text = str(error.get("message", "")).lower()
        param = str(error.get("param", "")).lower()
        if exc.status_code in (400, 405, 415, 422) and (
            param in {"response_format", "max_tokens", "model"}
            or any(key in text for key in ("response_format", "json_object", "max_tokens", "unsupported parameter"))
        ):
            return 503
    return 502


async def enhance_prompt(prompt: str, config: Config) -> EnhancedPrompt:
    started = time.monotonic()
    outcome = "cancelled"
    settings = config.prompt_enhancement
    usage_event = None
    try:
        model = _configured_model(config)
        rules = load_enhancement_rules()
        async with asyncio.timeout(settings.timeout_seconds):
            async with AsyncOpenAI(
                api_key=model.api_key,
                base_url=model.api_base,
                default_headers=build_request_headers(model),
                timeout=settings.timeout_seconds,
                max_retries=0,
            ) as client:
                completion = await client.chat.completions.create(
                    model=model.model,
                    messages=[
                        {"role": "system", "content": rules},
                        {"role": "user", "content": json.dumps({"original_prompt": prompt}, ensure_ascii=False)},
                    ],
                    stream=False,
                    response_format={"type": "json_object"},
                    max_tokens=settings.max_output_tokens,
                )
                usage_event = capture_sdk_usage(
                    completion, model_key=settings.model, model_name=model.model,
                )
                result = validate_completion(completion, settings.max_output_chars)
        outcome = "success"
        return result
    except (TimeoutError, APITimeoutError):
        outcome = "timeout"
        raise EnhancementError(504) from None
    except RateLimitError:
        outcome = "rate_limit"
        raise EnhancementError(429) from None
    except APIStatusError as exc:
        status = _upstream_status(exc)
        outcome = f"upstream_{status}"
        raise EnhancementError(status) from None
    except EnhancementError as exc:
        outcome = f"invalid_{exc.status_code}"
        raise
    except APIConnectionError:
        outcome = "upstream_failure"
        raise EnhancementError(502) from None
    except Exception:
        outcome = "internal_failure"
        raise EnhancementError(500) from None
    finally:
        # Parsing/validation failures still consumed tokens. Flush outside the
        # business timeout, so a database wait cannot become an upstream timeout.
        if usage_event is not None:
            try:
                await run_blocking(write_usage_event, usage_event)
            except Exception as exc:
                logger.warning("token usage write failed | error=%s", type(exc).__name__)
        logger.info("prompt enhancement | model_key=%s outcome=%s elapsed_ms=%d", settings.model, outcome, (time.monotonic() - started) * 1000)
