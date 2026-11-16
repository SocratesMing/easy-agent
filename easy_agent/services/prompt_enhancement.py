"""无会话副作用的提示词编辑服务；复用项目模型工厂，严格结构化输出。"""

import asyncio
import json
import logging
import time
from functools import lru_cache
from importlib.resources import files

from langchain_core.messages import HumanMessage, SystemMessage
from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError
from pydantic import BaseModel, ConfigDict, StrictStr, ValidationError

from easy_agent.agent.model import create_model
from easy_agent.bootstrap.config import Config

logger = logging.getLogger(__name__)

# 草稿编辑的固定边界；总时限须小于两套前端的 35 秒等待上限。
TIMEOUT_SECONDS = 30
MAX_INPUT_CHARS = 10_000
MAX_OUTPUT_CHARS = 20_000
MAX_OUTPUT_TOKENS = 8192

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


def validate_completion(completion, max_chars: int) -> EnhancedPrompt:
    """校验模型工厂返回的 AIMessage，保留原始 completion 的完整性要求。"""
    try:
        metadata = getattr(completion, "response_metadata", None) or {}
        if metadata.get("finish_reason") != "stop":
            raise ValueError("incomplete/refused")
        if (
            getattr(completion, "tool_calls", None)
            or getattr(completion, "invalid_tool_calls", None)
            or (getattr(completion, "additional_kwargs", None) or {}).get("refusal")
            or (getattr(completion, "additional_kwargs", None) or {}).get("function_call")
        ):
            raise ValueError("incomplete/refused")
        content = getattr(completion, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise ValueError("empty content")
        result = EnhancedPrompt.model_validate(json.loads(content))
        result.enhanced_prompt = result.enhanced_prompt.strip()
        if not result.enhanced_prompt or len(result.enhanced_prompt) > max_chars:
            raise ValueError("invalid size")
        return result
    except (AttributeError, IndexError, TypeError, ValueError, ValidationError):
        raise EnhancementError(502) from None


def _upstream_status(exc: APIStatusError) -> int:
    status_code = exc.status_code
    if status_code in (401, 403, 404):
        return 503
    if status_code == 429:
        return 429
    # 只把明确的协议/参数不兼容归为配置错误；其他 400 是生成失败。
    body = exc.body
    body = body if isinstance(body, dict) else {}
    error = body.get("error", body)
    if isinstance(error, dict):
        text = str(error.get("message", "")).lower()
        param = str(error.get("param", "")).lower()
        if status_code in (400, 405, 415, 422) and (
            param in {"response_format", "max_tokens", "max_completion_tokens", "model"}
            or any(key in text for key in (
                "response_format", "json_object", "max_tokens", "max_completion_tokens", "unsupported parameter",
            ))
        ):
            return 503
    return 502


async def enhance_prompt(prompt: str, config: Config) -> EnhancedPrompt:
    started = time.monotonic()
    outcome = "cancelled"
    try:
        rules = load_enhancement_rules()
        try:
            model = create_model(config)
        except ValueError:
            raise EnhancementError(503) from None
        request_model = model.bind(
            response_format={"type": "json_object"},
            max_tokens=MAX_OUTPUT_TOKENS,
        )
        async with asyncio.timeout(TIMEOUT_SECONDS):
            completion = await request_model.ainvoke([
                SystemMessage(content=rules),
                HumanMessage(
                    content=json.dumps({"original_prompt": prompt}, ensure_ascii=False)
                ),
            ])
            result = validate_completion(completion, MAX_OUTPUT_CHARS)
        outcome = "success"
        return result
    except (TimeoutError, APITimeoutError):
        outcome = "timeout"
        raise EnhancementError(504) from None
    except EnhancementError as exc:
        outcome = f"invalid_{exc.status_code}"
        raise
    except RateLimitError:
        outcome = "rate_limit"
        raise EnhancementError(429) from None
    except APIStatusError as exc:
        status = _upstream_status(exc)
        outcome = f"upstream_{status}"
        raise EnhancementError(status) from None
    except (APIConnectionError, ValueError):
        outcome = "upstream_failure"
        raise EnhancementError(502) from None
    except Exception:
        outcome = "internal_failure"
        raise EnhancementError(500) from None
    finally:
        logger.info("prompt enhancement | model_key=%s outcome=%s elapsed_ms=%d", config.active_model, outcome, (time.monotonic() - started) * 1000)
