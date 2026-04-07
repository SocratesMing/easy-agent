"""Chat service for streaming responses"""

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime
from typing import AsyncGenerator

from ...agent import WukongAgent
from ...config import Config
from ..database import Database, SessionModel, get_database
from ..models import ChatRequest

logger = logging.getLogger("wukong_agent.chat_service")

_session_agents: dict[str, WukongAgent] = {}
_agent_config: dict = None


def init_agent_config(config: Config, system_prompt: str, skills: list[str] = None):
    global _agent_config
    _agent_config = {
        "config": config,
        "system_prompt": system_prompt,
        "skills": skills or [],
    }
    logger.info("[初始化] Agent 配置初始化完成")


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

        if has_streaming:
            messages = [{"role": "user", "content": message_content}]
            full_response = ""
            current_thinking = ""
            step_count = 0
            tool_calls_count = 0
            
            thinking_started = False
            thinking_start_time = None
            assistant_started = False

            async for event in agent.agent.astream({"messages": messages}):
                if not isinstance(event, dict):
                    continue

                for node_name, node_output in event.items():
                    if not isinstance(node_output, dict) or 'messages' not in node_output:
                        continue

                    msgs = node_output['messages']
                    msgs_list = []
                    
                    if hasattr(msgs, '__iter__') and not isinstance(msgs, str):
                        try:
                            msgs_list = list(msgs)
                        except Exception:
                            if hasattr(msgs, 'value'):
                                msgs_list = [msgs.value]
                    elif isinstance(msgs, list):
                        msgs_list = msgs

                    for msg in msgs_list:
                        msg_type = getattr(msg, 'type', None)
                        if msg_type is None and isinstance(msg, dict):
                            msg_type = msg.get('type')

                        content = getattr(msg, 'content', '')
                        if not content and isinstance(msg, dict):
                            content = msg.get('content', '')

                        additional_kwargs = getattr(msg, 'additional_kwargs', {})
                        if not additional_kwargs and isinstance(msg, dict):
                            additional_kwargs = msg.get('additional_kwargs', {})

                        if msg_type == 'ai':
                            thinking_text = ""
                            response_text = ""

                            if isinstance(content, str):
                                import re
                                pattern = r'<think[^>]*>([\s\S]*?)</think\s*>'
                                matches = re.findall(pattern, content, re.IGNORECASE)
                                
                                for match in matches:
                                    if match.strip():
                                        thinking_text += match.strip() + "\n"
                                
                                cleaned_content = re.sub(pattern, '', content, flags=re.IGNORECASE).strip()
                                
                                if cleaned_content:
                                    response_text = cleaned_content
                                elif not thinking_text:
                                    response_text = content

                            if thinking_text.strip():
                                if not thinking_started:
                                    thinking_start_time = time.time()
                                    thinking_started = True
                                    logger.info(f"[{sid}] 🤔 思考开始")
                                    
                                yield format_sse({
                                    "type": "thinking_start",
                                    "content": "",
                                })
                                for line in thinking_text.strip().split('\n'):
                                    if line.strip():
                                        logger.info(f"[{sid}] 💭 {line.strip()}")
                                    yield format_sse({
                                        "type": "thinking",
                                        "content": line,
                                    })
                                
                                current_thinking = thinking_text.strip()
                                logger.info(f"[{sid}] 🤔 思考完成 | 总长度: {len(thinking_text.strip())} 字符")

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

                            for tc_name, tc_args in tool_calls:
                                tool_calls_count += 1
                                step_count += 1
                                tool_call_id = str(uuid.uuid4())
                                
                                logger.info(f"[{sid}] 📋 步骤 {step_count} | 🔧 工具调用 [{tool_calls_count}] | name: {tc_name}")
                                logger.info(f"[{sid}] 🔧 工具参数: {json.dumps(tc_args, ensure_ascii=False)}")
                                
                                yield format_sse({
                                    "type": "tool_call",
                                    "tool_name": tc_name,
                                    "arguments": tc_args,
                                })
                                db.record_tool_call(
                                    session_id=session_id,
                                    message_id=message_id,
                                    tool_name=tc_name,
                                    tool_call_id=tool_call_id,
                                    arguments=tc_args,
                                )

                            if response_text.strip():
                                if not assistant_started:
                                    assistant_started = True
                                    logger.info(f"[{sid}] 💬 回复开始")
                                    
                                full_response = response_text.strip()
                                yield format_sse({
                                    "type": "assistant_start",
                                    "content": "",
                                })
                                yield format_sse({
                                    "type": "content",
                                    "content": response_text.strip(),
                                })

                        elif msg_type == 'tool':
                            tool_content = getattr(msg, 'content', '')
                            if not tool_content and isinstance(msg, dict):
                                tool_content = msg.get('content', '')
                            
                            result_str = str(tool_content)
                            result_preview = result_str[:1000]
                            
                            logger.info(f"[{sid}] ✅ 工具结果 | 长度: {len(result_str)} 字符")
                            logger.info(f"[{sid}] 📄 结果内容: {result_preview}")
                            
                            yield format_sse({
                                "type": "tool_result",
                                "result": result_preview,
                                "success": True,
                            })
                            
                            step_count += 1
                            logger.info(f"[{sid}] 📊 步骤 {step_count} 完成")

            elapsed_time = time.time() - start_time
            
            if thinking_started and thinking_start_time:
                thinking_duration = time.time() - thinking_start_time
                logger.info(f"[{sid}] 🤔 思考结束 | 耗时: {thinking_duration:.2f}s")

            logger.info(f"[{sid}] ✅ 流式响应完成 | 总耗时: {elapsed_time:.2f}s | 工具调用次数: {tool_calls_count} | 步骤数: {step_count}")

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
