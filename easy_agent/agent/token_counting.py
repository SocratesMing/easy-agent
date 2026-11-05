"""摘要用的本地混合文本估算；不替代服务端返回的实际 usage。

LangChain 默认按 4 字符/token 估算，适用于常见英文，中文原文会被低估数倍。
保留框架对消息、工具 schema、图片的处理，把非 ASCII 文本补足到 1 字符/token，
并计入本项目在工具续跑请求里额外回传的 reasoning_content。无需下载 tokenizer。
"""

from __future__ import annotations

import math
import re

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.utils import convert_to_messages, count_tokens_approximately

from easy_agent.agent.model import extract_reasoning

_ASCII = re.compile(r"[\x00-\x7f]+")


def _non_ascii_count(text: str) -> int:
    return len(_ASCII.sub("", text))


def count_context_tokens(messages, *, tools=None) -> int:
    """ASCII 仍按 4 字符/token，非 ASCII 按 1 字符/token 的保守估算。

    与框架保持同一内容边界：图片使用框架固定开销，不扫描 base64；工具 schema
    已被框架按 ASCII JSON 转义计数，不重复补差。只统计出站工具续跑时会回传的
    思考；历史思考在 render_turn_request / build_context_messages 中剥离。
    """
    messages = convert_to_messages(messages)
    total = count_tokens_approximately(messages, tools=tools)
    non_ascii = 0
    reasoning_tokens = 0
    for message in messages:
        content = message.content
        if isinstance(content, str):
            non_ascii += _non_ascii_count(content)
        else:
            for block in content:
                if isinstance(block, str):
                    text = block
                elif isinstance(block, dict) and block.get("type") in {"image", "image_url"}:
                    continue
                elif isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                else:
                    text = repr(block)
                non_ascii += _non_ascii_count(text)
        non_ascii += _non_ascii_count(message.name or "")
        if isinstance(message, ToolMessage):
            non_ascii += _non_ascii_count(message.tool_call_id)
        if isinstance(message, AIMessage):
            if isinstance(content, str) and message.tool_calls:
                non_ascii += _non_ascii_count(repr(message.tool_calls))
            if message.tool_calls or message.invalid_tool_calls:
                reasoning = extract_reasoning(message.additional_kwargs)
                reasoning_tokens += math.ceil((len(reasoning) + 3 * _non_ascii_count(reasoning)) / 4)
    # 默认已把非 ASCII 算作 1/4 token，补上余下 3/4。保留官方中间件原本启用的
    # usage 校准：取较大值，避免在已按真实用量放大之后再次对中文补差。
    mixed = total + math.ceil(non_ascii * 3 / 4) + reasoning_tokens
    calibrated = count_tokens_approximately(
        messages, tools=tools, use_usage_metadata_scaling=True,
    )
    return max(mixed, calibrated)
