"""Model factory for creating LLM instances.

Model selection is protocol-driven (see ``ProviderConfig.protocol``):
- "openai":     ChatOpenAI subclass (ReasoningChatOpenAI), OpenAI-compatible API.
- "anthropic":  ChatAnthropic, Anthropic-compatible API (e.g. MiniMax / Bedrock / Anthropic).

Use ``create_model(config, model_name)`` to build an instance; config resolution
is handled by the public ``resolve_llm_config``.
"""

import logging

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI

from .config import Config, LLMConfig, RetryConfig

logger = logging.getLogger(__name__)

# Candidate field names used by various providers for model reasoning / thinking.
# DeepSeek uses "reasoning_content"; some models use "reasoning"; others use
# "reason_content". All are treated as aliases and the original key is preserved
# so the value round-trips correctly back to the same provider.
_REASONING_KEYS = ("reasoning_content", "reasoning", "reason_content")

# Anthropic-compatible models: extended-thinking budget and output cap.
# Values identical to the previous hardcoded literals (Phase 1 behavior-preserving).
ANTHROPIC_THINKING_BUDGET_TOKENS = 10000
ANTHROPIC_MAX_TOKENS = 16000


def _extract_reasoning(source: dict):
    """Return ``(key, value)`` for the first reasoning field present in ``source``.

    Returns ``None`` if none of the candidate keys carry a truthy value.
    """
    for key in _REASONING_KEYS:
        val = source.get(key)
        if val:
            return key, val
    return None


def extract_reasoning(additional_kwargs) -> str:
    """Extract reasoning/thinking text from a message's ``additional_kwargs``.

    Handles the various field names providers use (``reasoning_content``,
    ``reasoning``, ``reason_content``). Returns the first truthy value found,
    or an empty string when ``additional_kwargs`` is missing/empty.
    """
    if not isinstance(additional_kwargs, dict):
        return ""
    found = _extract_reasoning(additional_kwargs)
    return found[1] if found else ""


def strip_image_content(messages):
    """把消息列表中的 image_url / image 多模态内容块替换为文本占位。

    供不支持视觉（supports_vision=False）的模型使用，避免发送 image_url
    给 DeepSeek 等纯文本模型导致 400 invalid_request_error。

    幂等：对已过滤的消息重复执行无副作用。
    """
    result = []
    for msg in messages:
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            result.append(msg)
            continue
        new_content = []
        changed = False
        for block in content:
            if isinstance(block, dict) and block.get("type") in ("image_url", "image"):
                changed = True
                # 保留原文件名提示（若有），便于模型理解上下文
                url = ""
                if block.get("type") == "image_url":
                    url = (block.get("image_url") or {}).get("url", "")
                new_content.append(
                    {"type": "text", "text": f"[图片内容已省略{('：' + url[:60]) if url else ''}]"}
                )
            else:
                new_content.append(block)
        if changed:
            # 若过滤后只剩一个 text 块，转回纯字符串内容
            if (
                len(new_content) == 1
                and isinstance(new_content[0], dict)
                and new_content[0].get("type") == "text"
            ):
                msg = msg.model_copy(update={"content": new_content[0]["text"]})
            else:
                msg = msg.model_copy(update={"content": new_content})
        result.append(msg)
    return result


def resolve_llm_config(config: Config, model_name: str | None):
    """Resolve an LLMConfig to use for model creation.

    If ``model_name`` is given and matches a key in ``config.models``, build a
    fresh LLMConfig from that provider entry (preserving the global retry
    config). Otherwise fall back to the active ``config.llm``.
    """
    if not model_name:
        return config.llm

    provider = config.models.get(model_name)
    if provider is None:
        logger.warning(
            f"Model '{model_name}' not found in config.models, "
            f"falling back to active model '{config.active_model}'. "
            f"Available: {list(config.models.keys())}"
        )
        return config.llm

    retry = config.llm.retry if config.llm.retry else RetryConfig()
    return LLMConfig(
        api_key=provider.api_key,
        api_base=provider.api_base or None,
        model=provider.model or "claude-sonnet-4-6",
        provider=provider.provider or model_name,
        max_input_tokens=provider.max_input_tokens or 200000,
        protocol=provider.protocol or "openai",
        supports_vision=provider.supports_vision,
        retry=retry,
    )


