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
  approval_required {thread_id, action_requests, allowed_decisions}
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
from langgraph.types import Command

from ..agent import EasyAgent
from ..db import Database
from ..model import create_model, extract_reasoning
from ..services.mcp import _parse_mcp_content
from ..models.api import ChatRequest
from ..utils.session_logger import SessionLogger
from .agent_manager import get_agent_config

logger = logging.getLogger("easy_agent.chat_service")

MAX_CONTEXT_MESSAGES = 30
KEEP_RECENT_MESSAGES = 10


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


def _extract_file_paths_from_command(command: str) -> list[str]:
    """从删除命令中提取文件路径列表。

    支持复合命令（如 `ls ... && touch ... && rm ...`），
    只提取 rm/rmdir/unlink/shred 部分的文件路径。
    """
    if not command:
        return []
    # 按复合命令分隔符拆分
    parts = re.split(r'\s*(?:&&|\|\||;|\|)\s*', command)
    paths = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # 只处理删除类命令
        rm_match = re.match(r'^(rm|rmdir|unlink|shred)\s+', part)
        if not rm_match:
            continue
        # 移除命令名
        cleaned = part[rm_match.end():]
        # 移除 flags: -rf, -r, -f 等
        cleaned = re.sub(r'\s+-[rRfFilPdV]+\s*', ' ', cleaned)
        cleaned = re.sub(r'^-[rRfFilPdV]+\s*', '', cleaned)
        # 提取路径（支持引号）
        for m in re.finditer(r'"([^"]+)"|\'([^\']+)\'|(\S+)', cleaned):
            path = m.group(1) or m.group(2) or m.group(3)
            if path and not path.startswith('-') and path not in ('2>&1', '2>/dev/null', '>/dev/null'):
                paths.append(path)
    return paths


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


