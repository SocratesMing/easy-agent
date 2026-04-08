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
            token_count = 0  # 计数器
            is_in_thinking = False  # 是否在思考标签内
            thinking_buffer = ""  # 思考内容缓冲区
            first_token_time = None  # 第一个 token 的时间
            msg_received_time = None  # 思考开始时间（遇到 <think> 时记录）
            
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
                    
                    # 记录第一个 token 的时间
                    if first_token_time is None:
                        first_token_time = time.time()
                        logger.info(f"[{sid}] ⚡ 首 token 延迟: {first_token_time - start_time:.2f}s")
                    
                    accumulated_content += content_str
                    token_count += 1
                    
                    # 实时检测思考标签
                    import re
                    
                    # 检查是否有开始标签
                    if not is_in_thinking and '<think' in content_str.lower():
                        is_in_thinking = True
                        msg_received_time = time.time()  # 记录思考开始时间
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
                            
                            # 发送思考结束事件
                            thinking_duration = time.time() - msg_received_time
                            yield format_sse({
                                "type": "thinking_end",
                                "duration": round(thinking_duration, 2),
                                "step": current_step,
                            })
                            
                            is_in_thinking = False
                            
                            # 发送正式回复内容
                            if response_part.strip():
                                yield format_sse({
                                    "type": "content",
                                    "content": response_part,
                                })
                                accumulated_response += response_part
                    elif is_in_thinking:
                        # 在思考标签内，发送思考内容
                        yield format_sse({
                            "type": "thinking",
                            "content": content_str,
                            "step": current_step,
                        })
                        accumulated_thinking += content_str
                    else:
                        # 正式回复内容
                        logger.debug(f"[{sid}] 📤 发送 content 事件 | 长度: {len(content_str)} | 内容: {content_str[:50]}")
                        yield format_sse({
                            "type": "content",
                            "content": content_str,
                        })
                        accumulated_response += content_str
                
                # 处理工具调用 chunks
                if hasattr(token, "tool_call_chunks") and token.tool_call_chunks:
                    for tc_chunk in token.tool_call_chunks:
                        if tc_chunk.get("name"):
                            tool_name = tc_chunk['name']
                            # 记录工具调用开始时间
                            tool_call_start_times[tool_name] = time.time()
                            
                            logger.info(f"[{sid}] 🔧 工具调用: {tool_name}")
                            yield format_sse({
                                "type": "tool_call",
                                "tool_name": tool_name,
                                "arguments": tc_chunk.get("args", {}),
                                "step": current_step,
                            })
                
                # 处理工具结果
                if token.type == "tool":
                    tool_name = getattr(token, "name", "") or ""
                    result_content = str(getattr(token, "content", ""))
                    
                    # 判断工具执行是否成功
                    tool_success = True
                    exit_code_match = re.search(r'Exit code:\s*(\d+)', result_content)
                    if exit_code_match:
                        exit_code = int(exit_code_match.group(1))
                        tool_success = exit_code == 0
                    
                    # 计算工具执行时间
                    tool_duration = 0
                    if tool_name in tool_call_start_times:
                        tool_duration = time.time() - tool_call_start_times[tool_name]
                        del tool_call_start_times[tool_name]
                    
                    logger.info(f"[{sid}] {'✅' if tool_success else '❌'} 工具结果: {tool_name} | 成功: {tool_success} | 耗时: {tool_duration:.2f}s")
                    
                    # 保存到工具调用记录（用于数据库持久化）
                    tool_call_id = f"tool-{tool_name}-{len(tool_call_records)}"
                    tool_call_records.append((
                        tool_name,
                        tool_call_id,
                        {},  # arguments（从 pending_tool_calls 获取）
                        result_content,
                        tool_success,
                        round(tool_duration, 2),
                        current_step,
                    ))
                    
                    yield format_sse({
                        "type": "tool_result",
                        "tool_name": tool_name,
                        "result": result_content,
                        "success": tool_success,
                        "duration": round(tool_duration, 2),
                        "step": current_step,
                    })
            
            # 流式结束后，保存思考记录
            if accumulated_thinking.strip():
                thinking_duration = time.time() - msg_received_time
                db.record_thinking(
                    session_id=session_id,
                    message_id=message_id,
                    step=current_step,
                    content=accumulated_thinking.strip(),
                    duration=round(thinking_duration, 2),
                )
                logger.info(f"[{sid}] 💾 思考记录已保存 | 长度: {len(accumulated_thinking)}")

            # 发送回复结束事件
            if accumulated_response:
                yield format_sse({
                    "type": "content_end",
                    "content": "",
                })

            elapsed_time = time.time() - start_time
            generation_time = time.time() - first_token_time if first_token_time else 0
            
            # 确保发送思考结束事件(如果没有正常关闭)
            if thinking_started and thinking_start_time and thinking_end_time is None:
                thinking_duration = time.time() - thinking_start_time
                logger.info(f"[{sid}] 🤔 Step {current_step} 思考结束(异常) | 耗时: {thinking_duration:.2f}s | 长度: {len(accumulated_thinking)}")
                yield format_sse({
                    "type": "thinking_end",
                    "duration": round(thinking_duration, 2),
                    "step": current_step,
                })

            logger.info(f"[{sid}] ✅ 流式响应完成 | 总步骤: {current_step} | 请求耗时: {elapsed_time:.2f}s | 生成耗时: {generation_time:.2f}s | Token数: {token_count} | 思考长度: {len(accumulated_thinking)} | 回复长度: {len(accumulated_response)} 字符")
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
