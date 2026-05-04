"""Chat streaming service - SSE streaming generator with thinking/tool support"""

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import AsyncGenerator

from langchain_core.messages import AIMessage, HumanMessage

from ...agent import EasyAgent
from ..db import Database
from ..models import ChatRequest
from ..utils.session_logger import SessionLogger
from .agent_manager import get_agent_config

logger = logging.getLogger("easy_agent.chat_service")

MAX_CONTEXT_MESSAGES = 30
KEEP_RECENT_MESSAGES = 10


async def compress_context(
    messages: list[dict],
    keep_recent: int = KEEP_RECENT_MESSAGES,
) -> tuple[list[dict], str, int]:
    """Compress old conversation messages into a summary.

    Returns:
        (compressed_messages, summary_text, original_count)
    """
    from ...model import create_model

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
    """Build the full message with context awareness and compression."""
    session = db.get_session(session_id)
    if not session or len(session.messages) <= 4:
        return current_message

    session_messages = session.messages

    if len(session_messages) > MAX_CONTEXT_MESSAGES:
        compressed_msgs, summary, original_count = await compress_context(session_messages)
        if summary:
            context_summary = f"[历史对话摘要（前{original_count}条消息已压缩）]:\n{summary}\n\n"
            if session_logger:
                session_logger.log_context_compression(summary, original_count, len(compressed_msgs))
            logger.info(f"[{session_id[-5:]}] 上下文已压缩 | 原消息数: {original_count} | 摘要长度: {len(summary)}")
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
        message_content = f"[workspace: {ws} | tool path: /workspace/xxx | shell: cd {ws}]\n{message_content}"

    if session_id and db:
        message_content = await build_context_messages(db, session_id, message_content, session_logger)

    ws_info = str(agent.workspace_dir.absolute()) if agent and agent.workspace_dir else "unknown"
    logger.info(f"[{sid}] 开始流式响应 | workspace: {ws_info} | message: {message_content[:50]}{'...' if len(message_content) > 50 else ''} | 用户: {username}")

    def format_sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    try:
        yield format_sse({"type": "start", "session_id": session_id})

        start_time = time.time()

        has_streaming = hasattr(agent.agent, 'astream')
        # Initialized here for cancellation handler access
        blocks = []
        tool_call_records = []
        accumulated_thinking = ""
        accumulated_response = ""
        total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        # Extract context window info from model profile
        max_input_tokens = None
        auto_compress_tokens = None
        model_instance = getattr(agent, 'model', None)
        if model_instance and hasattr(model_instance, 'profile') and model_instance.profile:
            max_input_tokens = model_instance.profile.get("max_input_tokens")
        if max_input_tokens:
            auto_compress_tokens = int(max_input_tokens * 0.85)
        else:
            auto_compress_tokens = 170000  # fallback for unknown models

        if has_streaming:
            logger.info(f"[{sid}] 🚀 使用 DeepAgents 流式接口(支持 skills)")

            context_messages = []
            session = db.get_session(session_id)
            if session and session.messages:
                for msg in session.messages:
                    if msg.get("role") == "user":
                        context_messages.append(HumanMessage(content=str(msg.get("content", ""))))
                    elif msg.get("role") == "assistant":
                        assistant_content = str(msg.get("content", ""))
                        if msg.get("thinking"):
                            assistant_content = f"[思考]: {msg.get('thinking')}\n\n{assistant_content}"
                        context_messages.append(AIMessage(content=assistant_content))

            context_messages.append(HumanMessage(content=message_content))
            messages = context_messages
            full_response = ""
            current_thinking = ""
            thinking_started = False
            thinking_start_time = None
            thinking_end_time = None
            assistant_started = False
            current_step = 0
            current_step_thinking = ""
            tool_call_start_times = {}
            tool_call_step_map = {}
            tool_call_block_added = set()  # Track which tool calls have blocks already

            thinking_records = []

            pending_tool_calls = {}

            accumulated_content = ""
            is_in_thinking = False
            thinking_buffer = ""
            first_token_time = None
            msg_received_time = None
            content_start_time = None
            total_tool_duration = 0
            is_after_tool_result = False
            tool_call_accumulated_args = {}
            tool_call_id_map = {}
            last_persisted_len = 0

            # Track blocks for preserving execution order in DB
            block_order = 0
            current_content_block = None  # Track active content block

            def build_partial_assistant_message():
                """Build current assistant message for incremental persistence."""
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
                    ] if tool_call_records else None,
                    "blocks": blocks if blocks else None,
                }

            def persist_partial():
                """Persist current state to DB (incremental checkpoint)."""
                try:
                    msg = build_partial_assistant_message()
                    db.update_last_assistant_message(session_id, msg)
                    db.update_last_assistant_message_row(session_id, msg)
                except Exception as e:
                    logger.warning(f"[{sid}] 增量持久化失败: {e}")

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

                # Accumulate token usage from LLM response chunks
                if hasattr(token, 'usage_metadata') and token.usage_metadata:
                    um = token.usage_metadata
                    total_usage["input_tokens"] += um.get("input_tokens", 0) or 0
                    total_usage["output_tokens"] += um.get("output_tokens", 0) or 0
                    total_usage["total_tokens"] += um.get("total_tokens", 0) or 0
                # 某些模型可能使用 response_metadata 返回 usage
                elif hasattr(token, 'response_metadata') and token.response_metadata:
                    rm = token.response_metadata
                    if 'usage' in rm:
                        usage = rm['usage']
                        total_usage["input_tokens"] += usage.get('prompt_tokens', 0) or 0
                        total_usage["output_tokens"] += usage.get('completion_tokens', 0) or 0
                        total_usage["total_tokens"] += usage.get('total_tokens', 0) or 0

                is_subagent = any(s.startswith("tools:") for s in ns)
                if is_subagent:
                    continue

                content = getattr(token, 'content', '')
                if content:
                    content_str = str(content)
                    accumulated_content += content_str

                    if not is_in_thinking and '<think' in content_str.lower():
                        is_in_thinking = True
                        is_after_tool_result = False
                        msg_received_time = time.time()
                        current_step += 1
                        logger.info(f"[{sid}] 🤔 Step {current_step} 思考开始")
                        block_order += 1
                        blocks.append({
                            "type": "thinking",
                            "content": "",
                            "order": block_order,
                            "step": current_step,
                        })
                        yield format_sse({
                            "type": "thinking_start",
                            "content": "",
                            "step": current_step,
                        })

                    if is_in_thinking and '</think' in content_str.lower():
                        end_match = re.search(r'</think[^>]*>', content_str, re.IGNORECASE)
                        if end_match:
                            thinking_part = content_str[:end_match.start()]
                            response_part = content_str[end_match.end():]

                            if thinking_part.strip():
                                yield format_sse({
                                    "type": "thinking",
                                    "content": thinking_part,
                                    "step": current_step,
                                })
                                accumulated_thinking += thinking_part

                            thinking_end_time = time.time()
                            thinking_duration = thinking_end_time - thinking_start_time if thinking_start_time else 0

                            thinking_clean = accumulated_thinking.replace('<think>', '').replace('</think>', '').replace('<think ', '').replace('</think ', '').strip()
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

                            logger.info(f"[{sid}] 🤔 Step {current_step} 思考完成 | 耗时: {thinking_duration:.2f}s | 内容长度: {len(thinking_clean)} | 内容: {thinking_clean[:500]}")
                            # Update last thinking block with final content and duration
                            for blk in reversed(blocks):
                                if blk["type"] == "thinking" and blk["step"] == current_step:
                                    blk["content"] = thinking_clean
                                    blk["duration"] = round(thinking_duration, 2)
                                    break
                            yield format_sse({
                                "type": "thinking_end",
                                "duration": round(thinking_duration, 2),
                                "step": current_step,
                            })

                            is_in_thinking = False

                            if response_part.strip():
                                if content_start_time is None:
                                    content_start_time = time.time()
                                # Add content block if needed
                                if not current_content_block:
                                    block_order += 1
                                    current_content_block = {
                                        "type": "content",
                                        "content": "",
                                        "order": block_order,
                                    }
                                    blocks.append(current_content_block)
                                current_content_block["content"] += response_part
                                yield format_sse({
                                    "type": "content",
                                    "content": response_part,
                                })
                                accumulated_response += response_part
                    elif is_in_thinking:
                        accumulated_thinking += content_str
                        yield format_sse({
                            "type": "thinking",
                            "content": content_str,
                            "step": current_step,
                        })
                    else:
                        if is_after_tool_result:
                            logger.info(f"[{sid}] ⏭️ Step {current_step} 跳过重复 content | 长度: {len(content_str)}")
                            is_after_tool_result = False
                        else:
                            accumulated_response += content_str
                            logger.info(f"[{sid}] 📤 Step {current_step} 正式内容\n内容: {accumulated_response[:500]}")
                            # Add content block if needed
                            if not current_content_block:
                                block_order += 1
                                current_content_block = {
                                    "type": "content",
                                    "content": "",
                                    "order": block_order,
                                }
                                blocks.append(current_content_block)
                            current_content_block["content"] += content_str
                            yield format_sse({
                                "type": "content",
                                "content": content_str,
                            })
                            # Periodic persistence during content streaming
                            if len(accumulated_response) - last_persisted_len >= 500:
                                last_persisted_len = len(accumulated_response)
                                persist_partial()

                if hasattr(token, "tool_call_chunks") and token.tool_call_chunks:
                    for tc_chunk in token.tool_call_chunks:
                        tool_name_from_chunk = tc_chunk.get("name")
                        args_data = tc_chunk.get("args")
                        chunk_id = tc_chunk.get("id")

                        if tool_name_from_chunk:
                            tool_name = tool_name_from_chunk
                            if chunk_id:
                                tool_call_id_map[tool_name] = chunk_id
                            tool_call_start_times[tool_name] = time.time()
                            tool_call_step_map[tool_name] = current_step
                            # Add a new tool_call block for this tool invocation
                            current_content_block = None  # Reset so next content gets a new block
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
                            if isinstance(args_data, dict):
                                tool_call_accumulated_args[tool_name] = args_data.copy()
                            elif isinstance(args_data, str) and args_data:
                                try:
                                    parsed = json.loads(args_data)
                                    if isinstance(parsed, dict):
                                        tool_call_accumulated_args[tool_name] = parsed
                                    else:
                                        tool_call_accumulated_args[tool_name] = {"value": parsed}
                                except json.JSONDecodeError:
                                    tool_call_accumulated_args[tool_name] = {"raw": args_data}
                            else:
                                tool_call_accumulated_args[tool_name] = {}
                        elif tool_name and tool_name in tool_call_accumulated_args:
                            if isinstance(args_data, dict):
                                tool_call_accumulated_args[tool_name].update(args_data)
                            elif isinstance(args_data, str) and args_data:
                                try:
                                    parsed_args = json.loads(args_data)
                                    if isinstance(parsed_args, dict):
                                        tool_call_accumulated_args[tool_name].update(parsed_args)
                                    else:
                                        current_raw = tool_call_accumulated_args[tool_name].get("raw", "")
                                        tool_call_accumulated_args[tool_name]["raw"] = current_raw + args_data
                                except json.JSONDecodeError:
                                    current_raw = tool_call_accumulated_args[tool_name].get("raw", "")
                                    tool_call_accumulated_args[tool_name]["raw"] = current_raw + args_data

                    if tool_name and tool_name in tool_call_accumulated_args:
                        full_args = tool_call_accumulated_args[tool_name]

                        if "raw" in full_args and len(full_args) == 1:
                            log_args = full_args["raw"]
                        else:
                            log_args = json.dumps(full_args, ensure_ascii=False)

                        logger.info(f"[{sid}] 🔧 Step {current_step} 工具调用: {tool_name} | 参数: {log_args[:2000]}")
                        is_after_tool_result = True
                        # Update tool_call block arguments
                        for blk in reversed(blocks):
                            if blk["type"] == "tool_call" and blk["tool_name"] == tool_name:
                                blk["arguments"] = full_args
                                break
                        yield format_sse({
                            "type": "tool_call",
                            "tool_name": tool_name,
                            "tool_call_id": tool_call_id_map.get(tool_name, ""),
                            "arguments": full_args,
                            "step": current_step,
                        })

                if token.type == "tool":
                    tool_name = getattr(token, "name", "") or ""
                    result_content = str(getattr(token, "content", ""))

                    is_after_tool_result = True

                    tool_success = True

                    exit_code_match = re.search(r'Exit code:\s*(\d+)', result_content)
                    if exit_code_match:
                        exit_code = int(exit_code_match.group(1))
                        if exit_code != 0:
                            tool_success = False

                    error_keywords = [
                        'error', 'failed', 'cannot', 'exception', 'traceback',
                        'permission denied', 'no such file', 'file not found',
                        'already exists', 'command not found'
                    ]
                    result_lower = result_content.lower()
                    for keyword in error_keywords:
                        if keyword in result_lower:
                            tool_success = False
                            break

                    tool_duration = 0
                    if tool_name in tool_call_start_times:
                        tool_duration = time.time() - tool_call_start_times[tool_name]
                        del tool_call_start_times[tool_name]
                        total_tool_duration += tool_duration

                    tool_args = tool_call_accumulated_args.pop(tool_name, {}) if tool_name in tool_call_accumulated_args else {}
                    if not isinstance(tool_args, dict):
                        tool_args = {}
                    elif "raw" in tool_args and len(tool_args) == 1:
                        try:
                            parsed = json.loads(tool_args["raw"])
                            if isinstance(parsed, dict):
                                tool_args = parsed
                        except json.JSONDecodeError:
                            pass

                    result_len = len(result_content)
                    result_preview = result_content[:500] if result_content else ""

                    if "raw" in tool_args and len(tool_args) == 1:
                        log_args = tool_args["raw"]
                    else:
                        log_args = json.dumps(tool_args, ensure_ascii=False)

                    logger.info(f"[{sid}] Step {current_step} 工具调用: {tool_name} | 参数: {log_args[:2000]} | 耗时: {tool_duration:.2f}s | 内容长度: {result_len} | 调用结果: {'成功' if tool_success else '失败'} | 结果: {result_preview}")

                    # Update tool_call block with result
                    for blk in reversed(blocks):
                        if blk["type"] == "tool_call" and blk["tool_name"] == tool_name:
                            blk["result"] = result_content
                            blk["success"] = tool_success
                            blk["duration"] = round(tool_duration, 2)
                            break

                    tool_call_id = tool_call_id_map.pop(tool_name, f"tool-{tool_name}-{len(tool_call_records)}")
                    tool_call_records.append((
                        tool_name,
                        tool_call_id,
                        tool_args,
                        result_content,
                        tool_success,
                        round(tool_duration, 2),
                        current_step,
                    ))

                    yield format_sse({
                        "type": "tool_result",
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "arguments": tool_args,
                        "result": result_content,
                        "success": tool_success,
                        "duration": round(tool_duration, 2),
                        "step": current_step,
                    })

                    try:
                        db.record_tool_call(
                            session_id=session_id,
                            message_id=message_id,
                            tool_name=tool_name,
                            tool_call_id=tool_call_id,
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
                            tool_call_id=tool_call_id,
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
                            filename = file_args.get("file_name") or file_args.get("path") or file_args.get("file_path", "")
                            if filename:
                                base_name = os.path.basename(filename) if filename else "unknown"
                                ext = os.path.splitext(base_name)[1].lstrip(".") or "txt"
                                db.add_generated_file(
                                    session_id=session_id,
                                    message_id=message_id,
                                    filename=base_name,
                                    file_path=str(agent.workspace_dir / filename) if not os.path.isabs(filename) else filename,
                                    file_type=ext,
                                    size=0,
                                )
                                logger.info(f"[{sid}] 📄 记录生成文件: {base_name}")
                        except Exception as e:
                            logger.warning(f"[{sid}] 记录生成文件失败: {e}")

                    # Incremental persistence after each tool result
                    persist_partial()

            final_thinking_duration = None
            if accumulated_thinking.strip() and thinking_start_time and thinking_end_time:
                final_thinking_duration = round(thinking_end_time - thinking_start_time, 2)
                db.record_thinking(
                    session_id=session_id,
                    message_id=message_id,
                    step=current_step,
                    content=accumulated_thinking.strip(),
                    duration=final_thinking_duration,
                )
                logger.info(f"[{sid}] 💾 思考记录已保存 | 长度: {len(accumulated_thinking)} | 耗时: {final_thinking_duration}s")

            user_input_needed = False
            if accumulated_response:
                response_clean = accumulated_response.strip()
                question_patterns = [
                    r'[？?]\s*$',
                    r'方案[一二三123]\s*[）\)]?\s*$',
                    r'请选择', r'是否确认', r'确认要',
                    r'是否继续', r'你希望', r'你想[要]?',
                    r'输入\s*[1-3]', r'选择\s*[1-3]',
                    r'\([Yy]\/[Nn]\)', r'\([是否]\/[是否]\)',
                ]
                for pattern in question_patterns:
                    if re.search(pattern, response_clean, re.MULTILINE):
                        user_input_needed = True
                        break

                if user_input_needed:
                    logger.info(f"[{sid}] 🤝 检测到需要用户输入 | 内容: {response_clean[:200]}")
                    yield format_sse({
                        "type": "user_input_required",
                        "content": response_clean,
                    })

            if accumulated_response:
                yield format_sse({
                    "type": "content_end",
                    "content": "",
                })
                content_end_time = time.time()

            elapsed_time = time.time() - start_time

            thinking_time = round(thinking_end_time - thinking_start_time, 2) if thinking_start_time and thinking_end_time else 0
            content_time = round(content_end_time - content_start_time, 2) if content_start_time and content_end_time else 0

            if thinking_started and thinking_start_time and thinking_end_time is None:
                thinking_duration = time.time() - thinking_start_time
                logger.info(f"[{sid}] 🤔 Step {current_step} 思考结束(异常) | 耗时: {thinking_duration:.2f}s | 长度: {len(accumulated_thinking)}")
                yield format_sse({
                    "type": "thinking_end",
                    "duration": round(thinking_duration, 2),
                    "step": current_step,
                })

            logger.info(f"[{sid}] ✅ 流式响应完成 | 总步骤: {current_step} | 总耗时: {elapsed_time:.2f}s | 思考: {thinking_time}s | 回复: {content_time}s | 工具: {total_tool_duration:.2f}s | 思考长度: {len(accumulated_thinking)} | 回复长度: {len(accumulated_response)} 字符")
            if accumulated_thinking:
                logger.info(f"[{sid}] 🤔 思考内容:\n{accumulated_thinking}")
            if accumulated_response:
                logger.info(f"[{sid}] 💬 回复内容:\n{accumulated_response}")

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
                ] if tool_call_records else None,
                "blocks": blocks if blocks else None,
            }
            db.update_last_assistant_message(session_id, assistant_message)
            db.update_last_assistant_message_row(session_id, assistant_message)

            # Save context file to logs directory
            try:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                sid_prefix = session_id[:5]
                context_filename = f"{ts}_{sid_prefix}.json"
                log_dir = Path("logs/sessions")
                log_dir.mkdir(parents=True, exist_ok=True)
                context_path = log_dir / context_filename

                # Build full session message history
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
                            "thinking": accumulated_thinking if accumulated_thinking else None,
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
                        ] if tool_call_records else [],
                        "blocks": blocks if blocks else [],
                        "steps": current_step,
                        "elapsed_time": round(elapsed_time, 2),
                    },
                }

                with open(context_path, 'w', encoding='utf-8') as f:
                    json.dump(context_data, f, ensure_ascii=False, indent=2)

                logger.info(f"[{sid}] Context file saved: {context_filename}")
            except Exception as e:
                logger.warning(f"[{sid}] Failed to save context file: {e}")

            # Rename workspace if files were generated during this stream
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

                        logger.info(f"[{sid}] Workspace renamed: {old_ws_path} -> {new_ws_path}")
                    except Exception as e:
                        logger.warning(f"[{sid}] Workspace rename failed: {e}")

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
                ] if tool_call_records else None
                session_logger.log_assistant_response(
                    content=accumulated_response,
                    thinking=accumulated_thinking if accumulated_thinking else None,
                    tool_calls=tool_calls_for_log,
                )

            logger.info(f"[{sid}] Token usage: input={total_usage['input_tokens']}, output={total_usage['output_tokens']}, total={total_usage['total_tokens']}")
            yield format_sse({
                "type": "done",
                "session_id": session_id,
                "elapsed_time": round(elapsed_time, 2),
                "usage": {
                    **total_usage,
                    "max_input_tokens": max_input_tokens,
                    "auto_compress_tokens": auto_compress_tokens,
                },
            })

        else:
            messages = [{"role": "user", "content": message_content}]

            logger.info(f"[{sid}] 💬 开始非流式回复")

            yield format_sse({
                "type": "assistant_start",
                "content": "",
            })

            start_time = time.time()
            response_content = await agent.run(message_content)
            elapsed_time = time.time() - start_time

            logger.info(f"[{sid}] ✅ 非流式响应完成 | 耗时: {elapsed_time:.2f}s | 回复长度: {len(response_content)} 字符")

            yield format_sse({
                "type": "content",
                "content": response_content,
            })

            assistant_message = {
                "role": "assistant",
                "content": response_content,
                "timestamp": datetime.now().isoformat(),
            }
            db.add_message(session_id, assistant_message)
            db.add_message_row(session_id, assistant_message)

            if session_logger:
                session_logger.log_assistant_response(content=response_content)

            yield format_sse({
                "type": "done",
                "session_id": session_id,
                "elapsed_time": round(elapsed_time, 2),
                "usage": {
                    **total_usage,
                    "max_input_tokens": max_input_tokens,
                    "auto_compress_tokens": auto_compress_tokens,
                },
            })

    except asyncio.CancelledError:
        logger.info(f"[{sid}] ❌ 请求被取消")
        # Persist partial content on cancellation
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
                ] if tool_call_records else None,
                "blocks": blocks if blocks else None,
            }
            db.update_last_assistant_message(session_id, partial_msg)
            db.update_last_assistant_message_row(session_id, partial_msg)
            logger.info(f"[{sid}] 💾 取消时已保存部分回复 | 长度: {len(accumulated_response)}")
        except Exception as e:
            logger.warning(f"[{sid}] 取消时保存失败: {e}")
        yield format_sse({"type": "error", "content": "请求被取消"})
    except Exception as e:
        logger.error(f"[{sid}] ❌ 聊天异常: {str(e)}", exc_info=True)
        yield format_sse({"type": "error", "content": f"处理失败: {str(e)}"})