def format_sse(data: dict) -> str:
    """将事件数据序列化为 SSE 格式字符串。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def tool_call_records_to_dicts(records: list, result_limit: int = 5000) -> list[dict]:
    """将工具调用记录转为可序列化 dict 列表。

    records 中每个元素为 7 元组：
        [0]=tool_name, [1]=tool_call_id, [2]=arguments, [3]=result,
        [4]=success, [5]=duration, [6]=step
    result_limit 控制 result 字段保留的字符数（None 表示不截断）。
    """
    dicts = []
    for tc in records:
        result = tc[3]
        if result_limit is None:
            result = str(result)
        elif isinstance(result, str):
            result = result[:result_limit]
        else:
            result = str(result)[:result_limit]
        dicts.append(
            {
                "tool_name": tc[0],
                "tool_call_id": tc[1],
                "arguments": tc[2],
                "result": result,
                "success": tc[4],
                "duration": tc[5],
                "step": tc[6],
            }
        )
    return dicts


def build_assistant_message_dict(
    *,
    content: str,
    thinking: str,
    thinking_duration,
    tool_call_records: list,
    blocks: list,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    context_tokens: int,
    elapsed_time: float,
    step_count: int,
    result_limit: int = 5000,
) -> dict:
    """构建用于持久化/返回的 assistant 消息字典（常规结束、HITL 中断、取消等场景共用）。"""
    return {
        "role": "assistant",
        "content": content or "",
        "timestamp": datetime.now().isoformat(),
        "thinking": thinking or None,
        "thinking_duration": thinking_duration or None,
        "tool_calls": tool_call_records_to_dicts(tool_call_records, result_limit) or None,
        "blocks": blocks or None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "context_tokens": context_tokens if context_tokens > 0 else input_tokens,
            "elapsed_time": round(elapsed_time, 2),
            "step_count": step_count,
        },
    }


def parse_tool_args(args_str: str) -> dict:
    """解析工具调用参数 JSON 字符串为 dict；标量值包装为 {"value": ...}；解析失败返回 {}。"""
    if not args_str:
        return {}
    try:
        parsed = json.loads(args_str)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


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

    # 加载记忆上下文：
    # - 长期记忆（memories/{username}/AGENTS.md）：跨会话用户偏好与经验，始终加载
    # - 会话记忆（workspace/.../memory.md）：当前会话历史经验，首轮对话时不存在
    if agent:
        long_term_memory = agent.load_long_term_memory()
        session_memory = agent.load_session_memory()
        memory_parts = []
        if long_term_memory:
            memory_parts.append(f"[长期记忆 - 用户偏好与跨会话经验]:\n{long_term_memory}")
        if session_memory:
            memory_parts.append(f"[会话记忆 - 当前会话的历史经验总结]:\n{session_memory}")
        if memory_parts:
            message_content = (
                "\n\n---\n\n".join(memory_parts)
                + f"\n\n---\n\n{message_content}"
            )
            logger.info(
                f"[{sid}] 🧠 已注入记忆上下文 | "
                f"长期: {len(long_term_memory)} 字符 | 会话: {len(session_memory)} 字符"
            )

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
            # 从消息的 usage 字段累加（API 返回的准确值）
            for msg in session_obj.messages:
                msg_usage = msg.get("usage") or {}
                pre_session_tokens += msg_usage.get("total_tokens", 0)
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

    try:

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
                "tool_calls": tool_call_records_to_dicts(tool_call_records) or None,
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
            out = total_usage["output_tokens"]
            cumulative = pre_session_tokens + inp + out
            # context_tokens: 当前上下文窗口占用（最后一次 API 调用的 input_tokens）
            ctx_tokens = last_context_tokens if last_context_tokens > 0 else inp
            return format_sse(
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
            return format_sse({"type": "thinking_start", "content": "", "step": step})

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
                    f"[{sid}] 🤔 Step {step} 思考完成 | 耗时: {duration}s | 字符: {len(step_thinking)}"
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
            return format_sse({"type": "thinking_end", "duration": duration, "step": step})

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

            # 打印 token 用量（使用 API 返回的 usage_metadata）
            inp = step_usage["input_tokens"]
            out = step_usage["output_tokens"]
            step_total = step_usage["total_tokens"]
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
            is_pending = step_usage["input_tokens"] == 0
            logger.info(
                f"[{sid}] 📊 Step {step} Token{'(等待API返回)' if is_pending else ''} | "
                f"上下文占用: {ctx_tokens}/{ctx_max or '?'}{ctx_bar} | "
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
            token_sse = format_sse(
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

        emitted_tool_call_ids = set()
        _todo_emitted_for = set()

        # HITL: per-message thread_id for checkpointer state isolation
        thread_id = f"{session_id}-{message_id}"
        stream_config = {"configurable": {"thread_id": thread_id}}

        async for event in agent.agent.astream(
            {"messages": context_messages},
            stream_mode="messages",
            config=stream_config,
        ):
            chunk, metadata = event
            node = metadata.get("langgraph_node", "?")

            # ── AIMessageChunk: reasoning / text / tool_call_chunks ──────
            if isinstance(chunk, AIMessageChunk):
                rc = (
                    extract_reasoning(chunk.additional_kwargs)
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
                        extract_reasoning(chunk.additional_kwargs)
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
                        yield _start_thinking(current_step)
                    accumulated_thinking += rc
                    yield format_sse(
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
                    yield format_sse({"type": "content", "content": content})
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

                        full_args = tool_call_accumulated_args_str[tid]
                        args_data = parse_tool_args(full_args)
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

                        yield format_sse(
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
                                yield format_sse(
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

                                # 重新推送 tool_call 事件，确保前端获取完整参数
                                # （首个 chunk 的 tool_call 事件可能参数为空）
                                yield format_sse({
                                    "type": "tool_call",
                                    "tool_name": tool_call_id_to_name.get(resolved_tid, ""),
                                    "tool_call_id": resolved_tid,
                                    "arguments": args_data,
                                    "step": current_step,
                                })

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
                                        yield format_sse(
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

                # 工具执行日志由 LoggingMiddleware 统一记录（见 services/logging_middleware.py）

                # 工具结果处理完毕，重置 step 推进标记
                # 下一个 AIMessageChunk 将开启一个新的 step
                step_advanced_this_round = False

                yield format_sse(
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
                        yield format_sse(
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
            yield format_sse({"type": "content_end", "content": ""})

        # ── HITL: 检测中断（文件删除审批） ────────────────────────────
        try:
            graph_state = await agent.agent.aget_state(stream_config)
            if graph_state.next and graph_state.tasks:
                hitl_request = None
                for task in graph_state.tasks:
                    if hasattr(task, "interrupts") and task.interrupts:
                        hitl_request = task.interrupts[0].value
                        break
                if hitl_request:
                    action_requests = hitl_request.get("action_requests", [])
                    review_configs = hitl_request.get("review_configs", [])
                    config_map = {
                        cfg.get("action_name"): cfg for cfg in review_configs
                    }
                    # 从状态中获取 tool_call_id（ActionRequest 不含 id）
                    state_msgs = graph_state.values.get("messages", [])
                    pending_tc_ids = []
                    for msg in reversed(state_msgs):
                        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                            pending_tc_ids = [tc.get("id", "") for tc in msg.tool_calls]
                            break
                    # 保存部分 assistant 消息（复用取消路径模式）
                    partial_elapsed = time.time() - start_time
                    partial_msg = build_assistant_message_dict(
                        content=accumulated_response,
                        thinking=accumulated_thinking,
                        thinking_duration=None,
                        tool_call_records=tool_call_records,
                        blocks=blocks,
                        input_tokens=total_usage.get("input_tokens", 0),
                        output_tokens=total_usage.get("output_tokens", 0),
                        total_tokens=total_usage.get("total_tokens", 0),
                        context_tokens=last_context_tokens,
                        elapsed_time=partial_elapsed,
                        step_count=current_step,
                    )
                    db.update_last_assistant_message(session_id, partial_msg)
                    db.update_last_assistant_message_row(session_id, partial_msg)
                    logger.info(
                        f"[{sid}] 🔔 HITL 中断 | thread_id={thread_id} | "
                        f"{len(action_requests)} 个操作待审批"
                    )
                    yield format_sse(
                        {
                            "type": "approval_required",
                            "thread_id": thread_id,
                            "action_requests": [
                                {
                                    "tool_call_id": pending_tc_ids[i]
                                    if i < len(pending_tc_ids)
                                    else "",
                                    "tool_name": ar.get("name", ""),
                                    "arguments": ar.get("args", {}),
                                    "description": ar.get("description", ""),
                                    "allowed_decisions": config_map.get(
                                        ar.get("name", ""), {}
                                    ).get("allowed_decisions", ["approve", "reject"]),
                                    "file_paths": _extract_file_paths_from_command(
                                        ar.get("args", {}).get("command", "")
                                    ),
                                }
                                for i, ar in enumerate(action_requests)
                            ],
                        }
                    )
                    return
        except Exception as e:
            logger.warning(f"[{sid}] HITL 中断检测异常: {e}", exc_info=True)

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
        # 本轮对话（当前 exchange）token - 使用 API 返回的 usage_metadata
        inp_tokens = total_usage["input_tokens"]
        out_tokens = total_usage["output_tokens"]
        sum_tokens = total_usage["total_tokens"]

        # 整个会话的累计占用（优先使用消息中持久化的 usage 累加）
        session_total_tokens = 0
        session_msg_count = 0
        try:
            session = db.get_session(session_id)
            if session and session.messages:
                session_msg_count = len(session.messages)
                # 从消息的 usage 字段累加（API 返回的准确值）
                for msg in session.messages:
                    msg_usage = msg.get("usage") or {}
                    session_total_tokens += msg_usage.get("total_tokens", 0)
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
        assistant_message = build_assistant_message_dict(
            content=accumulated_response,
            thinking=accumulated_thinking,
            thinking_duration=thinking_time,
            tool_call_records=tool_call_records,
            blocks=blocks,
            input_tokens=inp_tokens,
            output_tokens=out_tokens,
            total_tokens=sum_tokens,
            context_tokens=last_context_tokens,
            elapsed_time=elapsed_time,
            step_count=current_step,
            result_limit=None,
        )
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
                    "tool_calls": tool_call_records_to_dicts(tool_call_records, 1000),
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

        # 会话级记忆持久化 — 每轮对话结束后更新工作区下的 memory.md
        # 1. 根据本轮对话内容按场景更新会话记忆（重复场景更新经验，新场景添加章节）
        # 2. 确保记忆文件不超过 2000 字符，超限时自动压缩
        # 注意：此更新在 workspace rename 之后执行，确保 memory.md 写入最终目录
        if agent and agent.memory_file:
            try:
                from .memory_manager import update_memory_after_session

                # 使用当前会话 Agent 的模型（支持动态模型选择），
                # 而非全局 _llm_instance（仅启动时的默认模型）
                session_llm = getattr(agent, "model", None)
                if session_llm is None:
                    from .agent_manager import _llm_instance
                    session_llm = _llm_instance

                # 使用原始用户输入（不含注入的记忆前缀）作为记忆提取素材
                raw_user_msg = parsed_content or request.message
                updated = update_memory_after_session(
                    agent.memory_file,
                    user_message=raw_user_msg,
                    assistant_response=accumulated_response,
                    llm=session_llm,
                )
                if updated:
                    logger.info(
                        f"[{sid}] 🧠 会话记忆已更新 | 文件: {agent.memory_file} | "
                        f"上限 2000 字符"
                    )
                    # 记忆更新后使 Agent 缓存失效，下次发消息时重建 Agent 以加载新记忆
                    try:
                        from .agent_manager import remove_session_agent
                        remove_session_agent(session_id)
                        logger.info(
                            f"[{sid}] 记忆更新后 Agent 缓存已失效，下次消息将重新加载记忆"
                        )
                    except Exception as e:
                        logger.warning(f"[{sid}] 使 Agent 缓存失效失败: {e}")
            except Exception as e:
                logger.warning(f"[{sid}] 会话记忆更新失败: {e}")

        # 用户级长期记忆持久化 — 每轮对话结束后更新 memories/{username}/AGENTS.md
        # 与工作区 memory.md 的区别：这是跨会话的用户级长期记忆，记录用户偏好与可复用经验
        if agent and getattr(agent, "long_term_memory_file", None):
            try:
                from .memory_manager import update_long_term_memory_after_session

                # 复用当前会话 Agent 的模型（支持动态模型选择）
                session_llm = getattr(agent, "model", None)
                if session_llm is None:
                    from .agent_manager import _llm_instance
                    session_llm = _llm_instance

                # 使用原始用户输入（不含注入的记忆前缀）作为记忆提取素材
                raw_user_msg = parsed_content or request.message
                updated_lt = update_long_term_memory_after_session(
                    agent.long_term_memory_file,
                    user_message=raw_user_msg,
                    assistant_response=accumulated_response,
                    llm=session_llm,
                )
                if updated_lt:
                    logger.info(
                        f"[{sid}] 🧠 用户长期记忆已更新 | 文件: {agent.long_term_memory_file}"
                    )
            except Exception as e:
                logger.warning(f"[{sid}] 用户长期记忆更新失败: {e}")

        # Session logger
        if session_logger:
            tool_calls_for_log = tool_call_records_to_dicts(tool_call_records, 1000) or None
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
        yield format_sse(
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
            partial_msg = build_assistant_message_dict(
                content=accumulated_response,
                thinking=accumulated_thinking,
                thinking_duration=None,
                tool_call_records=tool_call_records,
                blocks=blocks,
                input_tokens=total_usage.get("input_tokens", 0),
                output_tokens=total_usage.get("output_tokens", 0),
                total_tokens=total_usage.get("total_tokens", 0),
                context_tokens=last_context_tokens,
                elapsed_time=time.time() - start_time,
                step_count=current_step,
            )
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


async def resume_stream_generator(
    db: Database,
    agent: EasyAgent,
    session_id: str,
    thread_id: str,
    decisions: list[dict],
    username: str = "",
    session_logger: SessionLogger = None,
) -> AsyncGenerator[str, None]:
    """HITL 恢复流：用户审批后继续执行 Agent。

    从 DB 加载中断前的部分 assistant 消息，初始化状态，
    调用 agent.astream(Command(resume=...)) 继续执行。
    """
    start_time = time.time()
    sid = session_id[-5:] if session_id else "resume"

    yield format_sse({"type": "start", "session_id": session_id})

    # 从 DB 加载部分 assistant 消息
    accumulated_response = ""
    accumulated_thinking = ""
    blocks = []
    tool_call_records = []
    current_step = 0
    block_order_counter = 0
    total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    last_context_tokens = 0

    try:
        session = db.get_session(session_id)
        if session and session.messages:
            for msg in reversed(session.messages):
                if msg.get("role") == "assistant":
                    accumulated_response = msg.get("content", "") or ""
                    accumulated_thinking = msg.get("thinking", "") or ""
                    blocks = list(msg.get("blocks") or [])
                    for tc in msg.get("tool_calls") or []:
                        tool_call_records.append([
                            tc.get("tool_name", ""),
                            tc.get("tool_call_id", ""),
                            tc.get("arguments", {}),
                            tc.get("result", ""),
                            tc.get("success", True),
                            tc.get("duration", 0),
                            tc.get("step", 0),
                        ])
                    usage = msg.get("usage") or {}
                    total_usage = {
                        "input_tokens": usage.get("input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                    }
                    current_step = usage.get("step_count", 0)
                    block_order_counter = len(blocks)
                    break
    except Exception as e:
        logger.warning(f"[{sid}] HITL 恢复: 加载部分消息失败: {e}")

    logger.info(
        f"[{sid}] 🔔 HITL 恢复 | thread_id={thread_id} | "
        f"已有内容 {len(accumulated_response)} 字符 | "
        f"已有 blocks {len(blocks)} | decisions={decisions}"
    )

    stream_config = {"configurable": {"thread_id": thread_id}}
    max_input_tokens = getattr(agent, "max_input_tokens", None) or 0
    auto_compress_tokens = getattr(agent, "auto_compress_tokens", None) or 0

    is_in_thinking = False
    thinking_start_time = None
    thinking_step_start_len = 0
    content_start_time = None
    tool_call_start_times = {}
    tool_call_accumulated_args = {}
    tool_call_accumulated_args_str = {}
    tool_call_id_to_name = {}
    tool_call_index_to_id = {}
    pending_tool_args_by_index = {}
    current_thinking_block_idx = None
    current_content_block_idx = None
    emitted_tool_call_ids = set()
    _todo_emitted_for = set()

    def _persist_todos(todos_list):
        try:
            db.update_session_todos(session_id, todos_list)
            logger.info(f"[{sid}] 📋 Todo list persisted | items: {len(todos_list)}")
        except Exception as e:
            logger.warning(f"[{sid}] 持久化 Todo list 失败: {e}")

    def _end_thinking(step):
        nonlocal is_in_thinking, thinking_start_time, current_thinking_block_idx, thinking_step_start_len
        if not is_in_thinking:
            return None
        is_in_thinking = False
        duration = round(time.time() - thinking_start_time, 2) if thinking_start_time else 0
        thinking_start_time = None
        current_thinking_block_idx = None
        step_thinking_len = len(accumulated_thinking[thinking_step_start_len:].strip())
        logger.info(
            f"[{sid}] 🤔 Step {step} 思考完成 | 耗时: {duration}s | 字符: {step_thinking_len}"
        )
        return format_sse({"type": "thinking_end", "duration": duration, "step": step})

    def _end_thinking_if_needed():
        nonlocal is_in_thinking
        if not is_in_thinking:
            return None
        return _end_thinking(current_step)

    def _seal_content_block():
        nonlocal current_content_block_idx
        current_content_block_idx = None

    def _log_step_start(step, step_type, detail=""):
        msg = f"[{sid}] 🚀 Step {step} 开始 | 类型: {step_type}"
        if detail:
            msg += f" | {detail}"
        logger.info(msg)

    try:
        async for event in agent.agent.astream(
            Command(resume={"decisions": decisions}),
            stream_mode="messages",
            config=stream_config,
        ):
            chunk, metadata = event

            if isinstance(chunk, AIMessageChunk):
                rc = extract_reasoning(chunk.additional_kwargs) if hasattr(chunk, "additional_kwargs") else ""
                raw_content = chunk.content or ""
                tcc = getattr(chunk, "tool_call_chunks", None) or []

                if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
                    usage_meta = chunk.usage_metadata
                    total_usage["input_tokens"] += usage_meta.get("input_tokens", 0)
                    total_usage["output_tokens"] += usage_meta.get("output_tokens", 0)
                    total_usage["total_tokens"] += usage_meta.get("total_tokens", 0)
                    if usage_meta.get("input_tokens", 0) > 0:
                        last_context_tokens = usage_meta["input_tokens"]

                if rc:
                    if not is_in_thinking:
                        is_in_thinking = True
                        thinking_start_time = time.time()
                        thinking_step_start_len = len(accumulated_thinking)
                        current_step += 1
                        current_thinking_block_idx = block_order_counter
                        blocks.append({"type": "thinking", "order": block_order_counter, "content": "", "step": current_step})
                        block_order_counter += 1
                        yield format_sse({"type": "thinking_start", "step": current_step})
                    accumulated_thinking += rc
                    blocks[current_thinking_block_idx]["content"] = accumulated_thinking
                    yield format_sse({"type": "thinking", "content": rc, "step": current_step})

                if raw_content and not tcc:
                    if is_in_thinking:
                        te = _end_thinking(current_step)
                        if te:
                            yield te
                    if current_content_block_idx is None:
                        current_content_block_idx = block_order_counter
                        blocks.append({"type": "content", "order": block_order_counter, "content": "", "step": current_step})
                        block_order_counter += 1
                        if not content_start_time:
                            content_start_time = time.time()
                        yield format_sse({"type": "content_start", "step": current_step})
                    accumulated_response += raw_content
                    blocks[current_content_block_idx]["content"] = accumulated_response
                    yield format_sse({"type": "content", "content": raw_content})

                if tcc:
                    te = _end_thinking_if_needed()
                    if te:
                        yield te
                    _seal_content_block()
                    for tc_chunk in tcc:
                        tc_name = tc_chunk.get("name", "") or ""
                        tc_id = tc_chunk.get("id", "") or ""
                        tc_args_str = str(tc_chunk.get("args", "") or "")
                        tc_index = tc_chunk.get("index")

                        if tc_name and tc_id:
                            current_step += 1
                            _log_step_start(current_step, "工具调用", f"tools: [{tc_name}]")
                            tool_call_id_to_name[tc_id] = tc_name
                            tool_call_start_times[tc_id] = time.time()
                            if tc_index is not None:
                                tool_call_index_to_id[tc_index] = tc_id

                            pending_prefix = ""
                            if (
                                tc_index is not None
                                and tc_index in pending_tool_args_by_index
                            ):
                                pending_prefix = pending_tool_args_by_index.pop(tc_index)

                            tool_call_accumulated_args_str[tc_id] = pending_prefix + tc_args_str

                            full_args = tool_call_accumulated_args_str[tc_id]
                            args_data = parse_tool_args(full_args)
                            tool_call_accumulated_args[tc_id] = args_data
                            emitted_tool_call_ids.add(tc_id)
                            blocks.append({
                                "type": "tool_call",
                                "order": block_order_counter,
                                "tool_name": tc_name,
                                "tool_call_id": tc_id,
                                "arguments": args_data,
                                "result": "",
                                "success": True,
                                "duration": None,
                                "step": current_step,
                            })
                            block_order_counter += 1
                            yield format_sse({
                                "type": "tool_call",
                                "tool_name": tc_name,
                                "tool_call_id": tc_id,
                                "arguments": args_data,
                                "step": current_step,
                            })

                            if tc_name == "write_todos" and isinstance(args_data, dict):
                                todos = args_data.get("todos", [])
                                if todos:
                                    logger.info(f"[{sid}] 📋 Todo list | items: {len(todos)}")
                                    _persist_todos(todos)
                                    _todo_emitted_for.add(tc_id)
                                    yield format_sse({
                                        "type": "todo_list",
                                        "todos": todos,
                                        "step": current_step,
                                    })
                        elif tc_id or tc_index is not None:
                            resolved_tid = tc_id
                            if not resolved_tid and tc_index is not None:
                                resolved_tid = tool_call_index_to_id.get(tc_index, "")

                            if resolved_tid and resolved_tid in tool_call_id_to_name:
                                tool_call_accumulated_args_str[resolved_tid] = (
                                    tool_call_accumulated_args_str.get(resolved_tid, "")
                                    + tc_args_str
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

                                    # 重新推送 tool_call 事件，确保前端获取完整参数
                                    yield format_sse({
                                        "type": "tool_call",
                                        "tool_name": tool_call_id_to_name.get(resolved_tid, ""),
                                        "tool_call_id": resolved_tid,
                                        "arguments": args_data,
                                        "step": current_step,
                                    })

                                    # write_todos: 推送 todo_list 事件
                                    _tc_name = tool_call_id_to_name.get(resolved_tid, "")
                                    if _tc_name == "write_todos" and isinstance(args_data, dict):
                                        todos = args_data.get("todos", [])
                                        if todos and resolved_tid not in _todo_emitted_for:
                                            logger.info(f"[{sid}] 📋 Todo list | items: {len(todos)}")
                                            _persist_todos(todos)
                                            _todo_emitted_for.add(resolved_tid)
                                            yield format_sse({
                                                "type": "todo_list",
                                                "todos": todos,
                                                "step": current_step,
                                            })
                                except json.JSONDecodeError:
                                    pass
                            elif tc_index is not None:
                                pending_tool_args_by_index[tc_index] = (
                                    pending_tool_args_by_index.get(tc_index, "")
                                    + tc_args_str
                                )

            elif isinstance(chunk, ToolMessage):
                te = _end_thinking_if_needed()
                if te:
                    yield te
                resolved_tid = chunk.tool_call_id
                tool_name = chunk.name or "tool"
                tool_args = tool_call_accumulated_args.get(resolved_tid, {})
                result_content = _parse_mcp_content(chunk.content) if chunk.content else ""
                chunk_error = getattr(chunk, "additional_kwargs", {}).get("is_error", False)
                content_error = _is_error_result(result_content)
                success = not chunk_error and not content_error
                tool_start = tool_call_start_times.pop(resolved_tid, None)
                tool_duration = round(time.time() - tool_start, 2) if tool_start else 0

                for blk in reversed(blocks):
                    if blk["type"] == "tool_call" and blk.get("tool_call_id") == resolved_tid:
                        blk["result"] = result_content
                        blk["success"] = success
                        blk["duration"] = tool_duration
                        break

                tool_call_records.append([
                    tool_name, resolved_tid, tool_args, result_content,
                    success, tool_duration, current_step,
                ])

                # 工具执行日志由 LoggingMiddleware 统一记录（见 services/logging_middleware.py）

                yield format_sse({
                    "type": "tool_result",
                    "tool_name": tool_name,
                    "tool_call_id": resolved_tid,
                    "arguments": tool_args,
                    "result": result_content,
                    "success": success,
                    "duration": tool_duration,
                    "step": current_step,
                })

                # write_todos: 从工具结果中提取 todo list
                if tool_name == "write_todos" and resolved_tid not in _todo_emitted_for:
                    todos = (
                        tool_args.get("todos", [])
                        if isinstance(tool_args, dict)
                        else []
                    )
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
                        yield format_sse({
                            "type": "todo_list",
                            "todos": todos,
                            "step": current_step,
                        })

        # ── Post-streaming ─────────────────────────────────────────
        logger.info(f"[{sid}] 📢 流式输出结束 | steps={current_step} | blocks={len(blocks)}")
        te = _end_thinking(current_step)
        if te:
            yield te
        _seal_content_block()
        if accumulated_response:
            yield format_sse({"type": "content_end", "content": ""})

        # ── HITL: 检测嵌套中断 ─────────────────────────────────────
        try:
            graph_state = await agent.agent.aget_state(stream_config)
            if graph_state.next and graph_state.tasks:
                hitl_request = None
                for task in graph_state.tasks:
                    if hasattr(task, "interrupts") and task.interrupts:
                        hitl_request = task.interrupts[0].value
                        break
                if hitl_request:
                    action_requests = hitl_request.get("action_requests", [])
                    review_configs = hitl_request.get("review_configs", [])
                    config_map = {cfg.get("action_name"): cfg for cfg in review_configs}
                    state_msgs = graph_state.values.get("messages", [])
                    pending_tc_ids = []
                    for msg in reversed(state_msgs):
                        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                            pending_tc_ids = [tc.get("id", "") for tc in msg.tool_calls]
                            break
                    partial_elapsed = time.time() - start_time
                    partial_msg = build_assistant_message_dict(
                        content=accumulated_response,
                        thinking=accumulated_thinking,
                        thinking_duration=None,
                        tool_call_records=tool_call_records,
                        blocks=blocks,
                        input_tokens=total_usage.get("input_tokens", 0),
                        output_tokens=total_usage.get("output_tokens", 0),
                        total_tokens=total_usage.get("total_tokens", 0),
                        context_tokens=last_context_tokens,
                        elapsed_time=partial_elapsed,
                        step_count=current_step,
                    )
                    db.update_last_assistant_message(session_id, partial_msg)
                    db.update_last_assistant_message_row(session_id, partial_msg)
                    yield format_sse({
                        "type": "approval_required",
                        "thread_id": thread_id,
                        "action_requests": [
                            {
                                "tool_call_id": pending_tc_ids[i] if i < len(pending_tc_ids) else "",
                                "tool_name": ar.get("name", ""),
                                "arguments": ar.get("args", {}),
                                "allowed_decisions": config_map.get(ar.get("name", ""), {}).get("allowed_decisions", ["approve", "reject"]),
                                "file_paths": _extract_file_paths_from_command(
                                    ar.get("args", {}).get("command", "")
                                ),
                            }
                            for i, ar in enumerate(action_requests)
                        ],
                    })
                    return
        except Exception as e:
            logger.warning(f"[{sid}] HITL 恢复: 嵌套中断检测异常: {e}", exc_info=True)

        elapsed_time = time.time() - start_time

        # 会话累计 token
        session_total_tokens = 0
        try:
            sess = db.get_session(session_id)
            if sess and sess.messages:
                for msg in sess.messages:
                    msg_usage = msg.get("usage") or {}
                    session_total_tokens += msg_usage.get("total_tokens", 0)
        except Exception:
            pass

        inp_tokens = total_usage["input_tokens"]
        out_tokens = total_usage["output_tokens"]
        sum_tokens = total_usage["total_tokens"]

        usage_payload = {
            **total_usage,
            "max_input_tokens": max_input_tokens,
            "auto_compress_tokens": auto_compress_tokens,
            "session_estimate": session_total_tokens,
            "context_tokens": last_context_tokens if last_context_tokens > 0 else inp_tokens,
            "elapsed_time": round(elapsed_time, 2),
            "step_count": current_step,
        }

        assistant_message = build_assistant_message_dict(
            content=accumulated_response,
            thinking=accumulated_thinking,
            thinking_duration=None,
            tool_call_records=tool_call_records,
            blocks=blocks,
            input_tokens=inp_tokens,
            output_tokens=out_tokens,
            total_tokens=sum_tokens,
            context_tokens=last_context_tokens,
            elapsed_time=elapsed_time,
            step_count=current_step,
        )
        db.update_last_assistant_message(session_id, assistant_message)
        db.update_last_assistant_message_row(session_id, assistant_message)

        logger.info(
            f"[{sid}] ✅ 流式响应完成 | 总步骤: {current_step} | "
            f"总耗时: {elapsed_time:.2f}s | tokens={sum_tokens} (in={inp_tokens}, out={out_tokens})"
        )

        yield format_sse({
            "type": "done",
            "session_id": session_id,
            "elapsed_time": round(elapsed_time, 2),
            "usage": usage_payload,
        })

    except asyncio.CancelledError:
        logger.info(f"[{sid}] HITL 恢复被取消")
        partial_msg = build_assistant_message_dict(
            content=accumulated_response,
            thinking=accumulated_thinking,
            thinking_duration=None,
            tool_call_records=tool_call_records,
            blocks=blocks,
            input_tokens=total_usage.get("input_tokens", 0),
            output_tokens=total_usage.get("output_tokens", 0),
            total_tokens=total_usage.get("total_tokens", 0),
            context_tokens=last_context_tokens,
            elapsed_time=time.time() - start_time,
            step_count=current_step,
        )
        db.update_last_assistant_message(session_id, partial_msg)
        db.update_last_assistant_message_row(session_id, partial_msg)
        yield format_sse({"type": "error", "content": "请求被取消"})
    except Exception as e:
        logger.error(f"[{sid}] HITL 恢复异常: {str(e)}", exc_info=True)
        yield format_sse({"type": "error", "content": f"处理失败: {str(e)}"})
