"""Model factory for creating LLM instances.

Supported providers:
- deepseek: ChatOpenAI (OpenAI-compatible API)
- minimax: ChatAnthropic (Anthropic-compatible API)
"""

import logging

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_openai import ChatOpenAI

from .config import Config

logger = logging.getLogger(__name__)

_patches_applied = False


def _apply_reasoning_patches():
    """Monkey-patch langchain_openai to handle DeepSeek's reasoning_content field.

    DeepSeek's API uses ``reasoning_content`` for model thinking, but
    langchain_openai's internal message conversion functions ignore unknown
    ``additional_kwargs`` fields on AIMessage and don't capture
    ``reasoning_content`` from API responses. This causes 400 errors when
    ``reasoning_content`` from previous assistant messages is silently dropped
    on subsequent API calls within the same agent run.

    Patches three module-level functions:
    - ``_convert_delta_to_message_chunk`` -- capture reasoning_content from
      streaming delta responses into ``additional_kwargs``
    - ``_convert_dict_to_message`` -- capture reasoning_content from
      non-streaming responses into ``additional_kwargs``
    - ``_convert_message_to_dict`` -- emit reasoning_content from
      ``additional_kwargs`` back into the request payload
    """
    global _patches_applied
    if _patches_applied:
        return

    import langchain_openai.chat_models.base as lc_oai_base

    # 1) Streaming: capture reasoning_content from delta into additional_kwargs
    _orig_convert_delta = lc_oai_base._convert_delta_to_message_chunk

    def _patched_convert_delta(_dict, default_class):
        result = _orig_convert_delta(_dict, default_class)
        if isinstance(result, AIMessageChunk):
            rc = _dict.get("reasoning_content")
            if rc:
                result.additional_kwargs["reasoning_content"] = rc
        return result

    lc_oai_base._convert_delta_to_message_chunk = _patched_convert_delta

    # 2) Non-streaming: capture reasoning_content from response dict into additional_kwargs
    _orig_convert_dict = lc_oai_base._convert_dict_to_message

    def _patched_convert_dict(_dict):
        result = _orig_convert_dict(_dict)
        if isinstance(result, AIMessage):
            rc = _dict.get("reasoning_content")
            if rc:
                result.additional_kwargs["reasoning_content"] = rc
        return result

    lc_oai_base._convert_dict_to_message = _patched_convert_dict

    # 3) Outbound: emit reasoning_content from additional_kwargs back into request dict
    _orig_convert_msg = lc_oai_base._convert_message_to_dict

    def _patched_convert_msg(message, api="chat/completions"):
        result = _orig_convert_msg(message, api)
        if isinstance(message, (AIMessage, AIMessageChunk)):
            rc = message.additional_kwargs.get("reasoning_content")
            if rc:
                result["reasoning_content"] = rc
        return result

    lc_oai_base._convert_message_to_dict = _patched_convert_msg

    _patches_applied = True
    logger.info("Applied langchain_openai reasoning_content patches for DeepSeek")


def create_model(config: Config):
    """Create LLM model instance based on provider.

    Args:
        config: Application configuration.

    Returns:
        LangChain chat model instance.

    Raises:
        ValueError: If provider is not supported.
    """
    provider = config.llm.provider.lower()

    if provider == "deepseek":
        return _create_deepseek(config.llm)
    elif provider == "minimax":
        return _create_minimax(config.llm)
    else:
        raise ValueError(f"Unsupported provider: {provider}")


def _create_deepseek(llm_config) -> ChatOpenAI:
    """Create DeepSeek model using OpenAI-compatible API.

    Applies monkey-patches to langchain_openai so that DeepSeek's
    ``reasoning_content`` field (used for model thinking) is preserved
    across API calls within the same agent execution.
    """
    _apply_reasoning_patches()
    return ChatOpenAI(
        model=llm_config.model,
        api_key=llm_config.api_key,
        base_url=llm_config.api_base,
        max_retries=llm_config.retry.max_retries if llm_config.retry.enabled else 0,
    )


def _create_minimax(llm_config) -> ChatAnthropic:
    """Create MiniMax model using Anthropic-compatible API.

    MiniMax provides an Anthropic-compatible endpoint.
    Configure api_base in config.yaml, e.g.:
    - China: https://api.minimaxi.com/anthropic
    - Global: https://api.minimax.io/anthropic
    """
    return ChatAnthropic(
        model=llm_config.model,
        api_key=llm_config.api_key,
        base_url=llm_config.api_base,
        max_retries=llm_config.retry.max_retries if llm_config.retry.enabled else 0,
    )