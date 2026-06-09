"""Model factory for creating LLM instances.

Supported providers:
- deepseek: ChatOpenAI (OpenAI-compatible API)
- minimax: ChatAnthropic (Anthropic-compatible API)
"""

import logging

import langchain_openai.chat_models.base as lc_oai_base
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import chat_model_stream as cms
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_openai import ChatOpenAI

from .config import Config

logger = logging.getLogger(__name__)

_patches_applied = False


def _parse_mcp_content(content) -> str:
    """Parse MCP-style ToolMessage content and extract plain text.

    MCP tools return content as a list of blocks like:
        [{"type": "text", "text": "..."}]
    or Python repr:
        [{'type': 'text', 'text': '...'}]
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif hasattr(item, "text"):
                parts.append(str(item.text))
        if parts:
            return "\n\n".join(parts)
    return str(content)


def _apply_reasoning_patches():
    """Monkey-patch langchain_openai to handle DeepSeek's reasoning_content field.

    DeepSeek's API uses ``reasoning_content`` for model thinking, but
    langchain_openai's internal message conversion functions ignore unknown
    ``additional_kwargs`` fields on AIMessage and don't capture
    ``reasoning_content`` from API responses. This causes 400 errors when
    ``reasoning_content`` from previous assistant messages is silently dropped
    on subsequent API calls within the same agent run.

    Uses THREE layers of patching for robustness:

    **Layer 1 (message conversion functions)**
    - ``_convert_delta_to_message_chunk`` -- capture reasoning_content from
      streaming delta responses into ``additional_kwargs``
    - ``_convert_dict_to_message`` -- capture reasoning_content from
      non-streaming responses into ``additional_kwargs``
    - ``_convert_message_to_dict`` -- emit reasoning_content from
      ``additional_kwargs`` back into the request payload

    **Layer 2 (request payload)**
    - ``ChatOpenAI._get_request_payload`` -- forcefully injects
      ``reasoning_content`` into the request payload for every assistant
      message that has it in ``additional_kwargs``, as a safety net.

    **Layer 3 (stream assembly)**
    - ``BaseChatOpenAI._astream`` -- copy ``reasoning_content`` from
      ``additional_kwargs`` to ``generation_info`` so it flows through
      the v2 streaming pipeline
    - ``_ChatModelStreamBase._assemble_message`` -- extract
      ``reasoning_content`` from ``response_metadata`` and put it into
      ``additional_kwargs`` of the final ``AIMessage``
    """
    global _patches_applied
    if _patches_applied:
        return

    # ── Layer 1: message conversion functions ──────────────────────────────

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
        is_ai = isinstance(message, (AIMessage, AIMessageChunk))

        result = _orig_convert_msg(message, api)
        # Attach reasoning_content back to the dict for downstream use
        if is_ai:
            rc = message.additional_kwargs.get("reasoning_content")
            if rc:
                result["reasoning_content"] = rc
        return result

    lc_oai_base._convert_message_to_dict = _patched_convert_msg

    # ── Layer 2: directly patch ChatOpenAI._get_request_payload ────────────

    _orig_get_payload = ChatOpenAI._get_request_payload

    def _patched_get_payload(self, input_, **kwargs):
        payload = _orig_get_payload(self, input_, **kwargs)
        msgs = payload.get("messages", [])
        for msg in msgs:
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                if "reasoning_content" not in msg:
                    pass
        return payload

    ChatOpenAI._get_request_payload = _patched_get_payload

    # ── Layer 3: stream assembly — preserve additional_kwargs ──────────────

    # Patch _astream to copy reasoning_content from additional_kwargs
    # to generation_info, which flows through to response_metadata
    # in the v2 streaming pipeline.
    _orig_astream = lc_oai_base.BaseChatOpenAI._astream

    _tc_fallback_counter = 0

    async def _patched_astream(self, messages, **kwargs):
        nonlocal _tc_fallback_counter
        async for generation_chunk in _orig_astream(self, messages, **kwargs):
            msg = generation_chunk.message
            if isinstance(msg, AIMessageChunk):
                rc = msg.additional_kwargs.get("reasoning_content")
                if rc:
                    gi = dict(generation_chunk.generation_info or {})
                    gi["reasoning_content"] = rc
                    generation_chunk.generation_info = gi
                if msg.tool_call_chunks:
                    patched = []
                    for tc in msg.tool_call_chunks:
                        tc = dict(tc)
                        if tc.get("id") is None and tc.get("name"):
                            _tc_fallback_counter += 1
                            tc["id"] = f"tc_{_tc_fallback_counter}"
                        patched.append(tc)
                    msg.tool_call_chunks = patched
                    if msg.tool_calls:
                        patched_calls = []
                        for tc in msg.tool_calls:
                            tc = dict(tc)
                            if tc.get("id") is None:
                                _tc_fallback_counter += 1
                                tc["id"] = f"tc_{_tc_fallback_counter}"
                            patched_calls.append(tc)
                        msg.tool_calls = patched_calls
            yield generation_chunk

    lc_oai_base.BaseChatOpenAI._astream = _patched_astream

    # Patch _assemble_message to extract reasoning_content from
    # response_metadata and put it into additional_kwargs.
    _orig_assemble = cms._ChatModelStreamBase._assemble_message

    def _patched_assemble(self):
        result = _orig_assemble(self)
        if isinstance(result, AIMessage):
            rc = result.response_metadata.get("reasoning_content")
            if rc:
                result.additional_kwargs["reasoning_content"] = rc
        return result

    cms._ChatModelStreamBase._assemble_message = _patched_assemble

    _patches_applied = True
    logger.info("Applied langchain_openai reasoning_content patches for DeepSeek")


def create_model(config: Config):
    """Create LLM model instance based on protocol.

    The protocol field in config determines which API client to use:
    - "openai": Use ChatOpenAI (OpenAI-compatible API)
    - "anthropic": Use ChatAnthropic (Anthropic-compatible API)

    Args:
        config: Application configuration.

    Returns:
        LangChain chat model instance.

    Raises:
        ValueError: If protocol is not supported.
    """
    protocol = config.llm.protocol.lower()

    if protocol == "openai":
        return _create_openai_compatible(config.llm)
    elif protocol == "anthropic":
        return _create_anthropic_compatible(config.llm)
    else:
        raise ValueError(
            f"Unsupported protocol: {protocol}. Use 'openai' or 'anthropic'."
        )


def _create_openai_compatible(llm_config) -> ChatOpenAI:
    """Create model using OpenAI-compatible API.

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
