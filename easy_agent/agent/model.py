"""Model factory for creating LLM instances.

Model selection is protocol-driven (see ``ProviderConfig.protocol``). The only
supported protocol is "openai": ``ReasoningChatOpenAI`` (a ChatOpenAI subclass) on
an OpenAI-compatible API. The Anthropic protocol has been removed; Claude-family
models can still be used through an OpenAI-compatible gateway.

Use ``create_model(config, model_name)`` to build an instance; config resolution
is handled by the public ``resolve_llm_config``.
"""

import json
import logging
import os

from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_openai import ChatOpenAI

from easy_agent.bootstrap.config import Config, LLMConfig, RetryConfig

logger = logging.getLogger(__name__)
#: 模型入口参数 / 发给模型的完整上下文（请求）/ 模型原始返回报文，都是 DEBUG 级别：
#: 日志级别为 debug（``EASY_LOG_LEVEL=debug`` 或 ``easy-web --log-level debug``）时才记录。
#: 只写 proc 文件，不进控制台（见 ``bootstrap/initialization/log.py`` 的
#: ``LOG_CONSOLE_EXCLUDED_LOGGERS``）。报文含完整对话内容，体量大且可能含敏感信息，仅排障时开启。
wire_logger = logging.getLogger("easy_agent.llm_wire")

# Candidate field names used by various providers for model reasoning / thinking.
# DeepSeek uses "reasoning_content"; some models use "reasoning"; others use
# "reason_content". All are treated as aliases and the original key is preserved
# so the value round-trips correctly back to the same provider.
_REASONING_KEYS = ("reasoning_content", "reasoning", "reason_content")


def llm_wire_log_enabled() -> bool:
    """当前日志级别是否记录大模型请求 / 响应报文（DEBUG）。每次请求都会检查，级别调整立即生效。"""
    return wire_logger.isEnabledFor(logging.DEBUG)


def _mask_secret(value) -> str:
    text = str(value or "")
    if len(text) <= 8:
        return "***" if text else ""
    return f"{text[:4]}***{text[-4:]}"


_SECRET_HEADERS = frozenset({"authorization", "x-api-key", "api-key", "proxy-authorization"})


def _log_http_request(request) -> None:
    """打印 SDK 最终发出的 HTTP 请求：方法、完整 URL、全部请求头与请求体。"""
    try:
        headers = {
            key: _mask_secret(value) if key.lower() in _SECRET_HEADERS else value
            for key, value in request.headers.items()
        }
        raw = request.content
        try:
            body = json.dumps(json.loads(raw), ensure_ascii=False)
        except ValueError:
            body = raw.decode("utf-8", errors="replace")
        wire_logger.debug(
            f"[LLM_REQUEST] {request.method} {request.url} | "
            f"headers={json.dumps(headers, ensure_ascii=False)} | body={body}"
        )
    except Exception as exc:  # noqa: BLE001 - 日志失败不影响请求
        logger.warning(f"[LLM_REQUEST] 打印请求失败: {exc}")


def _log_http_response(response, chunks: list[bytes]) -> None:
    """打印模型 endpoint 的原始返回：状态码、全部响应头与（解压后的）原始报文。

    流式响应即 SSE 原文（``data: {...}`` 逐行），非流式为 JSON 原文。
    """
    try:
        body = b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")
        wire_logger.debug(
            f"[LLM_RESPONSE] {response.status_code} {response.request.method} {response.request.url} | "
            f"headers={json.dumps(dict(response.headers.items()), ensure_ascii=False)} | "
            f"body={body}"
        )
    except Exception as exc:  # noqa: BLE001 - 日志失败不影响响应
        logger.warning(f"[LLM_RESPONSE] 打印响应失败: {exc}")