def create_model(config: Config, model_name: str | None = None):
    """Create LLM model instance based on protocol.

    The protocol field in config determines which API client to use:
    - "openai": Use ChatOpenAI (OpenAI-compatible API)
    - "anthropic": Use ChatAnthropic (Anthropic-compatible API)

    Args:
        config: Application configuration.
        model_name: Optional model key (from ``config.models``). When provided
            and present, the corresponding provider config is used instead of
            the active model. Useful for per-request model selection.

    Returns:
        LangChain chat model instance.

    Raises:
        ValueError: If protocol is not supported or the selected provider has
            no api_key configured.
    """
    llm_config = resolve_llm_config(config, model_name)
    if not llm_config.api_key:
        raise ValueError(
            f"Model '{model_name or config.active_model}' has no api_key configured. "
            f"Available models: {list(config.models.keys())}"
        )

    protocol = llm_config.protocol.lower()
    logger.info(
        f"Creating LLM instance | model_name={model_name or config.active_model} | "
        f"provider={llm_config.provider} | model={llm_config.model} | "
        f"protocol={protocol}"
    )

    if protocol == "openai":
        return _create_openai_compatible(llm_config)
    elif protocol == "anthropic":
        return _create_anthropic_compatible(llm_config)
    else:
        raise ValueError(
            f"Unsupported protocol: {protocol}. Use 'openai' or 'anthropic'."
        )


class ReasoningChatOpenAI(ChatOpenAI):
    """ChatOpenAI 子类，承载两个独立职责（Phase 1 仅做结构分清，不改行为）：

    职责 A - 图片过滤：当 supports_vision=False 时，在流式/非流式入口
        过滤消息中的 image_url/image 多模态块，避免 DeepSeek 等纯文本模型
        收到图片内容报 400 invalid_request_error。
    职责 B - reasoning 提取：把 OpenAI 兼容接口返回的思考内容
        (reasoning_content / reasoning / reason_content) 写入 additional_kwargs，
        供前端展示。
    """

    supports_vision: bool = False

    # ── 职责 A：图片过滤 ─────────────────────────────────────────────
    def _maybe_strip_images(self, messages, **kwargs):
        if self.supports_vision:
            return messages, kwargs
        return strip_image_content(messages), kwargs

    async def _astream(self, messages, stop=None, **kwargs):
        messages, kwargs = self._maybe_strip_images(messages, **kwargs)
        async for chunk in super()._astream(messages, stop=stop, **kwargs):
            yield chunk

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        messages, kwargs = self._maybe_strip_images(messages, **kwargs)
        return await super()._agenerate(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )

    def _stream(self, messages, stop=None, **kwargs):
        messages, kwargs = self._maybe_strip_images(messages, **kwargs)
        yield from super()._stream(messages, stop=stop, **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        messages, kwargs = self._maybe_strip_images(messages, **kwargs)
        return super()._generate(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )

    # ── 职责 B：reasoning 提取 ────────────────────────────────────────
    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ):
        gen = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        if gen is None:
            return gen
        msg = gen.message
        if not isinstance(msg, AIMessageChunk):
            return gen

        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
        if not choices or choices[0].get("delta") is None:
            return gen
        delta = choices[0]["delta"]

        reasoning = ""
        for key in _REASONING_KEYS:
            val = delta.get(key)
            if isinstance(val, str):
                reasoning += val

        if reasoning:
            existing = msg.additional_kwargs.get("reasoning_content")
            msg.additional_kwargs["reasoning_content"] = (existing or "") + reasoning

        return gen


def _create_openai_compatible(llm_config) -> ChatOpenAI:
    """Create model using OpenAI-compatible API."""
    return ReasoningChatOpenAI(
        model=llm_config.model,
        api_key=llm_config.api_key,
        base_url=llm_config.api_base,
        max_retries=llm_config.retry.max_retries if llm_config.retry.enabled else 0,
        supports_vision=getattr(llm_config, "supports_vision", False),
    )


def _create_anthropic_compatible(llm_config) -> ChatAnthropic:
    """Create model using Anthropic-compatible API.

    Works with any provider that exposes an Anthropic-compatible endpoint,
    such as MiniMax, AWS Bedrock, or direct Anthropic API.

    Configure api_base in config.yaml, e.g.:
    - MiniMax China: https://api.minimaxi.com/anthropic
    - MiniMax Global: https://api.minimax.io/anthropic
    - Anthropic direct: https://api.anthropic.com
    """
    kwargs = {
        "model": llm_config.model,
        "api_key": llm_config.api_key,
        "base_url": llm_config.api_base,
        "max_retries": llm_config.retry.max_retries if llm_config.retry.enabled else 0,
        "thinking": {"type": "enabled", "budget_tokens": ANTHROPIC_THINKING_BUDGET_TOKENS},
        "max_tokens": ANTHROPIC_MAX_TOKENS,
    }
    return ChatAnthropic(**kwargs)
