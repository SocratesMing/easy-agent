"""Chat streaming service - SSE streaming generator with thinking/tool support"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ..agent import EasyAgent
from ..db import Database
from ..models.api import ChatRequest
from ..utils.session_logger import SessionLogger
from .agent_manager import get_agent_config

logger = logging.getLogger("easy_agent.chat_service")

MAX_CONTEXT_MESSAGES = 30
KEEP_RECENT_MESSAGES = 10


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - chinese_chars
    return int(chinese_chars / 1.5 + other_chars / 4)


_MODEL_CONTEXT_LIMITS = {
    "deepseek": 131072,
    "gpt-4": 131072,
    "gpt-4o": 131072,
    "gpt-4-turbo": 131072,
    "gpt-3.5": 16385,
    "claude": 200000,
    "qwen": 131072,
    "glm": 131072,
    "minimax": 131072,
    "moonshot": 131072,
    "yi": 131072,
}


def _get_model_context_limit(model_instance) -> int | None:
    if model_instance is None:
        return None
    model_name = ""
    if hasattr(model_instance, "model_name"):
        model_name = model_instance.model_name or ""
    elif hasattr(model_instance, "model"):
        model_name = model_instance.model or ""
    if not model_name:
        return None
    model_lower = model_name.lower()
    for prefix, limit in _MODEL_CONTEXT_LIMITS.items():
        if prefix in model_lower:
            return limit
    return None


async def compress_context(
    messages: list[dict],
    keep_recent: int = KEEP_RECENT_MESSAGES,
) -> tuple[list[dict], str, int]:
    from ..model import create_model

    if len(messages) <= keep_recent + 4:
        return messages, "", len(messages)

    recent = messages[-keep_recent:]
    to_compress = messages[:-keep_recent]

    conversation_text = ""
    for msg in to_compress:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str) and content.strip():
            conversation_text += f"[{role}]: {content[:500]}\n"

    if not conversation_text.strip():
        return messages, "", len(messages)

    try:
        agent_cfg = get_agent_config()
        llm = create_model(agent_cfg["config"])
        summary_prompt = (
            "你是一个对话压缩助手。请将以下对话历史压缩为一段简短的摘要，"
            "保留关键信息、用户需求、已做出的决策和已完成的工作。\n\n"
            f"{conversation_text}\n\n压缩摘要："
        )
        result = await llm.ainvoke([HumanMessage(content=summary_prompt)])
        summary = result.content if hasattr(result, "content") else str(result)
        summary = summary.strip()

        compressed_msg = {
            "role": "system",
            "content": f"[历史对话压缩摘要]:\n{summary}\n\n[以下是最新的对话内容]:",
        }
        compressed = [compressed_msg] + recent
        return compressed, summary, len(to_compress)
    except Exception as e:
        logger.warning(f"上下文压缩失败: {e}")
        return messages, "", len(messages)


async def build_context_messages(
    db: Database,
    session_id: str,
    current_message: str,
    session_logger: SessionLogger = None,
) -> str:
    session = db.get_session(session_id)
    if not session or len(session.messages) <= 4:
        return current_message

    session_messages = session.messages

    if len(session_messages) > MAX_CONTEXT_MESSAGES:
        compressed_msgs, summary, original_count = await compress_context(
            session_messages
        )
        if summary:
            context_summary = (
                f"[历史对话摘要（前{original_count}条消息已压缩）]:\n{summary}\n\n"
            )
            if session_logger:
                session_logger.log_context_compression(
                    summary, original_count, len(compressed_msgs)
                )
            logger.info(
                f"[{session_id[-5:]}] 上下文已压缩 | 原消息数: {original_count} | 摘要长度: {len(summary)}"
            )
            return context_summary + current_message

    return current_message


def _update_accumulated_args(
    tool_call_accumulated_args: dict,
    tool_name: str,
    args_data: any,
    sid: str = "",
) -> None:
    """Update accumulated tool call arguments from a chunk."""
    if isinstance(args_data, dict):
        if tool_name in tool_call_accumulated_args and isinstance(
            tool_call_accumulated_args[tool_name], dict
        ):
            tool_call_accumulated_args[tool_name].update(args_data)
        else:
            tool_call_accumulated_args[tool_name] = args_data.copy()
        logger.info(
            f"[{sid}] 📦 _update_accumulated_args(dict) {tool_name}: {json.dumps(tool_call_accumulated_args[tool_name], ensure_ascii=False)[:200]}"
        )
    elif isinstance(args_data, str) and args_data:
        if tool_name not in tool_call_accumulated_args or not isinstance(
            tool_call_accumulated_args[tool_name], str
        ):
            tool_call_accumulated_args[tool_name] = args_data
        else:
            tool_call_accumulated_args[tool_name] += args_data
        try:
            parsed = json.loads(tool_call_accumulated_args[tool_name])
            if isinstance(parsed, dict):
                tool_call_accumulated_args[tool_name] = parsed
            else:
                tool_call_accumulated_args[tool_name] = {"value": parsed}
            logger.info(
                f"[{sid}] 📦 _update_accumulated_args(str→dict) {tool_name}: {json.dumps(parsed, ensure_ascii=False)[:200]}"
            )
        except json.JSONDecodeError:
            pass
    else:
        if tool_name not in tool_call_accumulated_args:
            tool_call_accumulated_args[tool_name] = {}


async def chat_stream_generator(
    request: ChatRequest,
    db: Database,
    agent: EasyAgent,
    session_id: str,
    message_id: str,
    username: str,
    http_request=None,
    parsed_content: str = None,
    session_logger=None,
) -> AsyncGenerator[str, None]:
    start_time = time.time()
    sid = session_id[-5:] if session_id else "new"

    message_content = parsed_content or request.message

    if agent and agent.workspace_dir:
        ws = agent.workspace_dir.absolute().as_posix()
        message_content = f"[workspace: {ws} | tool path: /workspace/xxx | shell: cd {ws}]\n{message_content}"

    if session_id and db:
        message_content = await build_context_messages(
            db, session_id, message_content, session_logger
        )

    ws_info = (
        str(agent.workspace_dir.absolute())
        if agent and agent.workspace_dir
        else "unknown"
    )
    logger.info(
        f"[{sid}] 开始流式响应 | workspace: {ws_info} | message: {message_content[:50]}{'...' if len(message_content) > 50 else ''} | 用户: {username}"
    )

    def format_sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    try:
        yield format_sse({"type": "start", "session_id": session_id})

        start_time = time.time()

        has_streaming = hasattr(agent.agent, "astream")
        blocks = []
        tool_call_records = []
        accumulated_thinking = ""
        accumulated_response = ""
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        max_input_tokens = None
        auto_compress_tokens = None
        model_instance = getattr(agent, "model", None)
        if (
            model_instance
            and hasattr(model_instance, "profile")
            and model_instance.profile
        ):
            max_input_tokens = model_instance.profile.get("max_input_tokens")
        if not max_input_tokens:
            max_input_tokens = _get_model_context_limit(model_instance)
        if max_input_tokens:
            auto_compress_tokens = int(max_input_tokens * 0.85)
        else:
            auto_compress_tokens = 170000

        if has_streaming:
            logger.info(f"[{sid}] 🚀 使用 DeepAgents 流式接口(支持 skills)")

            context_messages = []
            session = db.get_session(session_id)
            if session and session.messages:
                provider = (
                    agent.config.llm.provider.lower() if agent and agent.config else ""
                )
                for msg in session.messages:
                    if msg.get("role") == "user":
                        context_messages.append(
                            HumanMessage(content=str(msg.get("content", "")))
                        )
                    elif msg.get("role") == "assistant":
                        assistant_content = str(msg.get("content", ""))
                        thinking = msg.get("thinking")
                        if thinking:
                            if provider == "deepseek":
                                context_messages.append(
                                    AIMessage(
                                        content=assistant_content,
                                        additional_kwargs={
                                            "reasoning_content": thinking
                                        },
                                    )
                                )
                            else:
                                assistant_content = (
                                    f"[思考]: {thinking}\n\n{assistant_content}"
                                )
                                context_messages.append(
                                    AIMessage(content=assistant_content)
                                )
                        else:
                            context_messages.append(
                                AIMessage(content=assistant_content)
                            )

            context_messages.append(HumanMessage(content=message_content))
            messages = context_messages
            thinking_started = False
            thinking_start_time = None
            thinking_end_time = None
            current_step = 0
            tool_call_start_times = {}
            tool_call_step_map = {}
            tool_call_block_added = set()

            accumulated_content = ""
            is_in_thinking = False
            _thinking_from_reasoning = False  # True when thinking started by reasoning_content
            content_start_time = None
            total_tool_duration = 0
            is_after_tool_result = False
            tool_call_accumulated_args = {}
            tool_call_id_map = {}
            tool_call_order = []
            last_persisted_len = 0

            block_order = 0
            current_content_block = None

            def _end_thinking():
                """End the current thinking step and emit thinking_end event."""
                nonlocal is_in_thinking, thinking_start_time, thinking_end_time
                nonlocal _thinking_from_reasoning, current_content_block
                now = time.time()
                thinking_end_time = now
                thinking_duration = (
                    now - thinking_start_time if thinking_start_time else 0
                )
                thinking_clean = accumulated_thinking.strip()
                if thinking_clean:
                    try:
                        db.record_thinking(
                            session_id=session_id,
                            message_id=message_id,
                            step=current_step,
                            content=thinking_clean[:10000],
                            duration=round(thinking_duration, 2),
                        )
                    except Exception as e:
                        logger.warning(f"[{sid}] 持久化思考记录失败: {e}")
                    if session_logger:
                        session_logger.log_thinking(
                            content=thinking_clean,
                            step=current_step,
                            duration=round(thinking_duration, 2),
                            message_id=message_id,
                        )
                    logger.info(
                        f"[{sid}] 🤔 Step {current_step} 思考完成 | 耗时: {thinking_duration:.2f}s | 长度: {len(thinking_clean)}"
                    )
                for blk in reversed(blocks):
                    if blk["type"] == "thinking" and blk["step"] == current_step:
                        blk["content"] = thinking_clean
                        blk["duration"] = round(thinking_duration, 2)
                        break
                yield format_sse(
                    {
                        "type": "thinking_end",
                        "duration": round(thinking_duration, 2),
                        "step": current_step,
                    }
                )
                is_in_thinking = False
                _thinking_from_reasoning = False
                current_content_block = None

            def build_partial_assistant_message():
                return {
                    "role": "assistant",
                    "content": accumulated_response if accumulated_response else "",
                    "timestamp": datetime.now().isoformat(),
                    "thinking": accumulated_thinking if accumulated_thinking else None,
                    "thinking_duration": None,
                    "tool_calls": [
                        {
                            "tool_name": tc[0],
                            "tool_call_id": tc[1],
                            "arguments": tc[2],
                            "result": str(tc[3])[:5000],
                            "success": tc[4],
                            "duration": tc[5],
                            "step": tc[6],
                        }
                        for tc in tool_call_records
                    ]
                    if tool_call_records
                    else None,
                    "blocks": blocks if blocks else None,
                }

            def persist_partial():
                try:
                    msg = build_partial_assistant_message()
                    db.update_last_assistant_message(session_id, msg)
                    db.update_last_assistant_message_row(session_id, msg)
                except Exception as e:
                    logger.warning(f"[{sid}] 增量持久化失败: {e}")

            last_token_usage_time = 0

            def build_token_usage_event():
                nonlocal last_token_usage_time
                now = time.time()
                if now - last_token_usage_time < 0.5:
                    return None
                last_token_usage_time = now
                est_input = total_usage["input_tokens"] or estimate_tokens(
                    message_content
                )
                est_output = total_usage["output_tokens"] or estimate_tokens(
                    accumulated_response
                ) + estimate_tokens(accumulated_thinking)
                return format_sse(
                    {
                        "type": "token_usage",
                        "input_tokens": est_input,
                        "output_tokens": est_output,
                        "total_tokens": est_input + est_output,
                        "max_input_tokens": max_input_tokens,
                        "auto_compress_tokens": auto_compress_tokens,
                    }
                )

            async for chunk in agent.agent.astream(
                {"messages": messages},
                stream_mode="messages",
                subgraphs=True,
                version="v2",
            ):
                if chunk["type"] != "messages":
                    continue

                token, metadata = chunk["data"]
                ns = chunk.get("ns", [])

                if hasattr(token, "usage_metadata") and token.usage_metadata:
                    um = token.usage_metadata
                    logger.info(f"[{sid}] 📊 usage_metadata found: {um}")
                    total_usage["input_tokens"] += um.get("input_tokens", 0) or 0
                    total_usage["output_tokens"] += um.get("output_tokens", 0) or 0
                    total_usage["total_tokens"] += um.get("total_tokens", 0) or 0
                    tu = build_token_usage_event()
                    if tu:
                        yield tu
                elif hasattr(token, "response_metadata") and token.response_metadata:
                    rm = token.response_metadata
                    if "usage" in rm:
                        usage = rm["usage"]
                        logger.info(
                            f"[{sid}] 📊 response_metadata.usage found: {usage}"
                        )
                        total_usage["input_tokens"] += (
                            usage.get("prompt_tokens", 0) or 0
                        )
                        total_usage["output_tokens"] += (
                            usage.get("completion_tokens", 0) or 0
                        )
                        total_usage["total_tokens"] += usage.get("total_tokens", 0) or 0
                        tu = build_token_usage_event()
                        if tu:
                            yield tu
                else:
                    token_type = type(token).__name__
                    has_usage_meta = hasattr(token, "usage_metadata")
                    has_resp_meta = hasattr(token, "response_metadata")
                    if total_usage["total_tokens"] == 0 and has_usage_meta:
                        logger.info(
                            f"[{sid}] 📊 token type={token_type}, usage_metadata={getattr(token, 'usage_metadata', None)}, response_metadata keys={list(getattr(token, 'response_metadata', {}).keys()) if has_resp_meta else 'N/A'}"
                        )

                is_subagent = any(s.startswith("tools:") for s in ns)
                if is_subagent:
                    # Only skip content/thinking from subagents.
                    # Tool calls/results are still needed for arg accumulation.
                    has_tool_info = (
                        isinstance(token, ToolMessage)
                        or (hasattr(token, "tool_calls") and token.tool_calls)
                        or (hasattr(token, "tool_call_chunks") and token.tool_call_chunks)
                    )
                    if not has_tool_info:
                        continue

                content = getattr(token, "content", "")
                if content and not isinstance(token, ToolMessage):
                    if isinstance(content, list):
                        has_unhandled = False
                        for block in content:
                            block_type = block.get("type", "")
                            if block_type in ("thinking", "thinking_delta"):
                                thinking_text = block.get("thinking", "")
                                if thinking_text:
                                    if not is_in_thinking:
                                        is_in_thinking = True
                                        _thinking_from_reasoning = True
                                        is_after_tool_result = False
                                        thinking_start_time = time.time()
                                        current_step += 1
                                        logger.info(f"[{sid}] 🤔 Step {current_step} 思考开始 (Anthropic)")
                                        block_order += 1
                                        blocks.append(
                                            {
                                                "type": "thinking",
                                                "content": "",
                                                "order": block_order,
                                                "step": current_step,
                                            }
                                        )
                                        yield format_sse(
                                            {
                                                "type": "thinking_start",
                                                "content": "",
                                                "step": current_step,
                                            }
                                        )
                                    accumulated_thinking += thinking_text
                                    yield format_sse(
                                        {
                                            "type": "thinking",
                                            "content": thinking_text,
                                            "step": current_step,
                                        }
                                    )
                            elif block_type in ("text", "text_delta"):
                                text = block.get("text", "")
                                if text:
                                    if is_in_thinking:
                                        for _ in _end_thinking():
                                            yield _
                                    accumulated_response += text
                                    if not current_content_block:
                                        block_order += 1
                                        current_content_block = {
                                            "type": "content",
                                            "content": "",
                                            "order": block_order,
                                        }
                                        blocks.append(current_content_block)
                                    current_content_block["content"] += text
                                    yield format_sse(
                                        {
                                            "type": "content",
                                            "content": text,
                                        }
                                    )
                                    tu = build_token_usage_event()
                                    if tu:
                                        yield tu
                                    if len(accumulated_response) - last_persisted_len >= 500:
                                        last_persisted_len = len(accumulated_response)
                                        persist_partial()
                            elif block_type == "redacted_thinking":
                                pass
                            else:
                                has_unhandled = True
                        if not has_unhandled:
                            continue
                        content_str = ""
                    else:
                        content_str = str(content)
                        accumulated_content += content_str

                    if not is_in_thinking and "<think" in content_str.lower():
                        is_in_thinking = True
                        is_after_tool_result = False
                        time.time()
                        thinking_start_time = time.time()
                        current_step += 1
                        logger.info(f"[{sid}] 🤔 Step {current_step} 思考开始")
                        block_order += 1
                        blocks.append(
                            {
                                "type": "thinking",
                                "content": "",
                                "order": block_order,
                                "step": current_step,
                            }
                        )
                        yield format_sse(
                            {
                                "type": "thinking_start",
                                "content": "",
                                "step": current_step,
                            }
                        )

                    if is_in_thinking and "</think" in content_str.lower():
                        end_match = re.search(
                            r"</think[^>]*>", content_str, re.IGNORECASE
                        )
                        if end_match:
                            thinking_part = content_str[: end_match.start()]
                            response_part = content_str[end_match.end() :]

                            if thinking_part.strip():
                                yield format_sse(
                                    {
                                        "type": "thinking",
                                        "content": thinking_part,
                                        "step": current_step,
                                    }
                                )
                                accumulated_thinking += thinking_part

                            thinking_end_time = time.time()
                            thinking_duration = (
                                thinking_end_time - thinking_start_time
                                if thinking_start_time
                                else 0
                            )

                            thinking_clean = (
                                accumulated_thinking.replace(" thinking", "")
                                .replace(" response", "")
                                .replace("<think ", "")
                                .replace("</think ", "")
                                .strip()
                            )
                            try:
                                db.record_thinking(
                                    session_id=session_id,
                                    message_id=message_id,
                                    step=current_step,
                                    content=thinking_clean[:10000],
                                    duration=round(thinking_duration, 2),
                                )
                            except Exception as e:
                                logger.warning(f"[{sid}] 持久化思考记录失败: {e}")

                            if session_logger and thinking_clean.strip():
                                session_logger.log_thinking(
                                    content=thinking_clean,
                                    step=current_step,
                                    duration=round(thinking_duration, 2),
                                    message_id=message_id,
                                )

                            logger.info(
                                f"[{sid}] 🤔 Step {current_step} 思考完成 | 耗时: {thinking_duration:.2f}s | 内容长度: {len(thinking_clean)} | 内容: {thinking_clean[:50]}"
                            )
                            for blk in reversed(blocks):
                                if (
                                    blk["type"] == "thinking"
                                    and blk["step"] == current_step
                                ):
                                    blk["content"] = thinking_clean
                                    blk["duration"] = round(thinking_duration, 2)
                                    break
                            yield format_sse(
                                {
                                    "type": "thinking_end",
                                    "duration": round(thinking_duration, 2),
                                    "step": current_step,
                                }
                            )

                            is_in_thinking = False

                            if response_part.strip():
                                if content_start_time is None:
                                    content_start_time = time.time()
                                if not current_content_block:
                                    block_order += 1
                                    current_content_block = {
                                        "type": "content",
                                        "content": "",
                                        "order": block_order,
                                    }
                                    blocks.append(current_content_block)
                                current_content_block["content"] += response_part
                                yield format_sse(
                                    {
                                        "type": "content",
                                        "content": response_part,
                                    }
                                )
                                accumulated_response += response_part
                                tu = build_token_usage_event()
                                if tu:
                                    yield tu
                    elif is_in_thinking:
                        # If thinking was started by reasoning_content, end it when
                        # actual content arrives (the model has finished reasoning)
                        if _thinking_from_reasoning:
                            for _ in _end_thinking():
                                yield _
                            # Process content as regular response text
                            accumulated_response += content_str
                            if not current_content_block:
                                block_order += 1
                                current_content_block = {
                                    "type": "content",
                                    "content": "",
                                    "order": block_order,
                                }
                                blocks.append(current_content_block)
                            current_content_block["content"] += content_str
                            yield format_sse(
                                {
                                    "type": "content",
                                    "content": content_str,
                                }
                            )
                            tu = build_token_usage_event()
                            if tu:
                                yield tu
                            if len(accumulated_response) - last_persisted_len >= 500:
                                last_persisted_len = len(accumulated_response)
                                persist_partial()
                        else:
                            accumulated_thinking += content_str
                            yield format_sse(
                                {
                                    "type": "thinking",
                                    "content": content_str,
                                    "step": current_step,
                                }
                            )
                    else:
                        if not content_str:
                            continue
                        if is_after_tool_result:
                            logger.info(
                                f"[{sid}] ⏭️ Step {current_step} 跳过重复 content | 长度: {len(content_str)}"
                            )
                            is_after_tool_result = False
                        else:
                            accumulated_response += content_str
                            if len(accumulated_response) // 200 > (len(accumulated_response) - len(content_str)) // 200:
                                logger.info(
                                    f"[{sid}] 📤 Step {current_step} 正式内容 ({len(accumulated_response)} chars): {accumulated_response[:200]}..."
                                )
                            if not current_content_block:
                                block_order += 1
                                current_content_block = {
                                    "type": "content",
                                    "content": "",
                                    "order": block_order,
                                }
                                blocks.append(current_content_block)
                            current_content_block["content"] += content_str
                            yield format_sse(
                                {
                                    "type": "content",
                                    "content": content_str,
                                }
                            )
                            tu = build_token_usage_event()
                            if tu:
                                yield tu
                            if len(accumulated_response) - last_persisted_len >= 500:
                                last_persisted_len = len(accumulated_response)
                                persist_partial()

                if hasattr(token, "tool_call_chunks") and token.tool_call_chunks:
                    # End thinking if tool call arrives while still in thinking mode
                    if is_in_thinking:
                        for _ in _end_thinking():
                            yield _

                    for tc_chunk in token.tool_call_chunks:
                        tool_name_from_chunk = tc_chunk.get("name")
                        args_data = tc_chunk.get("args")
                        chunk_id = tc_chunk.get("id")

                        if tool_name_from_chunk:
                            tool_name = tool_name_from_chunk
                            tool_call_order.append(tool_name)
                            tool_call_id_map[tool_name] = (
                                chunk_id or f"tool-{tool_name}"
                            )
                            tool_call_start_times[tool_name] = time.time()
                            tool_call_step_map[tool_name] = current_step
                            current_content_block = None
                            block_order += 1
                            tc_block = {
                                "type": "tool_call",
                                "tool_name": tool_name,
                                "tool_call_id": chunk_id or f"tool-{tool_name}",
                                "arguments": {},
                                "result": "",
                                "success": True,
                                "order": block_order,
                                "step": current_step,
                            }
                            blocks.append(tc_block)
                            tool_call_block_added.add(tool_name)
                            _update_accumulated_args(
                                tool_call_accumulated_args, tool_name, args_data, sid
                            )

                            yield format_sse(
                                {
                                    "type": "tool_call",
                                    "tool_name": tool_name,
                                    "tool_call_id": chunk_id or f"tool-{tool_name}",
                                    "arguments": tool_call_accumulated_args[tool_name],
                                    "step": current_step,
                                }
                            )
                        elif chunk_id:
                            tool_name = next(
                                (
                                    name
                                    for name, cid in tool_call_id_map.items()
                                    if cid == chunk_id
                                ),
                                None,
                            )
                            if not tool_name:
                                continue
                            _update_accumulated_args(
                                tool_call_accumulated_args, tool_name, args_data, sid
                            )
                            for blk in reversed(blocks):
                                if (
                                    blk["type"] == "tool_call"
                                    and blk["tool_name"] == tool_name
                                ):
                                    blk["arguments"] = tool_call_accumulated_args[
                                        tool_name
                                    ]
                                    break
                            yield format_sse(
                                {
                                    "type": "tool_call",
                                    "tool_name": tool_name,
                                    "tool_call_id": chunk_id,
                                    "arguments": tool_call_accumulated_args[tool_name],
                                    "step": current_step,
                                }
                            )

                        elif tool_call_order:
                            tool_name = tool_call_order[-1]
                            _update_accumulated_args(
                                tool_call_accumulated_args, tool_name, args_data, sid
                            )
                            for blk in reversed(blocks):
                                if (
                                    blk["type"] == "tool_call"
                                    and blk["tool_name"] == tool_name
                                ):
                                    blk["arguments"] = tool_call_accumulated_args[
                                        tool_name
                                    ]
                                    break
                            yield format_sse(
                                {
                                    "type": "tool_call",
                                    "tool_name": tool_name,
                                    "tool_call_id": tool_call_id_map.get(tool_name, ""),
                                    "arguments": tool_call_accumulated_args[tool_name],
                                    "step": current_step,
                                }
                            )

                if hasattr(token, "tool_calls") and token.tool_calls:
                    # End thinking if tool calls arrive while still in thinking mode
                    if is_in_thinking:
                        for _ in _end_thinking():
                            yield _

                    for tc in token.tool_calls:
                        tool_name = tc.get("name", "")
                        tool_args = tc.get("args", {})
                        tool_call_id = tc.get("id", "")

                        if tool_name:
                            log_args = tool_args if tool_args and (not isinstance(tool_args, dict) or len(tool_args) > 0) else tool_call_accumulated_args.get(tool_name, tool_args)
                            logger.info(f"[{sid}] 🔧 token.tool_calls: name={tool_name}, args={json.dumps(log_args, ensure_ascii=False)[:200]}, id={tool_call_id}")
                            tool_call_id_map[tool_name] = tool_call_id
                            tool_call_start_times[tool_name] = time.time()
                            tool_call_step_map[tool_name] = current_step
                            current_content_block = None
                            # Update existing block if already created by tool_call_chunks
                            existing_tc = None
                            for blk in reversed(blocks):
                                if (
                                    blk["type"] == "tool_call"
                                    and blk.get("tool_call_id") == tool_call_id
                                ):
                                    existing_tc = blk
                                    break
                            if existing_tc:
                                existing_tc["arguments"] = tool_args
                                existing_tc["step"] = current_step
                                tc_block = existing_tc
                            else:
                                block_order += 1
                                tc_block = {
                                    "type": "tool_call",
                                    "tool_name": tool_name,
                                    "tool_call_id": tool_call_id or f"tool-{tool_name}",
                                    "arguments": tool_args,
                                    "result": "",
                                    "success": True,
                                    "order": block_order,
                                    "step": current_step,
                                }
                                blocks.append(tc_block)
                            tool_call_block_added.add(tool_name)
                            # Only set if non-empty; tool_call_chunks may have better accumulated data
                            if tool_args and isinstance(tool_args, dict) and len(tool_args) > 0:
                                tool_call_accumulated_args[tool_name] = tool_args
                                logger.info(
                                    f"[{sid}] 📦 tool_calls set {tool_name}: {json.dumps(tool_args, ensure_ascii=False)[:200]}"
                                )
                            elif tool_name not in tool_call_accumulated_args:
                                tool_call_accumulated_args[tool_name] = tool_args

                            yield format_sse(
                                {
                                    "type": "tool_call",
                                    "tool_name": tool_name,
                                    "tool_call_id": tool_call_id or f"tool-{tool_name}",
                                    "arguments": tool_call_accumulated_args[tool_name],
                                    "step": current_step,
                                }
                            )

                if hasattr(token, "additional_kwargs") and token.additional_kwargs:
                    ak = token.additional_kwargs

                    reasoning_content = ak.get("reasoning_content")
                    if reasoning_content is not None:
                        if (
                            isinstance(reasoning_content, str)
                            and reasoning_content.strip()
                        ):
                            if not is_in_thinking:
                                is_in_thinking = True
                                _thinking_from_reasoning = True
                                is_after_tool_result = False
                                time.time()
                                thinking_start_time = time.time()
                                current_step += 1
                                logger.info(
                                    f"[{sid}] 🤔 Step {current_step} 思考开始 (reasoning_content)"
                                )
                                block_order += 1
                                blocks.append(
                                    {
                                        "type": "thinking",
                                        "content": "",
                                        "order": block_order,
                                        "step": current_step,
                                    }
                                )
                                yield format_sse(
                                    {
                                        "type": "thinking_start",
                                        "content": "",
                                        "step": current_step,
                                    }
                                )
                            accumulated_thinking += reasoning_content
                            yield format_sse(
                                {
                                    "type": "thinking",
                                    "content": reasoning_content,
                                    "step": current_step,
                                }
                            )

                    if "tool_calls" in ak:
                        for tc in ak["tool_calls"]:
                            tc_id = tc.get("id", "")
                            tc_name = tc.get("function", {}).get("name", "")
                            tc_args_str = tc.get("function", {}).get("arguments", "{}")

                            if tc_name:
                                try:
                                    tc_args = (
                                        json.loads(tc_args_str)
                                        if isinstance(tc_args_str, str)
                                        else tc_args_str
                                    )
                                except json.JSONDecodeError:
                                    tc_args = {"raw": tc_args_str}

                                tool_call_id_map[tc_name] = tc_id
                                tool_call_start_times[tc_name] = time.time()
                                tool_call_step_map[tc_name] = current_step
                                current_content_block = None
                                block_order += 1
                                tc_block = {
                                    "type": "tool_call",
                                    "tool_name": tc_name,
                                    "tool_call_id": tc_id or f"tool-{tc_name}",
                                    "arguments": tc_args,
                                    "result": "",
                                    "success": True,
                                    "order": block_order,
                                    "step": current_step,
                                }
                                blocks.append(tc_block)
                                tool_call_block_added.add(tc_name)
                                tool_call_accumulated_args[tc_name] = tc_args

                                yield format_sse(
                                    {
                                        "type": "tool_call",
                                        "tool_name": tc_name,
                                        "tool_call_id": tc_id or f"tool-{tc_name}",
                                        "arguments": tc_args,
                                        "step": current_step,
                                    }
                                )

                if isinstance(token, ToolMessage):
                    tool_name = token.name or ""
                    result_content = str(token.content) if token.content else ""
                    tool_success = "Command failed with exit code" not in result_content and "failed with exit code" not in result_content
                    tool_call_id = token.tool_call_id or ""

                    if not tool_name:
                        continue

                    tool_start = tool_call_start_times.pop(tool_name, None)
                    tool_duration = time.time() - tool_start if tool_start else 0
                    total_tool_duration += tool_duration

                    tool_args = tool_call_accumulated_args.pop(tool_name, {})
                    # Debug: trace where args come from
                    if not tool_args or (isinstance(tool_args, dict) and len(tool_args) == 0):
                        logger.info(
                            f"[{sid}] ⚠️ tool_args empty for {tool_name}, "
                            f"accumulator keys: {list(tool_call_accumulated_args.keys())}, "
                            f"tc_id_map keys: {list(tool_call_id_map.keys())}"
                        )
                        for blk in reversed(blocks):
                            if blk["type"] == "tool_call" and blk["tool_name"] == tool_name:
                                block_args = blk.get("arguments", {})
                                if block_args:
                                    tool_args = block_args
                                break
                    result_len = len(result_content) if result_content else 0
                    result_preview = result_content[:200] if result_content else ""

                    if "raw" in tool_args and len(tool_args) == 1:
                        log_args = tool_args["raw"]
                    else:
                        log_args = json.dumps(tool_args, ensure_ascii=False)

                    logger.info(
                        f"[{sid}] Step {current_step} 工具调用: {tool_name} | 参数: {log_args[:2000]} | 耗时: {tool_duration:.2f}s | 内容长度: {result_len} | 调用结果: {'成功' if tool_success else '失败'} | 结果: {result_preview}"
                    )

                    for blk in reversed(blocks):
                        if blk["type"] == "tool_call" and blk["tool_name"] == tool_name:
                            blk["result"] = result_content
                            blk["success"] = tool_success
                            blk["duration"] = round(tool_duration, 2)
                            break

                    mapped_id = tool_call_id_map.pop(
                        tool_name, tool_call_id or f"tool-{tool_name}"
                    )
                    tool_call_records.append(
                        (
                            tool_name,
                            mapped_id,
                            tool_args,
                            result_content,
                            tool_success,
                            round(tool_duration, 2),
                            current_step,
                        )
                    )

                    yield format_sse(
                        {
                            "type": "tool_result",
                            "tool_name": tool_name,
                            "tool_call_id": mapped_id,
                            "arguments": tool_args,
                            "result": result_content,
                            "success": tool_success,
                            "duration": round(tool_duration, 2),
                            "step": current_step,
                        }
                    )
                    tu = build_token_usage_event()
                    if tu:
                        yield tu

                    try:
                        db.record_tool_call(
                            session_id=session_id,
                            message_id=message_id,
                            tool_name=tool_name,
                            tool_call_id=mapped_id,
                            arguments=tool_args,
                            result=str(result_content)[:5000],
                            success=tool_success,
                            duration=round(tool_duration, 2),
                            step=current_step,
                        )
                    except Exception as e:
                        logger.warning(f"[{sid}] 持久化工具调用记录失败: {e}")

                    if session_logger:
                        session_logger.log_tool_call(
                            tool_name=tool_name,
                            tool_call_id=mapped_id,
                            arguments=tool_args,
                            result=str(result_content)[:5000],
                            success=tool_success,
                            duration=round(tool_duration, 2),
                            step=current_step,
                            message_id=message_id,
                        )

                    if tool_success and tool_name in ("write_file", "write_tool"):
                        try:
                            file_args = tool_args if isinstance(tool_args, dict) else {}
                            filename = (
                                file_args.get("file_name")
                                or file_args.get("path")
                                or file_args.get("file_path", "")
                            )
                            if filename:
                                base_name = (
                                    os.path.basename(filename)
                                    if filename
                                    else "unknown"
                                )
                                ext = (
                                    os.path.splitext(base_name)[1].lstrip(".") or "txt"
                                )
                                db.add_generated_file(
                                    session_id=session_id,
                                    message_id=message_id,
                                    filename=base_name,
                                    file_path=str(agent.workspace_dir / filename)
                                    if not os.path.isabs(filename)
                                    else filename,
                                    file_type=ext,
                                    size=0,
                                )
                                logger.info(f"[{sid}] 📄 记录生成文件: {base_name}")
                        except Exception as e:
                            logger.warning(f"[{sid}] 记录生成文件失败: {e}")

                    persist_partial()

            final_thinking_duration = None
            if (
                accumulated_thinking.strip()
                and thinking_start_time
                and thinking_end_time
            ):
                final_thinking_duration = round(
                    thinking_end_time - thinking_start_time, 2
                )
                db.record_thinking(
                    session_id=session_id,
                    message_id=message_id,
                    step=current_step,
                    content=accumulated_thinking.strip(),
                    duration=final_thinking_duration,
                )
                logger.info(
                    f"[{sid}] 💾 思考记录已保存 | 长度: {len(accumulated_thinking)} | 耗时: {final_thinking_duration}s"
                )

            user_input_needed = False
            if accumulated_response:
                response_clean = accumulated_response.strip()
                question_patterns = [
                    r"[？?]\s*$",
                    r"方案[一二三123]\s*[）\)]?\s*$",
                    r"请选择",
                    r"是否确认",
                    r"确认要",
                    r"是否继续",
                    r"你希望",
                    r"你想[要]?",
                    r"输入\s*[1-3]",
                    r"选择\s*[1-3]",
                    r"\([Yy]\/[Nn]\)",
                    r"\([是否]\/[是否]\)",
                ]
                for pattern in question_patterns:
                    if re.search(pattern, response_clean, re.MULTILINE):
                        user_input_needed = True
                        break

                if user_input_needed:
                    logger.info(
                        f"[{sid}] 🤝 检测到需要用户输入 | 内容: {response_clean[:200]}"
                    )
                    yield format_sse(
                        {
                            "type": "user_input_required",
                            "content": response_clean,
                        }
                    )

            if accumulated_response:
                yield format_sse(
                    {
                        "type": "content_end",
                        "content": "",
                    }
                )
                content_end_time = time.time()

            elapsed_time = time.time() - start_time

            thinking_time = (
                round(thinking_end_time - thinking_start_time, 2)
                if thinking_start_time and thinking_end_time
                else 0
            )
            content_time = (
                round(content_end_time - content_start_time, 2)
                if content_start_time and content_end_time
                else 0
            )

            if thinking_started and thinking_start_time and thinking_end_time is None:
                thinking_duration = time.time() - thinking_start_time
                logger.info(
                    f"[{sid}] 🤔 Step {current_step} 思考结束(异常) | 耗时: {thinking_duration:.2f}s | 长度: {len(accumulated_thinking)}"
                )
                yield format_sse(
                    {
                        "type": "thinking_end",
                        "duration": round(thinking_duration, 2),
                        "step": current_step,
                    }
                )

            logger.info(
                f"[{sid}] ✅ 流式响应完成 | 总步骤: {current_step} | 总耗时: {elapsed_time:.2f}s | 思考: {thinking_time}s | 回复: {content_time}s | 工具: {total_tool_duration:.2f}s | 思考长度: {len(accumulated_thinking)} | 回复长度: {len(accumulated_response)} 字符"
            )
            if accumulated_thinking:
                logger.info(f"[{sid}] 🤔 思考内容(前200字):\n{accumulated_thinking[:200]}")
            if accumulated_response:
                logger.info(f"[{sid}] 💬 回复内容(前200字):\n{accumulated_response[:200]}")

            assistant_message = {
                "role": "assistant",
                "content": accumulated_response if accumulated_response else "",
                "timestamp": datetime.now().isoformat(),
                "thinking": accumulated_thinking if accumulated_thinking else None,
                "thinking_duration": final_thinking_duration,
                "tool_calls": [
                    {
                        "tool_name": tc[0],
                        "tool_call_id": tc[1],
                        "arguments": tc[2],
                        "result": tc[3],
                        "success": tc[4],
                        "duration": tc[5],
                        "step": tc[6],
                    }
                    for tc in tool_call_records
                ]
                if tool_call_records
                else None,
                "blocks": blocks if blocks else None,
            }
            db.update_last_assistant_message(session_id, assistant_message)
            db.update_last_assistant_message_row(session_id, assistant_message)

            try:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                sid_prefix = session_id[:5]
                context_filename = f"{ts}_{sid_prefix}.json"
                log_dir = Path("logs/sessions")
                log_dir.mkdir(parents=True, exist_ok=True)
                context_path = log_dir / context_filename

                session = db.get_session(session_id)
                all_messages = session.messages if session else []

                context_data = {
                    "session_id": session_id,
                    "message_id": message_id,
                    "timestamp": datetime.now().isoformat(),
                    "messages": all_messages,
                    "current_exchange": {
                        "user_message": message_content,
                        "assistant_response": {
                            "content": accumulated_response,
                            "thinking": accumulated_thinking
                            if accumulated_thinking
                            else None,
                            "thinking_duration": final_thinking_duration,
                        },
                        "tool_calls": [
                            {
                                "tool_name": tc[0],
                                "tool_call_id": tc[1],
                                "arguments": tc[2],
                                "result": str(tc[3])[:5000],
                                "success": tc[4],
                                "duration": tc[5],
                                "step": tc[6],
                            }
                            for tc in tool_call_records
                        ]
                        if tool_call_records
                        else [],
                        "blocks": blocks if blocks else [],
                        "steps": current_step,
                        "elapsed_time": round(elapsed_time, 2),
                    },
                }

                with open(context_path, "w", encoding="utf-8") as f:
                    json.dump(context_data, f, ensure_ascii=False, indent=2)

                logger.info(f"[{sid}] Context file saved: {context_filename}")
            except Exception as e:
                logger.warning(f"[{sid}] Failed to save context file: {e}")

            if tool_call_records and agent and not agent._workspace_renamed:
                has_file_writes = any(
                    tc[0] in ("write_file", "write_tool") and tc[4]
                    for tc in tool_call_records
                )
                if has_file_writes:
                    try:
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        sid_prefix = session_id[:5]
                        new_ws_name = f"{ts}_{sid_prefix}"

                        old_ws_path = str(agent.workspace_dir.absolute())
                        agent.rename_workspace(new_ws_name)
                        agent._workspace_renamed = True
                        new_ws_path = str(agent.workspace_dir.absolute())

                        db.update_generated_file_paths(
                            session_id, old_ws_path, new_ws_path
                        )
                        db.update_session_workspace_name(session_id, new_ws_name)

                        logger.info(
                            f"[{sid}] Workspace renamed: {old_ws_path} -> {new_ws_path}"
                        )
                    except Exception as e:
                        logger.warning(f"[{sid}] Workspace rename failed: {e}")

            if session_logger:
                tool_calls_for_log = (
                    [
                        {
                            "tool_name": tc[0],
                            "tool_call_id": tc[1],
                            "arguments": tc[2],
                            "result": str(tc[3])[:1000],
                            "success": tc[4],
                            "duration": tc[5],
                            "step": tc[6],
                        }
                        for tc in tool_call_records
                    ]
                    if tool_call_records
                    else None
                )
                session_logger.log_assistant_response(
                    content=accumulated_response,
                    thinking=accumulated_thinking if accumulated_thinking else None,
                    tool_calls=tool_calls_for_log,
                )

            logger.info(
                f"[{sid}] Token usage: input={total_usage['input_tokens']}, output={total_usage['output_tokens']}, total={total_usage['total_tokens']}"
            )

            if total_usage["total_tokens"] == 0 and (
                accumulated_response or accumulated_thinking
            ):
                total_usage["input_tokens"] = estimate_tokens(message_content)
                total_usage["output_tokens"] = estimate_tokens(
                    accumulated_response
                ) + estimate_tokens(accumulated_thinking)
                total_usage["total_tokens"] = (
                    total_usage["input_tokens"] + total_usage["output_tokens"]
                )
                logger.info(
                    f"[{sid}] 📊 Estimated tokens: input={total_usage['input_tokens']}, output={total_usage['output_tokens']}, total={total_usage['total_tokens']}"
                )

            usage_payload = {
                **total_usage,
                "max_input_tokens": max_input_tokens,
                "auto_compress_tokens": auto_compress_tokens,
            }
            logger.info(f"[{sid}] 📊 Sending done event with usage: {usage_payload}")
            yield format_sse(
                {
                    "type": "done",
                    "session_id": session_id,
                    "elapsed_time": round(elapsed_time, 2),
                    "usage": usage_payload,
                }
            )

        else:
            messages = [{"role": "user", "content": message_content}]

            logger.info(f"[{sid}] 💬 开始非流式回复")

            yield format_sse(
                {
                    "type": "assistant_start",
                    "content": "",
                }
            )

            start_time = time.time()
            response_content = await agent.run(message_content)
            elapsed_time = time.time() - start_time

            logger.info(
                f"[{sid}] ✅ 非流式响应完成 | 耗时: {elapsed_time:.2f}s | 回复长度: {len(response_content)} 字符"
            )

            yield format_sse(
                {
                    "type": "content",
                    "content": response_content,
                }
            )

            assistant_message = {
                "role": "assistant",
                "content": response_content,
                "timestamp": datetime.now().isoformat(),
            }
            db.add_message(session_id, assistant_message)
            db.add_message_row(session_id, assistant_message)

            if session_logger:
                session_logger.log_assistant_response(content=response_content)

            yield format_sse(
                {
                    "type": "done",
                    "session_id": session_id,
                    "elapsed_time": round(elapsed_time, 2),
                    "usage": {
                        **total_usage,
                        "max_input_tokens": max_input_tokens,
                        "auto_compress_tokens": auto_compress_tokens,
                    },
                }
            )

    except asyncio.CancelledError:
        logger.info(f"[{sid}] ❌ 请求被取消")
        try:
            partial_msg = {
                "role": "assistant",
                "content": accumulated_response if accumulated_response else "",
                "timestamp": datetime.now().isoformat(),
                "thinking": accumulated_thinking if accumulated_thinking else None,
                "thinking_duration": None,
                "tool_calls": [
                    {
                        "tool_name": tc[0],
                        "tool_call_id": tc[1],
                        "arguments": tc[2],
                        "result": str(tc[3])[:5000],
                        "success": tc[4],
                        "duration": tc[5],
                        "step": tc[6],
                    }
                    for tc in tool_call_records
                ]
                if tool_call_records
                else None,
                "blocks": blocks if blocks else None,
            }
            db.update_last_assistant_message(session_id, partial_msg)
            db.update_last_assistant_message_row(session_id, partial_msg)
            logger.info(
                f"[{sid}] 💾 取消时已保存部分回复 | 长度: {len(accumulated_response)}"
            )
        except Exception as e:
            logger.warning(f"[{sid}] 取消时保存失败: {e}")
        yield format_sse({"type": "error", "content": "请求被取消"})
    except Exception as e:
        logger.error(f"[{sid}] ❌ 聊天异常: {str(e)}", exc_info=True)
        yield format_sse({"type": "error", "content": f"处理失败: {str(e)}"})