def _tee_response_body(response) -> None:
    # SDK 的流式解析和非流式 read() 都经 iter_bytes/aiter_bytes 读取解压后的报文；
    # 边转发边累积，流结束（或被提前关闭）时整体打印一次，不缓冲、不影响逐字输出。
    # SDK 读到 [DONE] 即停止迭代，因此不能用「迭代器是否耗尽」判断报文是否完整。
    original_iter = response.iter_bytes
    original_aiter = response.aiter_bytes

    def iter_bytes(chunk_size=None):
        chunks = []
        try:
            for chunk in original_iter(chunk_size):
                chunks.append(chunk)
                yield chunk
        finally:
            _log_http_response(response, chunks)

    async def aiter_bytes(chunk_size=None):
        chunks = []
        try:
            async for chunk in original_aiter(chunk_size):
                chunks.append(chunk)
                yield chunk
        finally:
            _log_http_response(response, chunks)

    response.iter_bytes = iter_bytes
    response.aiter_bytes = aiter_bytes


def _sync_request_hook(request) -> None:
    if llm_wire_log_enabled():
        _log_http_request(request)


async def _async_request_hook(request) -> None:
    if llm_wire_log_enabled():
        _log_http_request(request)


def _sync_response_hook(response) -> None:
    if llm_wire_log_enabled():
        _tee_response_body(response)


async def _async_response_hook(response) -> None:
    if llm_wire_log_enabled():
        _tee_response_body(response)


def _attach_hooks(http_client, request_hook, response_hook) -> None:
    # langchain 用 lru_cache 让同 base_url 的模型共享 httpx 客户端，重复挂会重复打印
    if http_client is None or not hasattr(http_client, "event_hooks"):
        return
    hooks = http_client.event_hooks
    if request_hook not in hooks["request"]:
        hooks["request"].append(request_hook)
    if response_hook not in hooks["response"]:
        hooks["response"].append(response_hook)
    http_client.event_hooks = hooks


def install_request_logging(model) -> None:
    """在模型底层 SDK 的 httpx 客户端上挂请求 / 响应钩子。

    deepagents 中间件（system prompt / bind_tools / 摘要等）改写后的请求，最终都经
    langchain → openai SDK → httpx 发出；在 httpx 层拿到的才是真正发往
    endpoint 的 URL、请求头和请求体（含 SDK 追加的 headers、extra_body 等），以及
    endpoint 返回的原始报文。
    """
    sdk_clients = [
        (getattr(model, "root_client", None), _sync_request_hook, _sync_response_hook),
        (getattr(model, "root_async_client", None), _async_request_hook, _async_response_hook),
    ]
    for sdk_client, request_hook, response_hook in sdk_clients:
        _attach_hooks(getattr(sdk_client, "_client", None), request_hook, response_hook)


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


def inject_reasoning_content(payload: dict, messages: list) -> None:
    """把每条 assistant 消息的思考写回出站请求的 ``reasoning_content`` 字段。

    ``langchain_openai._convert_message_to_dict`` 只序列化
    content/role/name/tool_calls，``additional_kwargs`` 里的 reasoning 会被丢弃。
    思考模式的上游要求回传：DeepSeek 文档写明请求带 ``tools`` 时，历史各轮的
    ``reasoning_content`` 都应回传并拼进上下文，不带工具调用的回答也一样；缺了会 400
    ``The reasoning_content in the thinking mode must be passed back to the API``。

    因此所有 assistant 消息都回传：本轮和历史轮次、带不带 ``tool_calls`` 一视同仁。
    只在消息确实带思考文本时写这个字段，非思考模型产生的消息不会多出字段。
    """
    payload_messages = payload.get("messages")
    if not isinstance(payload_messages, list) or not payload_messages:
        return
    # 只处理 chat/completions 形状；Responses API 的输入项带 type 字段
    if not all(isinstance(item, dict) and "role" in item for item in payload_messages):
        return
    if any("type" in item for item in payload_messages):
        return
    count = len(payload_messages)
    pairs = (
        zip(messages, payload_messages)
        if len(messages) == count
        else zip(messages[-count:], payload_messages)
    )
    for message, item in pairs:
        if not isinstance(message, AIMessage) or item.get("role") != "assistant":
            continue
        reasoning = extract_reasoning(message.additional_kwargs)
        if reasoning:
            item["reasoning_content"] = reasoning


try:  # LangChain 公开的「中间件内部模型调用」标记（摘要 / 工具筛选 / 工具模拟等）
    from langchain.agents.middleware.internal_call_transformer import (
        INTERNAL_CALL_METADATA_KEY as _INTERNAL_CALL_KEY,
    )
