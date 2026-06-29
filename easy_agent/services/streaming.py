"""Chat streaming service - SSE streaming generator with thinking/tool support.

Uses agent.astream(stream_mode='messages') for raw message-level streaming,
which correctly captures DeepSeek's reasoning_content as per-token chunks.

Events emitted to frontend:
  start            {session_id}
  thinking_start   {step}
  thinking         {content, step}
  thinking_end     {duration, step}
  content          {content}
  content_end      {}
  tool_call        {tool_name, tool_call_id, arguments, step}
  tool_result      {tool_name, tool_call_id, arguments, result, success, duration, step}
  todo_list        {todos, step}
  token_usage      {input_tokens, output_tokens, total_tokens, max_input_tokens, auto_compress_tokens}
  done             {session_id, elapsed_time, usage}
  error            {content}
"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from ..agent import EasyAgent
from ..db import Database
from ..model import _parse_mcp_content, create_model
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


def _is_error_result(text: str) -> bool:
    """Check if a tool result string indicates an error.

    DeepAgents built-in tools return errors as plain string content (e.g.
    "Error: File not found", "Cannot write to ..."), not as ToolMessage
    metadata. This function detects those patterns.
    """
    if not text:
        return False
    stripped = text.strip()
    if stripped.startswith("Error:") or stripped.startswith("Error "):
        return True
    if stripped.startswith("Cannot write to"):
        return True
    if "[Command failed with exit code" in stripped:
        return True
    # Check for stderr errors in execute results even with exit code 0
    # e.g. "[stderr] /bin/sh: 1: cd: can't cd to ..."
    # e.g. "[stderr] mkdir: cannot create directory '/workspace': Permission denied"
    stderr_match = re.search(r"\[stderr\]\s*(.+)", text, re.DOTALL)
    if stderr_match:
        stderr_text = stderr_match.group(1).strip()
        # Common error patterns in stderr
        error_patterns = [
            "permission denied",
            "cannot create directory",
            "can't cd to",
            "cannot cd to",
            "no such file",
            "not found",
            "command not found",
            "is not a directory",
            "is not a file",
            "access denied",
            "operation not permitted",
            "read-only",
        ]
        stderr_lower = stderr_text.lower()
        for pattern in error_patterns:
            if pattern in stderr_lower:
                return True
    return False


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
        message_content = f"[workspace: {agent.workspace_virtual_path}/ | shell: cd {ws}]\n{message_content}"

    if session_id and db:
        message_content = await build_context_messages(
            db, session_id, message_content, session_logger
        )
    # capture pre-exchange cumulative session token estimate (used later in _token_event
    # so frontend can display session-level context consumption during streaming)
    pre_session_tokens = 0
    try:
        session_obj = db.get_session(session_id)
        if session_obj and session_obj.messages:
            # 优先从消息的 usage 字段累加（API 返回的准确值）
            for msg in session_obj.messages:
                msg_usage = msg.get("usage") or {}
                pre_session_tokens += msg_usage.get("total_tokens", 0)
            # 如果没有 usage 数据，用估算兜底
            if pre_session_tokens == 0:
                pre_session_tokens = estimate_tokens(str(session_obj.messages))
    except Exception:
        pass

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

        def _sse(data: dict) -> str:
            return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

        yield format_sse({"type": "start", "session_id": session_id})

        # ── 状态变量 ─────────────────────────────────────────────────
        blocks = []
        tool_call_records = []
        accumulated_thinking = ""
        accumulated_response = ""
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        step_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        last_context_tokens = (
            0  # 最后一次 API 调用的 input_tokens，即当前上下文窗口占用
        )

        current_step = 0
        is_in_thinking = False
        thinking_start_time = None
        thinking_end_time = None
        thinking_step_start_len = 0
        content_start_time = None
        content_step_start_len = 0
        content_block_start_len = 0  # 当前 content block 的起始位置（用于分段持久化）
        tool_call_start_times = {}
        tool_call_accumulated_args = {}
        tool_call_accumulated_args_str = {}  # tool_call_id → raw args string
        tool_call_id_to_name = {}  # tool_call_id → tool_name
        tool_call_index_to_id = {}  # chunk index → tool_call_id (for correlating subsequent chunks)
        pending_tool_args_by_index = {}  # chunk index → accumulated args string (before name/id arrive)
        last_persisted_len = 0
        last_token_usage_time = 0
        step_advanced_this_round = False  # 当前 round 是否已推进过 step

        max_input_tokens = None
        auto_compress_tokens = None
        result_log_truncate = 200
        _agent_config = get_agent_config()
        if _agent_config and _agent_config.get("config"):
            max_input_tokens = _agent_config["config"].llm.max_input_tokens
            result_log_truncate = getattr(
                _agent_config["config"].tools, "result_log_truncate", 200
            )
        if not max_input_tokens:
            model_instance = getattr(agent, "model", None)
            if (
                model_instance
                and hasattr(model_instance, "profile")
                and model_instance.profile
            ):
                max_input_tokens = model_instance.profile.get("max_input_tokens")
        if not max_input_tokens:
            model_instance = getattr(agent, "model", None)
            max_input_tokens = _get_model_context_limit(model_instance)
        if max_input_tokens:
            auto_compress_tokens = int(max_input_tokens * 0.85)
        else:
            auto_compress_tokens = 170000

        _debug_logged = False

        # ── 辅助函数 ─────────────────────────────────────────────────

        def _make_partial():
            return {
                "role": "assistant",
                "content": accumulated_response or "",
                "timestamp": datetime.now().isoformat(),
                "thinking": accumulated_thinking or None,
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
                or None,
                "blocks": blocks or None,
            }

        def _persist():
            try:
                msg = _make_partial()
                db.update_last_assistant_message(session_id, msg)
                db.update_last_assistant_message_row(session_id, msg)
            except Exception as e:
                logger.warning(f"[{sid}] 增量持久化失败: {e}")

        def _persist_todos(todos_list):
            """Persist todo list to database, replacing any existing plan."""
            try:
                db.update_session_todos(session_id, todos_list)
                logger.info(
                    f"[{sid}] 📋 Todo list persisted | items: {len(todos_list)}"
                )
            except Exception as e:
                logger.warning(f"[{sid}] 持久化 Todo list 失败: {e}")

        def _token_event():
            nonlocal last_token_usage_time
            now = time.time()
            if now - last_token_usage_time < 0.5:
                return None
            last_token_usage_time = now
            inp = total_usage["input_tokens"]
            if not inp:
                # API 未返回 usage_metadata，从完整上下文估算
                context_text = "".join(
                    str(getattr(msg, "content", "")) for msg in context_messages
                )
                inp = (
                    estimate_tokens(context_text)
                    + estimate_tokens(accumulated_response)
                    + estimate_tokens(accumulated_thinking)
                )
                for blk in blocks:
                    if blk.get("type") == "tool_result":
                        inp += estimate_tokens(str(blk.get("result", "")))
                    elif blk.get("type") == "tool_call":
                        inp += estimate_tokens(str(blk.get("tool_args", "")))
            out = total_usage["output_tokens"] or (
                estimate_tokens(accumulated_response)
                + estimate_tokens(accumulated_thinking)
            )
            cumulative = pre_session_tokens + inp + out
            # context_tokens: 当前上下文窗口占用（最后一次 API 调用的 input_tokens）
            ctx_tokens = last_context_tokens if last_context_tokens > 0 else inp
            return _sse(
                {
                    "type": "token_usage",
                    "input_tokens": inp,
                    "output_tokens": out,
                    "total_tokens": inp + out,
                    "session_estimate": cumulative,
                    "context_tokens": ctx_tokens,
                    "max_input_tokens": max_input_tokens,
                    "auto_compress_tokens": auto_compress_tokens,
                    "elapsed_time": round(time.time() - start_time, 2),
                    "step_count": current_step,
                }
            )

        def _start_thinking(step: int):
            nonlocal \
                is_in_thinking, \
                thinking_start_time, \
                current_step, \
                thinking_step_start_len
            is_in_thinking = True
            thinking_start_time = time.time()
            thinking_step_start_len = len(accumulated_thinking)
            current_step = step
            blocks.append(
                {
                    "type": "thinking",
                    "content": "",
                    "order": len(blocks),
                    "step": step,
                }
            )
            return _sse({"type": "thinking_start", "content": "", "step": step})

        def _end_thinking(step: int):
            nonlocal is_in_thinking, thinking_end_time, thinking_step_start_len
            thinking_end_time = time.time()
            duration = round(
                (thinking_end_time - thinking_start_time) if thinking_start_time else 0,
                2,
            )
            step_thinking = accumulated_thinking[thinking_step_start_len:].strip()
            if step_thinking:
                try:
                    db.record_thinking(
                        session_id=session_id,
                        message_id=message_id,
                        step=step,
                        content=step_thinking[:10000],
                        duration=duration,
                    )
                except Exception as e:
                    logger.warning(f"[{sid}] 持久化思考记录失败: {e}")
                if session_logger:
                    session_logger.log_thinking(
                        content=step_thinking,
                        step=step,
                        duration=duration,
                        message_id=message_id,
                    )
                logger.info(
                    f"[{sid}] 🤔 Step {step} 思考完成 | 耗时: {duration}s | 长度: {len(step_thinking)} | 内容: {step_thinking}"
                )
            else:
                logger.info(
                    f"[{sid}] 🤔 Step {step} 思考完成（空） | 耗时: {duration}s"
                )
            for blk in reversed(blocks):
                if blk["type"] == "thinking" and blk.get("step") == step:
                    blk["content"] = step_thinking
                    blk["duration"] = duration
                    break
            is_in_thinking = False
            return _sse({"type": "thinking_end", "duration": duration, "step": step})

        def _end_thinking_if_needed():
            nonlocal is_in_thinking
            if not is_in_thinking:
                return None
            return _end_thinking(current_step)

        def _seal_content_block():
            """将当前累积的正文内容（自上次封存以来的部分）作为一个 content block 添加到 blocks 中。
            在切换到 thinking/tool_call 或流结束时调用，确保中间正文内容被分段持久化。"""
            nonlocal content_block_start_len
            segment = accumulated_response[content_block_start_len:]
            if segment.strip():
                blocks.append(
                    {
                        "type": "content",
                        "content": segment,
                        "order": len(blocks),
                    }
                )
            content_block_start_len = len(accumulated_response)

        def _finalize_step(step: int):
            """打印上一个 step 的正文汇总 + token 用量信息，并发送 token 事件给前端。"""
            nonlocal content_step_start_len
            step_content = accumulated_response[content_step_start_len:].strip()
            if step_content:
                logger.info(
                    f"[{sid}] 💬 Step {step} 正文 | 长度: {len(step_content)} | 内容: {step_content}"
                )
            content_step_start_len = len(accumulated_response)

            # 打印 token 用量
            # 优先使用 API 返回的 usage_metadata，否则从 context_messages 估算
            inp = step_usage["input_tokens"]
            if not inp:
                # API 未返回 usage_metadata，从完整上下文估算输入 token
                context_text = "".join(
                    str(getattr(msg, "content", "")) for msg in context_messages
                )
                inp = (
                    estimate_tokens(context_text)
                    + estimate_tokens(accumulated_response)
                    + estimate_tokens(accumulated_thinking)
                )
                # 加上工具调用/结果的估算
                for blk in blocks:
                    if blk.get("type") == "tool_result":
                        inp += estimate_tokens(str(blk.get("result", "")))
                    elif blk.get("type") == "tool_call":
                        inp += estimate_tokens(str(blk.get("tool_args", "")))
            out = step_usage["output_tokens"] or estimate_tokens(
                accumulated_thinking
            ) + estimate_tokens(step_content)
            step_total = step_usage["total_tokens"] or inp + out
            # context_tokens: 最后一次 API 调用的 input_tokens
            ctx_tokens = last_context_tokens if last_context_tokens > 0 else inp
            ctx_max = max_input_tokens or 0
            ctx_ratio = (ctx_tokens / ctx_max * 100) if ctx_max else 0
            ctx_bar = ""
            if ctx_max:
                filled = int(ctx_ratio / 5)  # 20 格
                ctx_bar = (
                    " [" + "█" * filled + "░" * (20 - filled) + f"] {ctx_ratio:.1f}%"
                )
            is_estimated = step_usage["input_tokens"] == 0
            logger.info(
                f"[{sid}] 📊 Step {step} Token{'(估算)' if is_estimated else ''} | "
                f"上下文占用: {ctx_tokens}/{ctx_max or '?'}{ctx_bar} | "
                f"Step输入: {inp} | Step输出: {out} | "
                f"Step合计: {step_total} | "
                f"累计输入: {total_usage['input_tokens']} | "
                f"累计输出: {total_usage['output_tokens']} | "
                f"累计 API Token: {total_usage['total_tokens']}"
            )

            # 每步完成时发送 token 用量事件给前端
            cumulative = (
                pre_session_tokens
                + total_usage["input_tokens"]
                + total_usage["output_tokens"]
            )
            token_sse = _sse(
                {
                    "type": "token_usage",
                    "input_tokens": total_usage["input_tokens"],
                    "output_tokens": total_usage["output_tokens"],
                    "total_tokens": total_usage["input_tokens"]
                    + total_usage["output_tokens"],
                    "session_estimate": cumulative,
                    "context_tokens": ctx_tokens,
                    "max_input_tokens": max_input_tokens,
                    "auto_compress_tokens": auto_compress_tokens,
                    "elapsed_time": round(time.time() - start_time, 2),
                    "step_count": current_step,
                }
            )

            step_usage["input_tokens"] = 0
            step_usage["output_tokens"] = 0
            step_usage["total_tokens"] = 0

            return token_sse

        def _log_step_start(step: int, step_type: str, detail: str = ""):
            """新 step 开始时立即打印日志。"""
            msg = f"[{sid}] 🚀 Step {step} 开始 | 类型: {step_type}"
            if detail:
                msg += f" | {detail}"
            logger.info(msg)

        # ── 构建上下文消息 ─────────────────────────────────────────────
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
                                    additional_kwargs={"reasoning_content": thinking},
                                )
                            )
                        else:
                            # 非 reasoning 模型：将历史思考用 <think> 标签包裹嵌入内容
                            # 避免模型学到用 "[思考]:" 文本标记，从而导致前端显示异常
                            assistant_content = (
                                f"<think>{thinking}</think>\n\n{assistant_content}"
                            )
                            context_messages.append(
                                AIMessage(content=assistant_content)
                            )
                    else:
                        context_messages.append(AIMessage(content=assistant_content))

        context_messages.append(HumanMessage(content=message_content))

        # ── 启动流式输出 (stream_mode='messages') ────────────────────────
        logger.info(f"[{sid}] 🚀 使用 stream_mode='messages' 流式接口")
        logger.info(f"[{sid}] 📨 上下文消息数: {len(context_messages)}")
        for i, msg in enumerate(context_messages):
            role = getattr(msg, "type", type(msg).__name__)
            content_full = str(getattr(msg, "content", ""))
            tool_calls = getattr(msg, "tool_calls", None)
            tc_info = f" | tool_calls={len(tool_calls)}" if tool_calls else ""
            logger.info(
                f"[{sid}] 📨 msg[{i}]: role={role}{tc_info} | len={len(content_full)}"
            )

        emitted_tool_call_ids = set()
        _todo_emitted_for = set()

        async for event in agent.agent.astream(
            {"messages": context_messages},
            stream_mode="messages",
        ):
            chunk, metadata = event
            node = metadata.get("langgraph_node", "?")

            # ── AIMessageChunk: reasoning / text / tool_call_chunks ──────
            if isinstance(chunk, AIMessageChunk):
                rc = (
                    chunk.additional_kwargs.get("reasoning_content", "")
                    if hasattr(chunk, "additional_kwargs")
                    else ""
                )
                raw_content = chunk.content or ""
                tcc = getattr(chunk, "tool_call_chunks", None) or []

                # Debug: log raw chunk structure for Anthropic protocol diagnosis
                if not _debug_logged:
                    _debug_logged = True
                    raw_content_repr = (
                        repr(chunk.content)[:300] if chunk.content else "''"
                    )
                    raw_rc_repr = repr(
                        chunk.additional_kwargs.get("reasoning_content", "")
                    )[:200]
                    raw_tcc_repr = repr(tcc)[:200] if tcc else "[]"
                    logger.info(
                        f"[{sid}] 🔍 AIMessageChunk debug | "
                        f"content_type={type(chunk.content).__name__} content={raw_content_repr} | "
                        f"rc={raw_rc_repr} | "
                        f"tcc={raw_tcc_repr} | "
                        f"node={node}"
                    )

                # ── Extract text and thinking from content ───────────────
                # Anthropic protocol (ChatAnthropic) sends content as a list of
                # content blocks when tools or thinking are enabled:
                #   text:    [{"type": "text", "text": "Hello", "index": 0}]
                #   thinking: [{"type": "thinking", "thinking": "...", "index": 0}]
                #   tool_use: [{"type": "tool_use", ...}]
                # OpenAI protocol sends content as a plain string.
                content = ""
                anthropic_thinking = ""

                if isinstance(raw_content, list):
                    for block in raw_content:
                        if not isinstance(block, dict):
                            content += str(block)
                            continue
                        block_type = block.get("type", "")
                        if block_type == "thinking":
                            # Anthropic thinking block: {"type": "thinking", "thinking": "..."}
                            anthropic_thinking += block.get("thinking", "")
                        elif block_type == "text":
                            # Anthropic text block: {"type": "text", "text": "..."}
                            content += block.get("text", "")
                        # Skip tool_use, input_json_delta, compaction, etc.
                elif isinstance(raw_content, str):
                    content = raw_content

                # DeepSeek reasoning_content (from additional_kwargs or list)
                if isinstance(rc, list):
                    rc = "".join(
                        c.get("text", "") if isinstance(c, dict) else str(c) for c in rc
                    )

                # === Usage metadata: 必须在 step 推进之前处理 ===
                # OpenAI 兼容 API 的 usage_metadata 在最后一个 AIMessageChunk 中返回，
                # 可能在 tool_call_chunks 之后、下一个 step 开始之前到达
                if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                    usage_meta = chunk.usage_metadata
                    total_usage["input_tokens"] += usage_meta.get("input_tokens", 0)
                    total_usage["output_tokens"] += usage_meta.get("output_tokens", 0)
                    total_usage["total_tokens"] += usage_meta.get("total_tokens", 0)
                    step_usage["input_tokens"] += usage_meta.get("input_tokens", 0)
                    step_usage["output_tokens"] += usage_meta.get("output_tokens", 0)
                    step_usage["total_tokens"] += usage_meta.get("total_tokens", 0)
                    # 记录最后一次 API 调用的 input_tokens 作为上下文窗口占用
                    if usage_meta.get("input_tokens", 0) > 0:
                        last_context_tokens = usage_meta["input_tokens"]

                # Merge Anthropic thinking into rc for unified handling
                if anthropic_thinking:
                    rc = rc + anthropic_thinking if rc else anthropic_thinking

                # === Reasoning (逐 token) ===
                if rc:
                    # 切换到思考前，封存之前的正文段落
                    _seal_content_block()
                    if not is_in_thinking:
                        token_sse = _finalize_step(current_step)
                        if token_sse:
                            yield token_sse
                        current_step += 1
                        step_advanced_this_round = True
                        _log_step_start(current_step, "思考")
                        yield _start_thinking(current_step)
                    accumulated_thinking += rc
                    yield _sse(
                        {"type": "thinking", "content": rc, "step": current_step}
                    )

                # === Text content (逐 token) ===
                if content and not tcc:
                    # 非 reasoning 模型：首次内容或工具结果后推进 step
                    if not is_in_thinking and not step_advanced_this_round:
                        token_sse = _finalize_step(current_step)
                        if token_sse:
                            yield token_sse
                        current_step += 1
                        step_advanced_this_round = True
                        _log_step_start(current_step, "正文")
                    te = _end_thinking_if_needed()
                    if te:
                        yield te
                    if content_start_time is None:
                        content_start_time = time.time()
                    accumulated_response += content
                    yield _sse({"type": "content", "content": content})
                    tu = _token_event()
                    if tu:
                        yield tu
                    if len(accumulated_response) - last_persisted_len >= 500:
                        last_persisted_len = len(accumulated_response)
                        _persist()

                # === Tool call chunks ===
                if tcc:
                    # 切换到工具调用前，封存之前的正文段落
                    _seal_content_block()
                    if not step_advanced_this_round:
                        token_sse = _finalize_step(current_step)
                        if token_sse:
                            yield token_sse
                        current_step += 1
                        step_advanced_this_round = True
                        tool_names = [
                            tc.get("name", "") for tc in tcc if tc.get("name")
                        ]
                        _log_step_start(
                            current_step, "工具调用", f"tools: {tool_names}"
                        )
                for tc in tcc:
                    name = tc.get("name", "") or ""
                    args_str = str(tc.get("args", "") or "")
                    tid = tc.get("id", "") or ""
                    tc_index = tc.get("index")

                    te = _end_thinking_if_needed()
                    if te:
                        yield te

                    if name and tid:
                        tool_name = name
                        tool_call_id_to_name[tid] = tool_name
                        tool_call_start_times[tid] = time.time()

                        if tc_index is not None:
                            tool_call_index_to_id[tc_index] = tid

                        pending_prefix = ""
                        if (
                            tc_index is not None
                            and tc_index in pending_tool_args_by_index
                        ):
                            pending_prefix = pending_tool_args_by_index.pop(tc_index)

                        tool_call_accumulated_args_str[tid] = pending_prefix + args_str

                        args_data = {}
                        full_args = tool_call_accumulated_args_str[tid]
                        if full_args:
                            try:
                                parsed = json.loads(full_args)
                                args_data = (
                                    parsed
                                    if isinstance(parsed, dict)
                                    else {"value": parsed}
                                )
                            except json.JSONDecodeError:
                                args_data = {}

                        tool_call_accumulated_args[tid] = args_data
                        emitted_tool_call_ids.add(tid)

                        blocks.append(
                            {
                                "type": "tool_call",
                                "tool_name": tool_name,
                                "tool_call_id": tid,
                                "arguments": args_data,
                                "result": "",
                                "success": True,
                                "order": len(blocks),
                                "step": current_step,
                            }
                        )

                        yield _sse(
                            {
                                "type": "tool_call",
                                "tool_name": tool_name,
                                "tool_call_id": tid,
                                "arguments": args_data,
                                "step": current_step,
                            }
                        )

                        # ── write_todos: emit todo_list SSE event ─────────
                        if tool_name == "write_todos" and isinstance(args_data, dict):
                            todos = args_data.get("todos", [])
                            if todos:
                                logger.info(
                                    f"[{sid}] 📋 Todo list | items: {len(todos)}"
                                )
                                _persist_todos(todos)
                                _todo_emitted_for.add(tid)
                                yield _sse(
                                    {
                                        "type": "todo_list",
                                        "todos": todos,
                                        "step": current_step,
                                    }
                                )
                    elif tid or tc_index is not None:
                        resolved_tid = tid
                        if not resolved_tid and tc_index is not None:
                            resolved_tid = tool_call_index_to_id.get(tc_index, "")

                        if resolved_tid and resolved_tid in tool_call_id_to_name:
                            tool_call_accumulated_args_str[resolved_tid] = (
                                tool_call_accumulated_args_str.get(resolved_tid, "")
                                + args_str
                            )

                            full_args_str = tool_call_accumulated_args_str[resolved_tid]
                            try:
                                parsed = json.loads(full_args_str)
                                args_data = (
                                    parsed
                                    if isinstance(parsed, dict)
                                    else {"value": parsed}
                                )
                                tool_call_accumulated_args[resolved_tid] = args_data

                                for blk in reversed(blocks):
                                    if (
                                        blk["type"] == "tool_call"
                                        and blk.get("tool_call_id") == resolved_tid
                                    ):
                                        blk["arguments"] = args_data
                                        break

                                tool_name = tool_call_id_to_name[resolved_tid]
                                if tool_name == "write_todos":
                                    todos = (
                                        args_data.get("todos", [])
                                        if isinstance(args_data, dict)
                                        else []
                                    )
                                    if todos and resolved_tid not in _todo_emitted_for:
                                        logger.info(
                                            f"[{sid}] 📋 Todo list | items: {len(todos)}"
                                        )
                                        _persist_todos(todos)
                                        _todo_emitted_for.add(resolved_tid)
                                        yield _sse(
                                            {
                                                "type": "todo_list",
                                                "todos": todos,
                                                "step": current_step,
                                            }
                                        )
                            except json.JSONDecodeError:
                                pass
                        elif tc_index is not None:
                            pending_tool_args_by_index[tc_index] = (
                                pending_tool_args_by_index.get(tc_index, "") + args_str
                            )

            # ── ToolMessage: tool execution result ──────────────────────
            elif isinstance(chunk, ToolMessage):
                te = _end_thinking_if_needed()
                if te:
                    yield te

                tool_call_id = chunk.tool_call_id
                tool_name = tool_call_id_to_name.get(tool_call_id, "")
                if not tool_name:
                    tool_name = getattr(chunk, "name", "") or ""

                resolved_tid = tool_call_id
                if tool_call_id not in tool_call_id_to_name:
                    for blk in reversed(blocks):
                        if blk["type"] == "tool_call" and blk.get("result", "") == "":
                            if blk.get("tool_name") == tool_name or not tool_name:
                                resolved_tid = blk.get("tool_call_id", tool_call_id)
                                tool_name = blk.get("tool_name", tool_name)
                                tool_call_id_to_name[tool_call_id] = tool_name
                                if tool_call_id in tool_call_accumulated_args:
                                    tool_call_accumulated_args[resolved_tid] = (
                                        tool_call_accumulated_args.pop(tool_call_id)
                                    )
                                if tool_call_id in tool_call_start_times:
                                    tool_call_start_times[resolved_tid] = (
                                        tool_call_start_times.pop(tool_call_id)
                                    )
                                break

                tool_args = tool_call_accumulated_args.get(resolved_tid, {})
                result_content = (
                    _parse_mcp_content(chunk.content) if chunk.content else ""
                )

                # Determine success: check both chunk metadata AND result content
                # DeepAgents built-in tools (read_file, write_file, execute, etc.)
                # return errors as plain string content (e.g. "Error: File not found"),
                # not as ToolMessages with is_error metadata.
                chunk_error = getattr(chunk, "additional_kwargs", {}).get(
                    "is_error", False
                )
                content_error = _is_error_result(result_content)
                success = not chunk_error and not content_error
                tool_start = tool_call_start_times.pop(resolved_tid, None)
                tool_duration = round(time.time() - tool_start, 2) if tool_start else 0

                for blk in reversed(blocks):
                    if (
                        blk["type"] == "tool_call"
                        and blk.get("tool_call_id") == resolved_tid
                    ):
                        blk["result"] = result_content
                        blk["success"] = success
                        blk["duration"] = tool_duration
                        break

                tool_call_records.append(
                    [
                        tool_name,
                        tool_call_id,
                        tool_args,
                        result_content,
                        success,
                        tool_duration,
                        current_step,
                    ]
                )

                # 日志截断长结果
                log_result = result_content
                if len(log_result) > result_log_truncate:
                    log_result = log_result[:result_log_truncate] + "..."
                # 截断工具参数值（最大100字符）
                _arg_value_truncate = 100
                log_args = {}
                if tool_args and isinstance(tool_args, dict):
                    for k, v in tool_args.items():
                        sv = str(v)
                        log_args[k] = (
                            sv[:_arg_value_truncate] + "..."
                            if len(sv) > _arg_value_truncate
                            else v
                        )
                logger.info(
                    f"[{sid}] {'✅' if success else '❌'} {tool_name} | "
                    f"参数: {json.dumps(log_args, ensure_ascii=False) if log_args else {}} | "
                    f"结果: {log_result} | 耗时: {tool_duration}s"
                )

                # 工具结果处理完毕，重置 step 推进标记
                # 下一个 AIMessageChunk 将开启一个新的 step
                step_advanced_this_round = False

                yield _sse(
                    {
                        "type": "tool_result",
                        "tool_name": tool_name,
                        "tool_call_id": resolved_tid,
                        "arguments": tool_args,
                        "result": result_content,
                        "success": success,
                        "duration": tool_duration,
                        "step": current_step,
                    }
                )

                # ── write_todos: extract todo list from tool result ───
                if tool_name == "write_todos" and resolved_tid not in _todo_emitted_for:
                    # Try to extract todos from arguments first
                    todos = (
                        tool_args.get("todos", [])
                        if isinstance(tool_args, dict)
                        else []
                    )
                    # If args don't have todos, try parsing from result text
                    if not todos and result_content:
                        todo_match = re.search(r"\[.*?\]", result_content, re.DOTALL)
                        if todo_match:
                            try:
                                parsed_result = json.loads(todo_match.group(0))
                                if isinstance(parsed_result, list):
                                    todos = parsed_result
                            except (json.JSONDecodeError, ValueError):
                                pass
                    if todos:
                        logger.info(f"[{sid}] 📋 Todo list | items: {len(todos)}")
                        _persist_todos(todos)
                        _todo_emitted_for.add(resolved_tid)
                        yield _sse(
                            {
                                "type": "todo_list",
                                "todos": todos,
                                "step": current_step,
                            }
                        )

                # Persist tool call
                try:
                    db.record_tool_call(
                        session_id=session_id,
                        message_id=message_id,
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        arguments=tool_args,
                        result=str(result_content)[:5000],
                        success=success,
                        duration=tool_duration,
                        step=current_step,
                    )
                except Exception as e:
                    logger.warning(f"[{sid}] 持久化工具调用记录失败: {e}")

                if session_logger:
                    session_logger.log_tool_call(
                        tool_name=tool_name,
                        tool_call_id=tool_call_id,
                        arguments=tool_args,
                        result=str(result_content)[:5000],
                        success=success,
                        duration=tool_duration,
                        step=current_step,
                        message_id=message_id,
                    )

                # Record generated file if applicable
                if tool_name in ("write_file", "write_tool"):
                    try:
                        file_args = tool_args if isinstance(tool_args, dict) else {}
                        filename = (
                            file_args.get("file_name")
                            or file_args.get("path")
                            or file_args.get("file_path", "")
                        )
                        if filename:
                            base_name = (
                                os.path.basename(filename) if filename else "unknown"
                            )
                            ext = os.path.splitext(base_name)[1].lstrip(".") or "txt"
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

                tu = _token_event()
                if tu:
                    yield tu
                _persist()

            # ── AIMessage (final assembled message with usage) ──────────
            elif isinstance(chunk, AIMessage) and not isinstance(chunk, AIMessageChunk):
                if chunk.usage_metadata:
                    usage_meta = chunk.usage_metadata
                    logger.info(f"[{sid}] 📊 usage_metadata: {usage_meta}")
                    total_usage["input_tokens"] += usage_meta.get("input_tokens", 0)
                    total_usage["output_tokens"] += usage_meta.get("output_tokens", 0)
                    total_usage["total_tokens"] += usage_meta.get("total_tokens", 0)
                    step_usage["input_tokens"] += usage_meta.get("input_tokens", 0)
                    step_usage["output_tokens"] += usage_meta.get("output_tokens", 0)
                    step_usage["total_tokens"] += usage_meta.get("total_tokens", 0)
                    if usage_meta.get("input_tokens", 0) > 0:
                        last_context_tokens = usage_meta["input_tokens"]
                    tu = _token_event()
                    if tu:
                        yield tu

        # ── Post-streaming: 流结束 ──────────────────────────────────────
        logger.info(f"[{sid}] 📢 流式输出结束")

        token_sse = _finalize_step(current_step)
        if token_sse:
            yield token_sse

        # 确保 thinking 结束
        te = _end_thinking_if_needed()
        if te:
            yield te

        # 将最后的正文内容段封存为 content block
        _seal_content_block()

        # 发送 content_end
        if accumulated_response:
            yield _sse({"type": "content_end", "content": ""})

        elapsed_time = time.time() - start_time

        # 统计
        thinking_time = 0
        if thinking_start_time and thinking_end_time:
            thinking_time = round(thinking_end_time - thinking_start_time, 2)
        content_time = 0
        if content_start_time:
            content_time = round(time.time() - content_start_time, 2)
        total_tool_duration = (
            sum(tc[5] for tc in tool_call_records) if tool_call_records else 0
        )

        logger.info(
            f"[{sid}] ✅ 流式响应完成 | 总步骤: {current_step} | "
            f"总耗时: {elapsed_time:.2f}s | 思考: {thinking_time}s | "
            f"回复: {content_time}s | 工具: {total_tool_duration:.2f}s | "
            f"思考长度: {len(accumulated_thinking)} | "
            f"回复长度: {len(accumulated_response)} 字符"
        )

        # ── Token 统计 ────────────────────────────────────────────────
        # 本轮对话（当前 exchange）token
        inp_tokens = total_usage["input_tokens"]
        out_tokens = total_usage["output_tokens"]
        sum_tokens = total_usage["total_tokens"]

        if sum_tokens == 0 and (accumulated_response or accumulated_thinking):
            inp_tokens = estimate_tokens(message_content)
            out_tokens = estimate_tokens(accumulated_response) + estimate_tokens(
                accumulated_thinking
            )
            sum_tokens = inp_tokens + out_tokens
            total_usage["input_tokens"] = inp_tokens
            total_usage["output_tokens"] = out_tokens
            total_usage["total_tokens"] = sum_tokens

        # 整个会话的累计占用（优先使用消息中持久化的 usage 累加）
        session_total_tokens = 0
        session_msg_count = 0
        try:
            session = db.get_session(session_id)
            if session and session.messages:
                session_msg_count = len(session.messages)
                # 优先从消息的 usage 字段累加（API 返回的准确值）
                for msg in session.messages:
                    msg_usage = msg.get("usage") or {}
                    session_total_tokens += msg_usage.get("total_tokens", 0)
                # 如果没有 usage 数据，用估算兜底
                if session_total_tokens == 0:
                    session_total_tokens = estimate_tokens(str(session.messages))
        except Exception:
            pass

        ctx_max = max_input_tokens or 0
        ctx_max_str = f"{ctx_max:,}" if ctx_max else "?"
        ratio = (
            ((session_total_tokens or sum_tokens) / ctx_max * 100) if ctx_max > 0 else 0
        )
        filled = min(20, int(ratio / 5))
        bar = " [" + "█" * filled + "░" * (20 - filled) + f"] {ratio:.1f}%"
        logger.info(
            f"[{sid}] 📊 Token | "
            f"本轮: ↑{inp_tokens} ↓{out_tokens} Σ{sum_tokens} | "
            f"会话累计: Σ{max(session_total_tokens, sum_tokens)} / {ctx_max_str}{bar} | "
            f"消息数: {max(session_msg_count, 1)}"
        )
        if accumulated_thinking:
            logger.info(f"[{sid}] 🤔 思考内容(前200字):\n{accumulated_thinking[:200]}")
        if accumulated_response:
            logger.info(f"[{sid}] 💬 回复内容(前200字):\n{accumulated_response[:200]}")

        # 持久化最终消息
        assistant_message = {
            "role": "assistant",
            "content": accumulated_response or "",
            "timestamp": datetime.now().isoformat(),
            "thinking": accumulated_thinking or None,
            "thinking_duration": thinking_time or None,
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
            or None,
            "blocks": blocks or None,
            "usage": {
                "input_tokens": inp_tokens,
                "output_tokens": out_tokens,
                "total_tokens": sum_tokens,
                "elapsed_time": round(elapsed_time, 2),
                "step_count": current_step,
            },
        }
        db.update_last_assistant_message(session_id, assistant_message)
        db.update_last_assistant_message_row(session_id, assistant_message)

        # 保存 context 文件
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
                        "thinking": accumulated_thinking or None,
                        "thinking_duration": thinking_time,
                    },
                    "tool_calls": [
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
                    ],
                },
            }
            with open(context_path, "w", encoding="utf-8") as f:
                json.dump(context_data, f, ensure_ascii=False, indent=2)
            logger.info(f"[{sid}] Context file saved: {context_filename}")
        except Exception as e:
            logger.warning(f"[{sid}] Failed to save context file: {e}")

        # Workspace rename
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
                    db.update_generated_file_paths(session_id, old_ws_path, new_ws_path)
                    db.update_session_workspace_name(session_id, new_ws_name)
                    logger.info(
                        f"[{sid}] Workspace renamed: {old_ws_path} -> {new_ws_path}"
                    )
                except Exception as e:
                    logger.warning(f"[{sid}] Workspace rename failed: {e}")

        # Memory file management — 每次会话后按使用场景更新用户记忆
        # 1. 根据本次会话内容按场景更新记忆（重复场景更新经验，新场景添加章节）
        # 2. 确保记忆文件不超过 3000 字符
        if agent and agent.memory_file:
            try:
                from .memory_manager import update_memory_after_session
                from .agent_manager import _llm_instance

                updated = update_memory_after_session(
                    agent.memory_file,
                    user_message=message_content,
                    assistant_response=accumulated_response,
                    llm=_llm_instance,
                )
                if updated:
                    logger.info(
                        f"[{sid}] 记忆文件已按场景更新（上限 3000 字符）"
                    )
            except Exception as e:
                logger.warning(f"[{sid}] 记忆文件更新失败: {e}")

        # Session logger
        if session_logger:
            tool_calls_for_log = [
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
            ] or None
            session_logger.log_assistant_response(
                content=accumulated_response,
                thinking=accumulated_thinking or None,
                tool_calls=tool_calls_for_log,
            )

        # Token 统计 — 已在上文完成 fallback 估算，此处直接使用

        usage_payload = {
            **total_usage,
            "max_input_tokens": max_input_tokens,
            "auto_compress_tokens": auto_compress_tokens,
            "session_estimate": session_total_tokens,
            "context_tokens": last_context_tokens
            if last_context_tokens > 0
            else inp_tokens,
            "step_count": current_step,
        }
        yield _sse(
            {
                "type": "done",
                "session_id": session_id,
                "elapsed_time": round(elapsed_time, 2),
                "usage": usage_payload,
            }
        )

    except asyncio.CancelledError:
        logger.info(
            f"[{sid}] ❌ 请求被取消 | 当前 step={current_step} | "
            f"已累积回复 {len(accumulated_response)} 字符 | "
            f"已记录 {len(tool_call_records)} 个工具调用 | "
            f"思考中={is_in_thinking}"
        )
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
                "usage": {
                    "input_tokens": total_usage.get("input_tokens", 0),
                    "output_tokens": total_usage.get("output_tokens", 0),
                    "total_tokens": total_usage.get("total_tokens", 0),
                    "elapsed_time": round(time.time() - start_time, 2),
                    "step_count": current_step,
                },
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
        if is_in_thinking:
            yield _end_thinking(current_step)
        for blk in reversed(blocks):
            if blk.get("type") == "tool_call" and blk.get("duration") is None:
                blk["success"] = False
                blk["result"] = f"执行中断: {str(e)[:result_log_truncate]}"
                blk["duration"] = 0
                break
        yield format_sse({"type": "error", "content": f"处理失败: {str(e)}"})
