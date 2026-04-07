"""Model initialization for different LLM providers"""

import httpx
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from .config import Config


def create_model(config: Config):
    """Create LLM model instance based on configuration
    
    Args:
        config: Configuration object containing LLM settings
        
    Returns:
        LangChain model instance (ChatAnthropic, ChatOpenAI, etc.)
        
    Raises:
        ValueError: If provider is not supported
    """
    llm_config = config.llm
    
    if llm_config.provider == "anthropic":
        return _create_anthropic_model(llm_config)
    elif llm_config.provider == "openai":
        return _create_openai_model(llm_config)
    elif llm_config.provider == "minimax":
        return _create_minimax_model(llm_config)
    else:
        raise ValueError(f"Unsupported LLM provider: {llm_config.provider}")


def _create_anthropic_model(llm_config):
    """Create Anthropic Claude model"""
    return ChatAnthropic(
        api_key=llm_config.api_key,
        model=llm_config.model,
        base_url=llm_config.api_base,
        max_retries=llm_config.retry.max_retries if llm_config.retry.enabled else 0,
    )


def _create_openai_model(llm_config):
    """Create OpenAI model"""
    base_url = llm_config.api_base
    if base_url and not base_url.endswith('/v1'):
        base_url = base_url.rstrip('/') + '/v1'

    return ChatOpenAI(
        api_key=llm_config.api_key,
        model=llm_config.model,
        base_url=base_url,
        max_retries=llm_config.retry.max_retries if llm_config.retry.enabled else 0,
    )


def _create_minimax_model(llm_config):
    """Create MiniMax model (OpenAI-compatible API)"""
    api_base = llm_config.api_base or "https://api.minimaxi.com"

    return ChatOpenAI(
        api_key=llm_config.api_key,
        model=llm_config.model,
        base_url=f"{api_base.rstrip('/')}/v1",
        http_async_client=httpx.AsyncClient(timeout=120.0),
        max_retries=llm_config.retry.max_retries if llm_config.retry.enabled else 0,
    )
