"""Chat service for streaming responses"""

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime
from typing import AsyncGenerator

from langchain_core.messages import HumanMessage

from ...agent import EasyAgent
from ...config import Config
from ..database import Database, SessionModel, get_database
from ..models import ChatRequest

logger = logging.getLogger("easy_agent.chat_service")

_session_agents: dict[str, EasyAgent] = {}
_agent_config: dict = None
_llm_instance = None  # 直接持有 LLM 实例用于流式调用


def init_agent_config(config: Config, system_prompt: str, skills: list[str] = None):
    global _agent_config, _llm_instance
    _agent_config = {
        "config": config,
        "system_prompt": system_prompt,
        "skills": skills or [],
    }
    # 创建 LLM 实例用于流式调用
    from ...model import create_model
    _llm_instance = create_model(config)
    logger.info("[初始化] Agent 配置初始化完成 | LLM 流式已启用")


async def get_or_create_agent_for_session(session_id: str, username: str = "default") -> EasyAgent:
    global _session_agents, _agent_config

    if session_id in _session_agents:
        return _session_agents[session_id]

    if _agent_config is None:
        raise RuntimeError("Agent 配置未初始化")

    logger.info(f"[{session_id[-5:]}] 为会话创建 Agent 实例 | username={username} | session_id={session_id}")

    agent = EasyAgent(
        config=_agent_config["config"],
        system_prompt=_agent_config["system_prompt"],
        skills=_agent_config["skills"],
        username=username,
        session_id=session_id,
    )
    _session_agents[session_id] = agent
    logger.info(f"[{session_id[-5:]}] Agent 实例创建成功 | workspace: {agent.workspace_dir}")
    return agent


def remove_session_agent(session_id: str):
    global _session_agents
    if session_id in _session_agents:
        del _session_agents[session_id]
        logger.info(f"[{session_id[-5:]}] Agent 缓存已清除")