except ImportError:  # pragma: no cover - 旧版 langchain 没有该模块
    _INTERNAL_CALL_KEY = "lc_internal_call"

# 未携带 _INTERNAL_CALL_KEY 的旧版本仍会标 lc_source，作为兜底识别。
_INTERNAL_LC_SOURCES = frozenset({"summarization", "tool_selection", "tool_emulation"})


def is_internal_model_call(metadata) -> bool:
    """``stream_mode="messages"`` 的 chunk 是否来自中间件内部的模型调用。

    deepagents 的摘要中间件等会在 agent 运行中另起一次模型调用，其 token 同样从
    ``messages`` 流冒出来（官方文档 Context engineering › Summarization 要求按
    ``metadata["lc_source"] == "summarization"`` 过滤）。v3 事件流会自动剔除，
    旧 ``astream`` 不会，消费方必须自己跳过，否则摘要会被当成回复正文。
    """
    if not isinstance(metadata, dict):
        return False
    if metadata.get(_INTERNAL_CALL_KEY):
        return True
    return metadata.get("lc_source") in _INTERNAL_LC_SOURCES


# 非文本内容块：图片 / 文件 / 音视频等。纯文本模型（如 DeepSeek）收到这些块
# 会直接 400 invalid_request_error。
MEDIA_BLOCK_TYPES = frozenset({
    "image_url", "image", "input_image",
    "file", "input_file", "document",
    "audio", "input_audio", "video",
})


def _media_placeholder(block: dict) -> str:
    """把多模态块降级成文本占位，尽量保留可辨识信息（文件名 / URL 前缀）。"""
    btype = block.get("type")
    if btype in ("image_url", "image", "input_image"):
        url = ""
        if btype == "image_url":
            url = (block.get("image_url") or {}).get("url", "")
        return f"[图片内容已省略{('：' + url[:60]) if url else ''}]"
    if btype in ("file", "input_file", "document"):
        name = block.get("filename") or block.get("name") or ""
        return f"[文件内容已省略{('：' + name[:60]) if name else ''}]"
    if btype in ("audio", "input_audio"):
        return "[音频内容已省略]"
    if btype == "video":
        return "[视频内容已省略]"
    return "[媒体内容已省略]"


def _replace_blocks(messages, should_replace, placeholder_of):
    """遍历消息内容块，按谓词替换并尽量把单块结果还原成纯字符串。"""
    result = []
    for msg in messages:
        content = getattr(msg, "content", None)
        if not isinstance(content, list):
            result.append(msg)
            continue
        new_content = []
        changed = False
        for block in content:
            if should_replace(block):
                changed = True
                new_content.append({"type": "text", "text": placeholder_of(block)})
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


def strip_media_content(messages):
    """把消息列表中的多模态内容块（图片 / 文件 / 音视频）替换为文本占位。

    DeepSeek 等纯文本模型收到 image_url 或 file 块会直接 400
    invalid_request_error，例如
    ``.messages[63]: file must have a file_id or file_data``。

    幂等：对已过滤的消息重复执行无副作用。
    """

    def _should_replace(block):
        return isinstance(block, dict) and block.get("type") in MEDIA_BLOCK_TYPES

    return _replace_blocks(messages, _should_replace, _media_placeholder)


# 向后兼容：该函数最初只处理图片，现扩展到所有多模态块
strip_image_content = strip_media_content


def _is_incomplete_media_block(block) -> bool:
    """判断多媒体块是否缺少 API 要求的必填字段（缺了会连累整次请求被拒）。"""
    if not isinstance(block, dict):
        return False
    btype = block.get("type")
    if btype in ("file", "input_file"):
        # OpenAI 要求 file 块必须带 file_id 或 file_data
        return not (block.get("file_id") or block.get("file_data"))
    if btype == "image_url":
        return not (block.get("image_url") or {}).get("url")
    return False


def _incomplete_placeholder(block: dict) -> str:
    if block.get("type") in ("file", "input_file"):
        name = block.get("filename") or block.get("name") or ""
        return f"[文件引用不完整已省略{('：' + name[:60]) if name else ''}]"
    return "[图片引用不完整已省略]"


