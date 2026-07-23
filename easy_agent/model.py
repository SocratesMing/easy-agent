"""Model factory for creating LLM instances.

Supported providers:
- deepseek: ChatOpenAI (OpenAI-compatible API)
- minimax: ChatAnthropic (Anthropic-compatible API)
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


def _resolve_llm_config(config: Config, model_name: str | None):
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
    llm_config = _resolve_llm_config(config, model_name)
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
    """ChatOpenAI 子类：把 OpenAI 兼容接口返回的思考内容
    （reasoning_content / reasoning / reason_content）写入 additional_kwargs，
    供前端展示。

    当前 langchain-openai 不会自动提取这些非标准字段（官方文档亦说明，
    建议用 provider 专属子类处理），因此在此集中解析，而非全局猴子补丁。
    """

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
        "thinking": {"type": "enabled", "budget_tokens": 10000},
        "max_tokens": 16000,
    }
    return ChatAnthropic(**kwargs)