async def chat_stream_generator(
    request: ChatRequest,
    db: Database,
    agent: EasyAgent,
    session_id: str,
    message_id: str,
    username: str,
    http_request=None,
    parsed_content: str = None,
) -> AsyncGenerator[str, None]:
    start_time = time.time()
    sid = session_id[-5:] if session_id else "new"
    
    message_content = parsed_content or request.message
    
    logger.info(f"[{sid}] 开始流式响应 | message: {message_content[:50]}{'...' if len(message_content) > 50 else ''} | deep_think: {request.enable_deep_think} | use_knowledge_base: {request.use_knowledge_base} | 用户: {username}")

    def format_sse(data: dict) -> str:
        return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

    try:
        yield format_sse({"type": "start", "session_id": session_id})

        start_time = time.time()
        
        has_streaming = hasattr(agent.agent, 'astream')

        # 使用 DeepAgents 的流式接口(支持 skills 和工具调用)
        if has_streaming:
            logger.info(f"[{sid}] 🚀 使用 DeepAgents 流式接口(支持 skills)")
            
            messages = [HumanMessage(content=message_content)]
            full_response = ""
            current_thinking = ""
            thinking_started = False
            thinking_start_time = None
            thinking_end_time = None
            assistant_started = False
            current_step = 0  # 当前步骤序号
            current_step_thinking = ""  # 当前步骤的思考内容(每轮独立)
            tool_call_start_times = {}  # 记录每个工具调用的开始时间
            tool_call_step_map = {}  # 记录工具调用对应的步骤序号
            
            # 用于数据库持久化的收集器
            thinking_records = []  # [(step, content, duration), ...]
            tool_call_records = []  # [(tool_name, tool_call_id, args, result, success, duration, step), ...]
            tool_call_id_counter = 0  # 工具调用ID计数器
            
            # 临时存储工具调用信息(等待结果到来时合并)
            pending_tool_calls = {}  # tool_name -> {tool_call_id, arguments, step}
            
            # 使用 messages 模式获取逐 token 的流式输出
            accumulated_content = ""  # 累积的 AI 内容（包含思考标签）
            accumulated_thinking = ""  # 累积的思考内容
            accumulated_response = ""  # 累积的正式回复
            is_in_thinking = False  # 是否在思考标签内
            thinking_buffer = ""  # 思考内容缓冲区
            first_token_time = None  # 第一个 token 的时间
            msg_received_time = None  # 思考开始时间（遇到 <think> 时记录）
            content_start_time = None  # 正式内容开始时间
            total_tool_duration = 0  # 工具调用总时间
            is_after_tool_result = False  # 标记是否刚收到工具结果（用于过滤重复内容）
            tool_call_accumulated_args = {}  # 累积工具调用的完整参数 {tool_name: args_str}
            
            async for chunk in agent.agent.astream(
                {"messages": messages},
                stream_mode="messages",
                subgraphs=True,
                version="v2",
            ):
                if chunk["type"] != "messages":
                    continue
                
                token, metadata = chunk["data"]
                ns = chunk.get("ns", [])  # 命名空间
                
                # 只处理主 agent 的消息（忽略子 agent）
                is_subagent = any(s.startswith("tools:") for s in ns)
                if is_subagent:
                    continue
                
                # 处理 AI 内容 token（流式）
                content = getattr(token, 'content', '')
                if content:
                    content_str = str(content)
                    accumulated_content += content_str
                    
                    # 实时检测思考标签
                    import re
                    
                    # 检查是否有开始标签
                    if not is_in_thinking and '<think' in content_str.lower():
                        is_in_thinking = True
                        is_after_tool_result = False  # 进入思考说明新步骤开始
                        msg_received_time = time.time()  # 记录思考开始时间
                        current_step += 1  # 新的思考轮次，递增 step
                        logger.info(f"[{sid}] 🤔 Step {current_step} 思考开始")
                        # 发送思考开始事件
                        yield format_sse({
                            "type": "thinking_start",
                            "content": "",
                            "step": current_step,
                        })
                    
                    # 检查是否有结束标签
                    if is_in_thinking and '</think' in content_str.lower():
                        # 提取结束标签前的思考内容
                        end_match = re.search(r'</think[^>]*>', content_str, re.IGNORECASE)
                        if end_match:
                            thinking_part = content_str[:end_match.start()]
                            response_part = content_str[end_match.end():]
                            
                            # 发送剩余思考内容
                            if thinking_part.strip():
                                yield format_sse({
                                    "type": "thinking",
                                    "content": thinking_part,
                                    "step": current_step,
                                })
                                accumulated_thinking += thinking_part
                            
                            # 计算思考时间
                            thinking_end_time = time.time()
                            thinking_duration = thinking_end_time - thinking_start_time if thinking_start_time else 0
                            
                            # 发送思考结束事件
                            thinking_duration = time.time() - msg_received_time
                            thinking_clean = accumulated_thinking.replace('<think>', '').replace('</think>', '').replace('<think ', '').replace('</think ', '').strip()
                            logger.info(f"[{sid}] 🤔 Step {current_step} 思考完成 | 耗时: {thinking_duration:.2f}s | 内容长度: {len(thinking_clean)} | 内容: {thinking_clean[:500]}")
                            yield format_sse({
                                "type": "thinking_end",
                                "duration": round(thinking_duration, 2),
                                "step": current_step,
                            })
                            
                            is_in_thinking = False
                            
                            # 发送正式回复内容
                            if response_part.strip():
                                if content_start_time is None:
                                    content_start_time = time.time()  # 记录正式内容开始时间
                                yield format_sse({
                                    "type": "content",
                                    "content": response_part,
                                })
                                accumulated_response += response_part
                    elif is_in_thinking:
                        # 在思考标签内，发送思考内容
                        accumulated_thinking += content_str
                        yield format_sse({
                            "type": "thinking",
                            "content": content_str,
                            "step": current_step,
                        })
                    else:
                        # 正式回复内容
                        if is_after_tool_result:
                            logger.info(f"[{sid}] ⏭️ Step {current_step} 跳过重复 content | 长度: {len(content_str)}")
                            is_after_tool_result = False
                        else:
                            accumulated_response += content_str
                            logger.info(f"[{sid}] 📤 Step {current_step} 正式内容\n内容: {accumulated_response[:500]}")
                            yield format_sse({
                                "type": "content",
                                "content": content_str,
                            })
                
                # 处理工具调用 chunks（流式累积参数）
                if hasattr(token, "tool_call_chunks") and token.tool_call_chunks:
                    for tc_chunk in token.tool_call_chunks:
                        tool_name_from_chunk = tc_chunk.get("name")
                        args_data = tc_chunk.get("args")
                        
                        if tool_name_from_chunk:
                            # 新的工具调用开始
                            tool_name = tool_name_from_chunk
                            # 记录工具调用开始时间
                            tool_call_start_times[tool_name] = time.time()
                            tool_call_step_map[tool_name] = current_step
                            # 初始化累积参数
                            if isinstance(args_data, dict):
                                tool_call_accumulated_args[tool_name] = args_data.copy()
                            elif isinstance(args_data, str) and args_data:
                                # 如果是字符串，尝试解析为 JSON 对象
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
                            # 续接参数（同一个工具调用的后续 chunk）
                            if isinstance(args_data, dict):
                                tool_call_accumulated_args[tool_name].update(args_data)
                            elif isinstance(args_data, str) and args_data:
                                # 尝试解析并合并
                                try:
                                    parsed_args = json.loads(args_data)
                                    if isinstance(parsed_args, dict):
                                        tool_call_accumulated_args[tool_name].update(parsed_args)
                                    else:
                                        # 如果解析后不是 dict，直接拼接
                                        current_raw = tool_call_accumulated_args[tool_name].get("raw", "")
                                        tool_call_accumulated_args[tool_name]["raw"] = current_raw + args_data
                                except json.JSONDecodeError:
                                    current_raw = tool_call_accumulated_args[tool_name].get("raw", "")
                                    tool_call_accumulated_args[tool_name]["raw"] = current_raw + args_data
                    
                    # 每次收到 chunk 都更新并发送 tool_call 事件（参数会逐步完整）
                    if tool_name and tool_name in tool_call_accumulated_args:
                        full_args = tool_call_accumulated_args[tool_name]
                        
                        if "raw" in full_args and len(full_args) == 1:
                            log_args = full_args["raw"]
                        else:
                            log_args = json.dumps(full_args, ensure_ascii=False)
                        
                        logger.info(f"[{sid}] 🔧 Step {current_step} 工具调用: {tool_name} | 参数: {log_args[:2000]}")
                        is_after_tool_result = True
                        yield format_sse({
                            "type": "tool_call",
                            "tool_name": tool_name,
                            "arguments": full_args,
                            "step": current_step,
                        })
                
                # 处理工具结果
                if token.type == "tool":
                    tool_name = getattr(token, "name", "") or ""
                    result_content = str(getattr(token, "content", ""))
                    
                    # 标记刚收到工具结果，后续 AI 可能会重复输出这些内容
                    is_after_tool_result = True
                    
                    # 判断工具执行是否成功
                    tool_success = True
                    
                    # 检查 exit code（格式: Exit code: 0 或 Exit code: 1）
                    exit_code_match = re.search(r'Exit code:\s*(\d+)', result_content)
                    if exit_code_match:
                        exit_code = int(exit_code_match.group(1))
                        if exit_code != 0:
                            tool_success = False
                    
                    # 检查常见的错误关键词
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
                    
                    # 计算工具执行时间
                    tool_duration = 0
                    if tool_name in tool_call_start_times:
                        tool_duration = time.time() - tool_call_start_times[tool_name]
                        del tool_call_start_times[tool_name]
                        total_tool_duration += tool_duration  # 累加工具调用总时间
                    
                    # 获取工具调用时的完整参数
                    tool_args = tool_call_accumulated_args.pop(tool_name, {}) if tool_name in tool_call_accumulated_args else {}
                    if not isinstance(tool_args, dict):
                        tool_args = {}
                    elif "raw" in tool_args and len(tool_args) == 1:
                        # 如果只有 raw 字段，尝试解析
                        try:
                            parsed = json.loads(tool_args["raw"])
                            if isinstance(parsed, dict):
                                tool_args = parsed
                        except json.JSONDecodeError:
                            pass
                    
                    result_len = len(result_content)
                    result_preview = result_content[:500] if result_content else ""
                    
                    # 如果只有 raw 字段，说明 JSON 解析失败，直接打印 raw 内容
                    if "raw" in tool_args and len(tool_args) == 1:
                        log_args = tool_args["raw"]
                    else:
                        log_args = json.dumps(tool_args, ensure_ascii=False)
                    
                    logger.info(f"[{sid}] Step {current_step} 工具调用: {tool_name} | 参数: {log_args[:2000]} | 耗时: {tool_duration:.2f}s | 内容长度: {result_len} | 调用结果: {'成功' if tool_success else '失败'} | 结果: {result_preview}")
                    
                    # 保存到工具调用记录（用于数据库持久化）
                    tool_call_id = f"tool-{tool_name}-{len(tool_call_records)}"
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
                        "arguments": tool_args,
                        "result": result_content,
                        "success": tool_success,
                        "duration": round(tool_duration, 2),
                        "step": current_step,
                    })
            
            # 流式结束后，计算并保存思考记录
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

            # 发送回复结束事件
            if accumulated_response:
                yield format_sse({
                    "type": "content_end",
                    "content": "",
                })
                content_end_time = time.time()

            elapsed_time = time.time() - start_time
            
            # 计算各阶段时间
            thinking_time = round(thinking_end_time - thinking_start_time, 2) if thinking_start_time and thinking_end_time else 0
            content_time = round(content_end_time - content_start_time, 2) if content_start_time and content_end_time else 0
            
            # 确保发送思考结束事件(如果没有正常关闭)
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

            # 保存到数据库(包含 thinking 和 tool_calls 字段)
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
            }
            db.add_message(session_id, assistant_message)

            yield format_sse({
                "type": "done",
                "data": {
                    "session_id": session_id,
                    "elapsed_time": round(elapsed_time, 2),
                },
            })

        # 非流式模式(备用)
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

            yield format_sse({
                "type": "done",
                "data": {
                    "session_id": session_id,
                    "elapsed_time": round(elapsed_time, 2),
                },
            })

    except asyncio.CancelledError:
        logger.info(f"[{sid}] ❌ 请求被取消")
        yield format_sse({"type": "error", "content": "请求被取消"})
    except Exception as e:
        logger.error(f"[{sid}] ❌ 聊天异常: {str(e)}", exc_info=True)
        yield format_sse({"type": "error", "content": f"处理失败: {str(e)}"})