def drop_incomplete_media_blocks(messages):
    """移除不完整的多媒体块，避免整次请求被 API 拒绝。

    即使模型声明支持多模态也要执行：历史上出现过 file 块只带 filename、
    既无 file_id 也无 file_data 的情况，OpenAI 会以
    ``file must have a file_id or file_data`` 拒绝整个请求。
    """
    return _replace_blocks(
        messages, _is_incomplete_media_block, _incomplete_placeholder
    )


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
        headers=dict(provider.headers or {}),
        retry=retry,
    )


# 未显式传 session_id 时的兜底会话标识：进程内稳定即可——网关只要求「稳定」，
# 用于路由优化与 prompt 缓存。按会话区分时由调用方（EasyAgent）传入真实 session_id。
_PROCESS_SESSION_ID = f"easy-agent-{os.getpid()}"


def resolve_context_length(
    config: "Config | None", model_name: str | None = None
) -> int:
    """取「本次会话实际使用模型」的上下文窗口（``context_length``）。

    用户可在输入框切换模型，而各 provider 的 ``context_length`` 是分别配置的，
    因此上下文压缩阈值与前端上下文占用率都必须以**当前模型**的值为准，
    不能只认 ``config.llm``（那只是启动时 ``model:`` 指向的 provider）。

    优先级：``config.models[model_name].context_length`` → ``config.llm``
    → 兜底 ``1_000_000``。解析失败不抛异常（调用方多为展示/日志路径）。
    """
    fallback = 1_000_000
    if config is None:
        return fallback
    try:
        resolved = resolve_llm_config(config, model_name)
        value = int(getattr(resolved, "context_length", 0) or 0)
        return value or fallback
    except Exception:  # noqa: BLE001
        return fallback


def build_request_headers(
    llm_config: LLMConfig, session_id: str | None = None
) -> dict[str, str] | None:
    """把 config 里的 ``headers`` 展开成实际请求头（``{session_id}`` 占位符做替换）。

    用途：部分 OpenAI 兼容网关（如 OpenCode Go）要求客户端自带 User-Agent 与稳定的
    会话标识请求头，缺失会直接 400（``MissingSessionID``）。没有配置 headers 时
    返回 None，保持原有行为不变。
    """
    if not llm_config.headers:
        return None
    sid = session_id or _PROCESS_SESSION_ID
    return {
        key: str(value).replace("{session_id}", sid)
        for key, value in llm_config.headers.items()
    }


