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

from ...agent import WukongAgent
from ...config import Config
from ..database import Database, SessionModel, get_database
from ..models import ChatRequest

logger = logging.getLogger("wukong_agent.chat_service")

_session_agents: dict[str, WukongAgent] = {}
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


async def get_or_create_agent_for_session(session_id: str, username: str = "default") -> WukongAgent:
    global _session_agents, _agent_config

    if session_id in _session_agents:
        return _session_agents[session_id]

    if _agent_config is None:
        raise RuntimeError("Agent 配置未初始化")

    logger.info(f"[{session_id[-5:]}] 为会话创建 Agent 实例 | username={username}")

    agent = WukongAgent(
        config=_agent_config["config"],
        system_prompt=_agent_config["system_prompt"],
        skills=_agent_config["skills"],
    )
    _session_agents[session_id] = agent
    logger.info(f"[{session_id[-5:]}] Agent 实例创建成功")
    return agent


def remove_session_agent(session_id: str):
    global _session_agents
    if session_id in _session_agents:
        del _session_agents[session_id]
        logger.info(f"[{session_id[-5:]}] Agent 缓存已清除")


async def chat_stream_generator(
    request: ChatRequest,
    db: Database,
    agent: WukongAgent,
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
            thinking_buffer = ""
            tool_call_start_times = {}  # 记录每个工具调用的开始时间
            
            async for event in agent.agent.astream({"messages": messages}):
                if not isinstance(event, dict):
                    continue
                
                for node_name, node_output in event.items():
                    if not isinstance(node_output, dict) or 'messages' not in node_output:
                        continue
                    
                    msgs_list = node_output['messages']
                    if not isinstance(msgs_list, list):
                        try:
                            msgs_list = list(msgs_list)
                        except:
                            continue
                    
                    for msg in msgs_list:
                        msg_type = getattr(msg, 'type', None)
                        if msg_type is None and isinstance(msg, dict):
                            msg_type = msg.get('type')
                        
                        content = getattr(msg, 'content', '')
                        if not content and isinstance(msg, dict):
                            content = msg.get('content', '')
                        
                        if msg_type == 'ai' and content:
                            # 处理 AI 消息(可能包含思考标签和工具调用)
                            import re
                            
                            # 检查工具调用
                            additional_kwargs = getattr(msg, 'additional_kwargs', {})
                            if not additional_kwargs and isinstance(msg, dict):
                                additional_kwargs = msg.get('additional_kwargs', {})
                            
                            tool_calls = []
                            if additional_kwargs.get('tool_calls'):
                                for tc in additional_kwargs['tool_calls']:
                                    tc_name = getattr(tc, 'name', '') or tc.get('name', '')
                                    tc_args = getattr(tc, 'args', {}) or tc.get('args', {})
                                    tool_calls.append((tc_name, tc_args))
                            
                            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                                for tc in msg.tool_calls:
                                    tc_name = getattr(tc, 'name', '') or tc.get('name', '')
                                    tc_args = getattr(tc, 'args', {}) or tc.get('args', {})
                                    tool_calls.append((tc_name, tc_args))
                            
                            # 发送工具调用事件
                            for tc_name, tc_args in tool_calls:
                                logger.info(f"[{sid}] 🔧 工具调用: {tc_name}")
                                logger.info(f"[{sid}] 📝 工具参数: {json.dumps(tc_args, ensure_ascii=False)[:200]}")
                                tool_call_start_times[tc_name] = time.time()  # 记录工具调用开始时间
                                yield format_sse({
                                    "type": "tool_call",
                                    "tool_name": tc_name,
                                    "arguments": tc_args,
                                })
                            
                            # 检查思考标签 - 每次都处理,包括工具执行过程中的思考
                            think_pattern = r'<think[^>]*>([\s\S]*?)</think\s*>'
                            matches = re.findall(think_pattern, content, re.IGNORECASE)
                            
                            if matches:
                                # 有思考内容(可能是首次思考,也可能是工具执行中的思考)
                                thinking_start_time = time.time()
                                thinking_started = True
                                
                                for thinking_text in matches:
                                    if thinking_text.strip():
                                        thinking_buffer += thinking_text.strip()
                                
                                logger.info(f"[{sid}] 🤔 思考开始")
                                yield format_sse({
                                    "type": "thinking_start",
                                    "content": "",
                                })
                                
                                # 发送思考内容
                                yield format_sse({
                                    "type": "thinking",
                                    "content": thinking_buffer,
                                })
                                
                                # 思考结束
                                thinking_duration = time.time() - thinking_start_time
                                thinking_end_time = time.time()
                                logger.info(f"[{sid}] 🤔 思考结束 | 耗时: {thinking_duration:.2f}s | 长度: {len(thinking_buffer)}")
                                logger.info(f"[{sid}] 🤔 思考内容:\n{thinking_buffer}")
                                yield format_sse({
                                    "type": "thinking_end",
                                    "duration": round(thinking_duration, 2),
                                })
                                
                                # 提取正式回复(移除思考标签后的内容)
                                response_text = re.sub(think_pattern, '', content, flags=re.IGNORECASE).strip()
                                if response_text:
                                    assistant_started = True
                                    logger.info(f"[{sid}] 💬 回复开始")
                                    yield format_sse({
                                        "type": "assistant_start",
                                        "content": "",
                                    })
                                    
                                    full_response = response_text
                                    yield format_sse({
                                        "type": "content",
                                        "content": response_text,
                                    })
                            elif not tool_calls:
                                # 没有思考标签和工具调用,直接是回复内容
                                if not assistant_started:
                                    assistant_started = True
                                    logger.info(f"[{sid}] 💬 回复开始")
                                    yield format_sse({
                                        "type": "assistant_start",
                                        "content": "",
                                    })
                                
                                full_response += content
                                yield format_sse({
                                    "type": "content",
                                    "content": content,
                                })
                        
                        elif msg_type == 'tool':
                            # 工具调用结果
                            tool_name = getattr(msg, 'name', '')
                            if not tool_name and isinstance(msg, dict):
                                tool_name = msg.get('name', '')
                            
                            tool_content = getattr(msg, 'content', '')
                            if not tool_content and isinstance(msg, dict):
                                tool_content = msg.get('content', '')
                            
                            result_str = str(tool_content)
                            result_preview = result_str[:1000] if len(result_str) > 1000 else result_str
                            
                            # 计算工具执行耗时
                            tool_duration = None
                            if tool_name in tool_call_start_times:
                                tool_duration = round(time.time() - tool_call_start_times[tool_name], 2)
                                logger.info(f"[{sid}] ✅ 工具结果: {tool_name} | 耗时: {tool_duration}s | 长度: {len(result_str)} 字符")
                                del tool_call_start_times[tool_name]  # 清除记录
                            else:
                                logger.info(f"[{sid}] ✅ 工具结果: {tool_name} | 长度: {len(result_str)} 字符")
                            
                            yield format_sse({
                                "type": "tool_result",
                                "tool_name": tool_name,
                                "result": result_preview,
                                "success": True,
                                "duration": tool_duration,
                            })

            elapsed_time = time.time() - start_time
            
            # 确保发送思考结束事件(如果没有正常关闭)
            if thinking_started and thinking_start_time and thinking_end_time is None:
                thinking_duration = time.time() - thinking_start_time
                logger.info(f"[{sid}] 🤔 思考结束(异常) | 耗时: {thinking_duration:.2f}s | 长度: {len(thinking_buffer)}")
                logger.info(f"[{sid}] 🤔 思考内容:\n{thinking_buffer}")
                yield format_sse({
                    "type": "thinking_end",
                    "duration": round(thinking_duration, 2),
                })

            logger.info(f"[{sid}] ✅ 流式响应完成 | 总耗时: {elapsed_time:.2f}s | 回复长度: {len(full_response)} 字符")
            logger.info(f"[{sid}] 💬 回复内容:\n{full_response}")

            # 保存到数据库
            assistant_message = {
                "role": "assistant",
                "content": full_response,
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
