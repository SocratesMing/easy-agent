"""Model factory for creating LLM instances.

Supports two interface types:
- anthropic: ChatAnthropic (Claude models)
- openai: ChatOpenAI (OpenAI-compatible APIs: DeepSeek, MiniMax, etc.)
"""

import httpx
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from .config import Config


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

    if provider == "anthropic":
        return _create_anthropic(config.llm)
    elif provider in ("openai", "deepseek", "minimax"):
        return _create_openai_compatible(config.llm, provider)
    else:
        raise ValueError(f"Unsupported provider: {provider}")


def _create_anthropic(llm_config) -> ChatAnthropic:
    """Create Anthropic Claude model."""
    return ChatAnthropic(
        api_key=llm_config.api_key,
        model=llm_config.model,
        base_url=llm_config.api_base,
        max_retries=llm_config.retry.max_retries if llm_config.retry.enabled else 0,
    )


def _create_openai_compatible(llm_config, provider: str) -> ChatOpenAI:
    """Create OpenAI-compatible model (DeepSeek, MiniMax, etc.).

    Different providers have different API base URL patterns:
    - openai: uses api_base as-is, appends /v1 if missing
    - deepseek: default https://api.deepseek.com, appends /v1
    - minimax: default https://api.minimaxi.com, appends /v1
    """
    api_base = llm_config.api_base

    if not api_base:
        defaults = {
            "openai": "https://api.openai.com/v1",
            "deepseek": "https://api.deepseek.com/v1",
            "minimax": "https://api.minimaxi.com/v1",
        }
        api_base = defaults.get(provider, "")

    # Ensure /v1 suffix for OpenAI-compatible endpoints
    if api_base and not api_base.rstrip("/").endswith("/v1"):
        api_base = api_base.rstrip("/") + "/v1"

    # MiniMax needs longer timeout
    timeout = 120.0 if provider == "minimax" else 60.0

    return ChatOpenAI(
        api_key=llm_config.api_key,
        model=llm_config.model,
        base_url=api_base,
        http_async_client=httpx.AsyncClient(timeout=timeout),
        max_retries=llm_config.retry.max_retries if llm_config.retry.enabled else 0,
        streaming=True,
    )