def create_model(
    config: Config, model_name: str | None = None, session_id: str | None = None
):
    """Create LLM model instance based on protocol.

    The only supported protocol is "openai" (``ReasoningChatOpenAI`` on an
    OpenAI-compatible API); any other value raises ``ValueError``.

    Args:
        config: Application configuration.
        model_name: Optional model key (from ``config.models``). When provided
            and present, the corresponding provider config is used instead of
            the active model. Useful for per-request model selection.
        session_id: Optional conversation id, substituted into configured
            request headers (``{session_id}``). Falls back to a stable
            per-process id.

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
        f"protocol={protocol} | api_base={llm_config.api_base} | "
        f"context_length={llm_config.context_length}"
    )

    if protocol != "openai":
        raise ValueError(
            f"Unsupported protocol: {protocol}. Only 'openai' (OpenAI-compatible API) is "
            "supported; the Anthropic protocol has been removed."
        )
    model = _create_openai_compatible(
        llm_config, default_headers=build_request_headers(llm_config, session_id)
    )
    # 钩子总是挂上，每次请求再按日志级别决定是否记录（非 DEBUG 时只多一次级别判断）
    install_request_logging(model)
    return apply_context_profile(model, llm_config.context_length)


#: ``config_models`` 声明的 context_length 缺省值：没配置时不覆盖 LangChain 内置 profile。
_UNSET_CONTEXT_LENGTH = LLMConfig.model_fields["context_length"].default


def apply_context_profile(model, context_length: int | None):
    """把配置的上下文窗口写进 ``model.profile["max_input_tokens"]``（DA-06）。

    deepagents 按 profile 计算默认摘要阈值；OpenAI 兼容网关上的模型没有内置
    profile，框架会退回 170k tokens 的固定阈值。只覆盖 ``max_input_tokens``，其余
    能力字段（图片输入、结构化输出等）保留；未配置 context_length 时不动内置值，
    避免用占位的缺省值盖掉准确的窗口大小。
    """
    value = int(context_length or 0)
    if value <= 0 or value == _UNSET_CONTEXT_LENGTH:
        return model
    try:
        model.profile = {**(getattr(model, "profile", None) or {}), "max_input_tokens": value}
    except Exception as exc:  # noqa: BLE001 - 不支持赋值的模型类不阻断建模
        logger.warning(f"设置模型 profile.max_input_tokens 失败（保留原值）: {exc}")
    return model


class ReasoningChatOpenAI(ChatOpenAI):
    """ChatOpenAI 子类，承载三个独立职责：

    职责 A - 多模态清理：在流式/非流式入口统一处理消息内容块
        （不再区分模型是否支持视觉）：
        1) 剔除不完整的块（如既无 file_id 也无 file_data 的 file 块、
           缺 url 的 image_url）——这类块会让 API 拒绝**整个请求**；
        2) 其余多模态块（image_url / file / 音视频等）降级为文本占位，
           避免 DeepSeek 等纯文本模型收到它们报 400 invalid_request_error。
    职责 B - reasoning 提取：把 OpenAI 兼容接口返回的思考内容
        (reasoning_content / reasoning / reason_content) 写入 additional_kwargs，
        供前端展示与出站回传。
    职责 C - reasoning 回传：出站请求里给每条带思考的 assistant 消息（本轮和历史
        轮次、带不带 tool_calls）补回 ``reasoning_content``（langchain 自己不序列化它）。
    """

    # ── 职责 A：多模态清理 ───────────────────────────────────────────
    def _sanitize_messages(self, messages, **kwargs):
        """发送前统一清理消息内容块（不区分模型是否支持视觉）。

        1) 剔除不完整的块：缺 file_id/file_data 的 file、缺 url 的 image_url
           —— 这类块会让 API 拒绝**整个请求**；
        2) 其余多模态块降级为文本占位，保证纯文本模型不会 400。
        """
        messages = drop_incomplete_media_blocks(messages)
        return strip_media_content(messages), kwargs

    async def _astream(self, messages, stop=None, **kwargs):
        messages, kwargs = self._sanitize_messages(messages, **kwargs)
        async for chunk in super()._astream(messages, stop=stop, **kwargs):
            yield chunk

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        messages, kwargs = self._sanitize_messages(messages, **kwargs)
        return await super()._agenerate(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )

    def _stream(self, messages, stop=None, **kwargs):
        messages, kwargs = self._sanitize_messages(messages, **kwargs)
        yield from super()._stream(messages, stop=stop, **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        messages, kwargs = self._sanitize_messages(messages, **kwargs)
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

    def _create_chat_result(self, response, generation_info=None):
        """非流式响应也提取思考内容（调度任务等走 ``ainvoke`` 的链路）。"""
        result = super()._create_chat_result(response, generation_info)
        try:
            response_dict = (
                response if isinstance(response, dict) else response.model_dump()
            )
            choices = response_dict.get("choices") or []
            for generation, choice in zip(result.generations, choices):
                found = _extract_reasoning(choice.get("message") or {})
                if found and isinstance(found[1], str):
                    generation.message.additional_kwargs["reasoning_content"] = found[1]
        except Exception as exc:  # noqa: BLE001 - 提取失败不影响正常返回
            logger.debug(f"非流式响应提取 reasoning 失败: {exc}")
        return result

    # ── 职责 C：reasoning 回传 ────────────────────────────────────────
    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        try:
            messages = self._convert_input(input_).to_messages()
        except Exception as exc:  # noqa: BLE001 - 对齐失败就按原载荷发送
            logger.debug(f"出站消息对齐失败，跳过 reasoning 回传: {exc}")
            return payload
        inject_reasoning_content(payload, messages)
        return payload


def _create_openai_compatible(
    llm_config, default_headers: dict[str, str] | None = None
) -> ChatOpenAI:
    """Create model using OpenAI-compatible API."""
    extra = {"default_headers": default_headers} if default_headers else {}
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
        **extra,
    )
