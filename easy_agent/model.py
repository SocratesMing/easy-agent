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
# "reason_content". All are treated as aliases.
_REASONING_KEYS = ("reasoning_content", "reasoning", "reason_content")

# Anthropic-compatible models: extended-thinking budget and output cap.
ANTHROPIC_THINKING_BUDGET_TOKENS = 10000
ANTHROPIC_MAX_TOKENS = 16000


def extract_reasoning(additional_kwargs) -> str:
    """取消息 ``additional_kwargs`` 里的思考文本（兼容各 provider 字段名）。

    依次尝试 ``reasoning_content`` / ``reasoning`` / ``reason_content``，
    返回首个真值；字段缺失/为空时返回空串。
    """
    if not isinstance(additional_kwargs, dict):
        return ""
    for key in _REASONING_KEYS:
        val = additional_kwargs.get(key)
        if val:
            return val
    return ""


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
        context_length=provider.context_length or 1_000_000,
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
    if protocol == "anthropic":
        return _create_anthropic_compatible(llm_config)
    raise ValueError(
        f"Unsupported protocol: {protocol}. Use 'openai' or 'anthropic'."
    )


class ReasoningChatOpenAI(ChatOpenAI):
    """ChatOpenAI 子类：暴露 OpenAI 兼容接口返回的思考内容。

    基类不提取非标准字段 ``reasoning_content`` 等（见 langchain_openai
    ``BaseChatOpenAI`` 文档）。这里把思考同时暴露为两处：
    - ``additional_kwargs["reasoning_content"]`` —— 旧消费者用 ``extract_reasoning`` 读取；
    - ``content`` 里的 ``reasoning`` 内容块 —— v3 事件流只从内容块产出
      ``reasoning-delta``，前端据此流式显示思考过程。
    """

    def _convert_chunk_to_generation_chunk(
        self, chunk, default_chunk_class, base_generation_info
    ):
        gen = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info
        )
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices", [])
        delta = choices[0].get("delta") if choices else None
        msg = getattr(gen, "message", None)
        if delta is None or not isinstance(msg, AIMessageChunk):
            return gen

        reasoning = ""
        for key in _REASONING_KEYS:
            val = delta.get(key)
            if isinstance(val, str):
                reasoning += val
        if not reasoning:
            return gen

        msg.additional_kwargs["reasoning_content"] = (
            msg.additional_kwargs.get("reasoning_content") or ""
        ) + reasoning
        content = msg.content
        blocks = (
            list(content)
            if isinstance(content, list)
            else ([{"type": "text", "text": content}] if content else [])
        )
        blocks.append({"type": "reasoning", "reasoning": reasoning})
        msg.content = blocks
        return gen


def _create_openai_compatible(llm_config) -> ChatOpenAI:
    """Create model using OpenAI-compatible API."""
    return ReasoningChatOpenAI(
        model=llm_config.model,
        api_key=llm_config.api_key,
        base_url=llm_config.api_base,
        max_retries=llm_config.retry.max_retries if llm_config.retry.enabled else 0,
        # 必须显式开启：langchain 只在「未传 base_url 等自定义客户端参数」时才默认
        # 打开 stream_usage（见 langchain_openai/chat_models/base.py 中
        # "Enable stream_usage by default if using default base URL and client" 分支）。
        # 本项目一律走自定义 base_url（DeepSeek / 火山方舟等兼容接口），不显式开启的
        # 话请求不会带 stream_options={"include_usage": true}，这些服务便不在流末尾
        # 返回 usage_metadata，前端「输入/输出/思考 Token」与「本轮上下文占用」会恒为 0。
        stream_usage=True,
    )


def _create_anthropic_compatible(llm_config) -> ChatAnthropic:
    """Create model using Anthropic-compatible API (MiniMax / Bedrock / Anthropic).

    Configure api_base in config.yaml, e.g. ``https://api.minimaxi.com/anthropic``.
    """
    return ChatAnthropic(
        model=llm_config.model,
        api_key=llm_config.api_key,
        base_url=llm_config.api_base,
        max_retries=llm_config.retry.max_retries if llm_config.retry.enabled else 0,
        thinking={"type": "enabled", "budget_tokens": ANTHROPIC_THINKING_BUDGET_TOKENS},
        max_tokens=ANTHROPIC_MAX_TOKENS,
    )
