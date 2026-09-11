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
  token_usage      {input_tokens, output_tokens, reasoning_tokens, context_length, auto_compress_tokens}
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

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.types import Command

from ..agent import EasyAgent, resolve_context_length
from ..db import Database
from .stream_processor import StreamProcessor
from ..models.api import ChatRequest
from ..utils.session_logger import SessionLogger
from .agent_manager import get_agent_config

logger = logging.getLogger("easy_agent.chat_service")


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


def format_sse(data: dict) -> str:
    """将事件数据序列化为 SSE 格式字符串。"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def tool_call_records_to_dicts(records: list, result_limit: int | None = None) -> list[dict]:
    """将工具调用记录转为可序列化 dict 列表。

    records 中每个元素为 7 元组：
        [0]=tool_name, [1]=tool_call_id, [2]=arguments, [3]=result,
        [4]=success, [5]=duration, [6]=step
    result_limit 控制 result 字段保留的字符数（None 表示不截断，默认不截断：
    截断会导致下一轮重建上下文时 token 明显缩水）。
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
                "approval_status": tc[7] if len(tc) > 7 else None,
            }
        )
    return dicts


def _historical_tool_records(message: dict) -> list[dict]:
    records = message.get("tool_calls") or []
    if records:
        return [record for record in records if isinstance(record, dict)]
    return [
        block
        for block in (message.get("blocks") or [])
        if isinstance(block, dict) and block.get("type") == "tool_call"
    ]


def _tool_call_tuples(blocks: list, *, with_approval: bool = False) -> list[tuple]:
    """从 StreamProcessor 的 blocks 中提取工具调用元组（供持久化使用）。

    chat / resume 两条流式链路原本各自定义了一份完全相同的闭包，现统一到此处。
    ``with_approval=True`` 时在末尾追加第 8 项 ``approval_status``（HITL 恢复流程需要）。
    """
    if with_approval:
        return [
            (b.get("tool_name", ""), b.get("tool_call_id", ""),
             b.get("arguments", {}), b.get("result", ""),
             b.get("success", True), b.get("duration"),
             b.get("step", 0), b.get("approval_status"))
            for b in blocks if b.get("type") == "tool_call"
        ]
    return [
        (b.get("tool_name", ""), b.get("tool_call_id", ""),
         b.get("arguments", {}), b.get("result", ""),
         b.get("success", True), b.get("duration"),
         b.get("step", 0))
        for b in blocks if b.get("type") == "tool_call"
    ]


def _persist_incremental(
    proc, *, db, session_id: str, sid: str, start_time: float
) -> None:
    """流式过程中增量持久化当前 assistant 消息（正文 / 思考 / 工具记录）。

    chat 与 resume 共用；内部吞掉异常——持久化失败不应中断流式输出。
    """
    try:
        msg = build_assistant_message_dict(
            content=proc.accumulated_response,
            thinking=proc.accumulated_thinking,
            thinking_duration=None,
            tool_call_records=_tool_call_tuples(proc.blocks),
            blocks=proc.blocks,
            input_tokens=proc.last_usage.get("input_tokens", 0),
            output_tokens=proc.last_usage.get("output_tokens", 0),
            reasoning_tokens=proc.last_usage.get("reasoning_tokens", 0),
            context_tokens=proc.last_context_tokens,
            elapsed_time=time.time() - start_time,
            step_count=proc.current_step,
        )
        db.update_last_assistant_message(session_id, msg)
        db.update_last_assistant_message_row(session_id, msg)
    except Exception as e:
        logger.warning(f"[{sid}] 增量持久化失败: {e}")


def _handle_tool_result_event(
    ev, *, db, session_id, message_id, session_logger, agent, sid
) -> None:
    """工具执行结果落库：工具调用记录 + 会话日志 + 生成文件登记。

    chat 与 resume 共用（原为两份逐行相同的闭包）。
    """
    tool_name = ev.get("tool_name", "")
    tool_call_id = ev.get("tool_call_id", "")
    tool_args = ev.get("arguments", {})
    result_content = ev.get("result", "")
    success = ev.get("success", True)
    tool_duration = ev.get("duration", 0)
    step = ev.get("step", 0)
    if message_id:
        try:
            db.record_tool_call(
                session_id=session_id, message_id=message_id,
                tool_name=tool_name, tool_call_id=tool_call_id,
                arguments=tool_args if isinstance(tool_args, dict) else {},
                result=str(result_content)[:5000] if result_content is not None else None,
                success=success, duration=tool_duration, step=step,
            )
        except Exception as e:
            logger.warning(f"[{sid}] 持久化工具调用记录失败: {e}")
    if session_logger:
        try:
            session_logger.log_tool_call(
                tool_name=tool_name, tool_call_id=tool_call_id,
                arguments=tool_args,
                result=str(result_content)[:5000],
                success=success, duration=tool_duration, step=step,
                message_id=message_id,
            )
        except Exception:
            pass
    if tool_name in ("write_file", "write_tool"):
        try:
            file_args = tool_args if isinstance(tool_args, dict) else {}
            filename = (
                file_args.get("file_name")
                or file_args.get("path")
                or file_args.get("file_path", "")
            )
            if filename:
                base_name = os.path.basename(filename) if filename else "unknown"
                ext = os.path.splitext(base_name)[1].lstrip(".") or "txt"
                db.add_generated_file(
                    session_id=session_id, message_id=message_id,
                    filename=base_name,
                    file_path=str(agent.workspace_dir / filename)
                    if not os.path.isabs(filename) else filename,
                    file_type=ext, size=0,
                )
                logger.info(f"[{sid}] 📄 记录生成文件: {base_name}")
        except Exception as e:
            logger.warning(f"[{sid}] 记录生成文件失败: {e}")


def _persist_thinking_event(
    ev, *, proc, db, session_id, message_id, sid
) -> None:
    """把指定 step 的思考内容落库（chat 与 resume 共用）。"""
    step = ev.get("step", 0)
    duration = ev.get("duration", 0)
    for blk in reversed(proc.blocks):
        if blk.get("type") == "thinking" and blk.get("step") == step:
            content = (blk.get("content", "") or "").strip()
            if content and message_id:
                try:
                    db.record_thinking(
                        session_id=session_id, message_id=message_id,
                        step=step, content=content[:10000], duration=duration,
                    )
                except Exception as e:
                    logger.warning(f"[{sid}] 持久化思考记录失败: {e}")
            break



def _assistant_content_message(message: dict, provider: str) -> AIMessage:
    """重建历史 assistant 消息：**只恢复正文，不回灌思考内容**。

    历史思考故意不注入上下文，原因有二：
    1. OpenAI 兼容通道（deepseek / ark / glm / ...）的请求侧不会序列化
       ``reasoning_content``——langchain_openai 的 ``_convert_message_to_dict``
       只输出 content/role/name/tool_calls/function_call，即使写进
       ``additional_kwargs`` 也会被丢弃，等于白写；
    2. 若改用 ``<think>…</think>`` 文本回灌，历史思考会被当成正文重复占用
       token，还可能干扰模型对自身输出的理解，且思考里出现 ``</think>``
       字面量时会破坏标签配对。

    Anthropic 协议在跨轮历史中允许省略 thinking block，故此处对所有 provider
    统一丢弃。``provider`` 参数保留以兼容既有调用方（后续如需按 provider
    差异化可在此基础上分支）。
    """
    return AIMessage(content=str(message.get("content") or ""))


def _hitl_interrupt_items(state) -> list[tuple[str | None, dict]]:
    """提取 LangGraph 状态中的 HITL interrupt ID 与请求负载。"""
    items: list[tuple[str | None, dict]] = []
    for task_item in getattr(state, "tasks", None) or []:
        for interrupt_item in getattr(task_item, "interrupts", None) or []:
            interrupt_id = (
                getattr(interrupt_item, "id", None)
                or getattr(interrupt_item, "interrupt_id", None)
            )
            value = getattr(interrupt_item, "value", None)
            if isinstance(value, dict):
                items.append((interrupt_id, value))
    return items


def _extract_hitl_request(graph_state) -> dict | None:
    """取出待审批的 HITL 中断负载；无中断时返回 None。

    **兼容层**：``state.tasks[*].interrupts[0].value`` 及其内部键
    （``action_requests`` / ``review_configs`` / ``action_name``）属于
    deepagents / langchain 的**内部结构**，不在公开契约内——官方对该中间件只承诺
    ``Command(resume={"decisions": ...})``。业务层统一经本函数取值，
    框架升级时只需调整这一处，而不是散落在两条流式链路里各改一遍。
    """
    if not getattr(graph_state, "next", None):
        return None
    items = _hitl_interrupt_items(graph_state)
    return items[0][1] if items else None


def _hitl_action_names(hitl_request: dict) -> set[str]:
    """待审批的工具名集合（兼容 ActionRequest 为 dict 或对象两种形态）。"""
    return {
        (ar.get("name", "") if isinstance(ar, dict) else getattr(ar, "name", ""))
        for ar in (hitl_request.get("action_requests") or [])
    }


def _hitl_review_config_map(hitl_request: dict) -> dict:
    """把中断负载里的 ``review_configs`` 转为 ``{action_name: config}``。"""
    return {
        cfg.get("action_name"): cfg
        for cfg in (hitl_request.get("review_configs") or [])
    }


def hanging_hitl_tool_count(state) -> int:
    """统计当前所有 HITL interrupt 中挂起的工具调用数量。"""
    return sum(
        len(value.get("action_requests") or [])
        for _, value in _hitl_interrupt_items(state)
    )


def normalize_resume_decisions(
    decisions: list[dict], *, hanging_tool_count: int
) -> list[dict]:
    """将前端的全局审批决策扩展为与挂起工具数一致的决策列表。

    前端目前一次点击只发送一个 decision。LangChain HITL 要求 decision 数量
    必须与 action_requests 数量一致；数量已匹配或语义不明确时不做猜测。
    """
    if hanging_tool_count <= 0 or len(decisions) != 1:
        return decisions
    return [dict(decisions[0]) for _ in range(hanging_tool_count)]


def build_context_messages(
    history_messages: list[dict], current_message: str, provider: str = ""
) -> list:
    """将持久化会话历史重建为完整的 LangChain 消息链。

    旧实现只恢复用户/助手正文，导致下一轮模型输入不包含上一轮工具调用与结果。
    这里按 step 恢复 AIMessage(tool_calls=...) 与对应 ToolMessage；无工具记录的
    旧会话回退为正文消息。

    注意：历史 **thinking 不回灌上下文**（详见 ``_assistant_content_message``），
    思考仅用于前端展示与持久化。
    """
    context_messages: list = []
    provider = (provider or "").lower()

    for message in history_messages:
        if message.get("role") == "user":
            context_messages.append(
                HumanMessage(content=str(message.get("content") or ""))
            )
            continue
        if message.get("role") != "assistant":
            continue

        records = _historical_tool_records(message)
        grouped_records: dict[int, list[dict]] = {}
        step_order: list[int] = []
        for index, record in enumerate(records):
            step = record.get("step") or 0
            if step not in grouped_records:
                grouped_records[step] = []
                step_order.append(step)
            tool_call_id = str(record.get("tool_call_id") or f"historical-{index}")
            tool_name = str(record.get("tool_name") or "tool")
            grouped_records[step].append(
                {
                    "name": tool_name,
                    "args": record.get("arguments") or {},
                    "id": tool_call_id,
                    "record": record,
                }
            )

        for step in step_order:
            tool_calls = grouped_records[step]
            context_messages.append(
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": item["name"], "args": item["args"], "id": item["id"]}
                        for item in tool_calls
                    ],
                )
            )
            for item in tool_calls:
                context_messages.append(
                    ToolMessage(
                        content=str(item["record"].get("result") or ""),
                        tool_call_id=item["id"],
                        name=item["name"],
                    )
                )

        if str(message.get("content") or "").strip() or message.get("thinking"):
            context_messages.append(_assistant_content_message(message, provider))

    context_messages.append(HumanMessage(content=current_message))
    return context_messages


def build_assistant_message_dict(
    *,
    content: str,
    thinking: str,
    thinking_duration,
    tool_call_records: list,
    blocks: list,
    input_tokens: int,
    output_tokens: int,
    context_tokens: int,
    elapsed_time: float,
    step_count: int,
    result_limit: int | None = None,
    reasoning_tokens: int = 0,
) -> dict:
    """构建用于持久化/返回的 assistant 消息字典（常规结束、HITL 中断、取消等场景共用）。"""
    _raw_content = content or ""
    # 过滤空/纯空白的 content block，避免持久化与历史会话渲染空正文
    _clean_blocks = [
        b for b in (blocks or [])
        if not (b.get("type") == "content" and not (b.get("content", "") or "").strip())
    ]
    return {
        "role": "assistant",
        "content": _raw_content if _raw_content.strip() else "",
        "timestamp": datetime.now().isoformat(),
        "thinking": thinking or None,
        "thinking_duration": thinking_duration or None,
        "tool_calls": tool_call_records_to_dicts(tool_call_records, result_limit) or None,
        "blocks": _clean_blocks or None,
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            # 思考 token（output_tokens 的子集，仅用于拆分展示）
            "reasoning_tokens": reasoning_tokens or 0,
            "context_tokens": context_tokens if context_tokens > 0 else input_tokens,
            "elapsed_time": round(elapsed_time, 2),
            "step_count": step_count,
        },
    }


def _mark_hitl_pending(
    partial_msg: dict,
    pending_tc_ids: list,
    action_requests: list,
    config_map: dict,
    thread_id: str,
) -> list:
    """将 HITL 中断触发的工具调用标记为「待审批」并附加可持久化的审批信息。

    在 tool_calls 与 blocks 上同步写入 approval_status / pending_approval /
    file_paths（前端历史渲染优先读 blocks，只标 tool_calls 会导致切回历史会话
    时审批徽章与按钮丢失）。同时在消息上写入 pending_approval（thread_id +
    操作详情），使前端从历史会话恢复时能重建审批 UI 并继续执行。

    Returns:
        action_requests 负载列表（同时用于下发 approval_required 事件）。
    """
    action_requests_payload = [
        {
            "tool_call_id": pending_tc_ids[i] if i < len(pending_tc_ids) else "",
            "tool_name": ar.get("name", ""),
            "arguments": ar.get("args", {}),
            "description": ar.get("description", ""),
            "allowed_decisions": config_map.get(ar.get("name", ""), {}).get(
                "allowed_decisions", ["approve", "reject"]
            ),
            "file_paths": _extract_file_paths_from_command(
                ar.get("args", {}).get("command", "")
            ),
        }
        for i, ar in enumerate(action_requests)
    ]
    _pending_file_paths = {
        ar["tool_call_id"]: ar.get("file_paths", [])
        for ar in action_requests_payload
        if ar.get("tool_call_id")
    }
    for tc in partial_msg.get("tool_calls") or []:
        if tc.get("tool_call_id") in pending_tc_ids:
            tc["approval_status"] = "pending"
            if tc.get("tool_call_id") in _pending_file_paths:
                tc["file_paths"] = _pending_file_paths[tc["tool_call_id"]]
    for b in partial_msg.get("blocks") or []:
        if (
            b.get("type") == "tool_call"
            and b.get("tool_call_id") in pending_tc_ids
        ):
            b["approval_status"] = "pending"
            b["pending_approval"] = True
            if b.get("tool_call_id") in _pending_file_paths:
                b["file_paths"] = _pending_file_paths[b["tool_call_id"]]
    partial_msg["pending_approval"] = {
        "thread_id": thread_id,
        "action_requests": action_requests_payload,
    }
    return action_requests_payload


def _maybe_rename_workspace(agent, db, session_id, tool_call_records, sid):
    """首轮对话完成后（含文件写入时）将工作区从 session_id 重命名为时间戳目录。

    主生成器在遇到 HITL 中断时会提前 return（约 1266 行），跳过此步；
    HITL 批准后的 resume 生成器需补做，否则带文件操作的 HITL 会话工作区
    始终停留在 session_id 命名，且不会触发记忆生成所需的目录。
    """
    try:
        if tool_call_records and agent and not agent._workspace_renamed:
            has_file_writes = any(
                tc[0] in ("write_file", "write_tool") and tc[4]
                for tc in tool_call_records
            )
            if has_file_writes:
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


def _submit_memory_updates(agent, session_id, db, raw_user_msg, accumulated_response, sid):
    """将「会话级 memory.md」与「用户级 AGENTS.md」的记忆更新提交到后台线程执行。

    主生成器与 HITL resume 生成器均复用此函数，确保无论首轮是否触发 HITL、
    在任务真正完成（含批准后）都会在工作区生成 memory.md，并在下一轮加载。

    注意：
    - update_memory_after_session 内部会同步调用 LLM（阻塞式 HTTP），必须放到线程池，
      否则会阻塞事件循环数秒~十几秒。
    - 会话级记忆写入成功后会调用 remove_session_agent 使 Agent 缓存失效，
      下次发消息时重建 Agent 以加载新记忆（保证第二轮能读到第一轮总结）。
    """
    # 1) 会话级记忆：workspace/{username}/{workspace_name}/memory.md
    if agent and agent.memory_file:
        try:
            from .memory_manager import update_memory_after_session

            session_llm = getattr(agent, "model", None)
            if session_llm is None:
                from .agent_manager import _llm_instance
                session_llm = _llm_instance

            def _session_memory_job():
                try:
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

            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, _session_memory_job)
            logger.info(f"[{sid}] 🧠 会话记忆更新已提交至后台线程执行")
        except Exception as e:
            logger.warning(f"[{sid}] 会话记忆更新提交失败: {e}")

    # 2) 用户级长期记忆：memories/{username}/AGENTS.md
    if agent and getattr(agent, "long_term_memory_file", None):
        try:
            from .memory_manager import update_long_term_memory_after_session

            session_llm = getattr(agent, "model", None)
            if session_llm is None:
                from .agent_manager import _llm_instance
                session_llm = _llm_instance

            def _long_term_memory_job():
                try:
                    update_long_term_memory_after_session(
                        agent.long_term_memory_file,
                        user_message=raw_user_msg,
                        assistant_response=accumulated_response,
                        llm=session_llm,
                    )
                    logger.info(
                        f"[{sid}] 🧠 用户长期记忆已更新 | 文件: {agent.long_term_memory_file}"
                    )
                except Exception as e:
                    logger.warning(f"[{sid}] 用户长期记忆更新失败: {e}")

            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, _long_term_memory_job)
            logger.info(f"[{sid}] 🧠 用户长期记忆更新已提交至后台线程执行")
        except Exception as e:
            logger.warning(f"[{sid}] 用户长期记忆更新提交失败: {e}")


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
    # 保存原始用户输入（记忆/工作区前缀注入前），供日志明确打印用户实际提问。
    raw_user_input = message_content

    if agent and agent.workspace_dir:
        message_content = f"[workspace: {agent.workspace_virtual_path}/ | shell: cd {agent.workspace_virtual_path}]\n{message_content}"

    # 记忆统一由 create_deep_agent(memory=[...]) 经 MemoryMiddleware 注入 system prompt：
    #   - 会话记忆 /workspace/memory.md
    #   - 长期记忆 /memories/AGENTS.md（见 _build_backend 的 /memories/ 路由）
    # 此处**不再**把记忆拼进用户消息——否则同一份内容会同时出现在 system prompt
    # 与用户消息中，多占一份上下文。记忆更新后由 remove_session_agent 使缓存失效，
    # 下次重建 Agent 时会加载到最新内容。

    # 说明：会话累计 token（pre_session_tokens / session_estimate）已移除，
    # 前端只展示本轮的 input / output / reasoning 三类 token。

    ws_info = (
        str(agent.workspace_dir.absolute())
        if agent and agent.workspace_dir
        else "unknown"
    )
    logger.info(
        f"[{sid}] 开始流式响应 | workspace: {ws_info} | 用户: {username}"
    )
    logger.info(
        f"[{sid}] 💬 用户输入 | {raw_user_input[:200]}{'...' if len(raw_user_input) > 200 else ''}"
    )

    try:

        yield format_sse({"type": "start", "session_id": session_id})

        # ── 配置 ─────────────────────────────────────────────────
        context_length = None
        auto_compress_tokens = None
        result_log_truncate = 200
        _agent_config = get_agent_config()
        if _agent_config and _agent_config.get("config"):
            # 以「本次实际使用的模型」的窗口为基准（用户可切换模型，各 provider 的
            # context_length 分别配置），口径与摘要中间件保持一致。
            context_length = resolve_context_length(
                _agent_config["config"], getattr(agent, "model_name", None)
            )
            result_log_truncate = getattr(
                _agent_config["config"].tools, "result_log_truncate", 200
            )
        if not context_length:
            model_instance = getattr(agent, "model", None)
            if (
                model_instance
                and hasattr(model_instance, "profile")
                and model_instance.profile
            ):
                context_length = model_instance.profile.get("max_input_tokens")  # LangChain model.profile 的键名，非本项目字段
        if not context_length:
            model_instance = getattr(agent, "model", None)
            context_length = _get_model_context_limit(model_instance)
        if context_length:
            _summ = get_agent_config()
            _thr = (
                _summ["config"].summarization.compression_threshold
                if _summ and _summ.get("config")
                else 0.8
            )
            auto_compress_tokens = int(context_length * _thr)
        else:
            auto_compress_tokens = 170000

        # ── StreamProcessor（混合流式：updates 权威 + messages token 流式）──
        proc = StreamProcessor(
            sid=sid, session_id=session_id, db=db, message_id=message_id,
            session_logger=session_logger,
            context_length=context_length,
            auto_compress_tokens=auto_compress_tokens,
            start_time=start_time,
        )

        def _tool_call_records_from_blocks(blks):
            return _tool_call_tuples(blks)

        def _persist():
            _persist_incremental(
                proc, db=db, session_id=session_id, sid=sid, start_time=start_time
            )

        def _on_tool_result(ev):
            _handle_tool_result_event(
                ev, db=db, session_id=session_id, message_id=message_id,
                session_logger=session_logger, agent=agent, sid=sid,
            )

        def _on_thinking_end(ev):
            _persist_thinking_event(
                ev, proc=proc, db=db, session_id=session_id,
                message_id=message_id, sid=sid,
            )

        last_persisted_len = 0

        # ── 构建上下文消息 ─────────────────────────────────────────────
        # session.messages 末尾是本次刚写入 DB 的当前用户消息（chat.py 在调用
        # 本生成器前已 add_message），需排除以免与下方 message_content 重复注入。
        # 完整历史直接注入；超长时由官方 SummarizationMiddleware 在模型调用前
        # 按 token 阈值自动摘要，无需在此自行截断或压缩。
        session = db.get_session(session_id)
        total_msgs = len(session.messages) if session and session.messages else 0
        history_messages = session.messages[:-1] if total_msgs else []
        provider = agent.config.llm.provider.lower() if agent and agent.config else ""
        context_messages = build_context_messages(
            history_messages, message_content, provider=provider
        )
        logger.info(
            f"[{sid}] 📚 上下文消息构建 | DB 消息数: {total_msgs} | "
            f"历史注入: {len(context_messages) - 1} | 压缩: 官方 SummarizationMiddleware"
        )

        # ── 启动流式输出 (stream_mode=['messages', 'updates']) ─────────
        logger.info(f"[{sid}] 🚀 使用 stream_mode=['messages', 'updates'] 流式接口")

        # HITL: per-message thread_id for checkpointer state isolation
        thread_id = f"{session_id}-{message_id}"
        stream_config = {"configurable": {"thread_id": thread_id}}

        async for event in agent.agent.astream(
            {"messages": context_messages},
            stream_mode=["messages", "updates"],
            config=stream_config,
        ):
            mode, data = (
                event if isinstance(event, tuple) else ("messages", event)
            )
            for ev in proc.handle(mode, data):
                ev_type = ev.get("type")
                if ev_type == "tool_result":
                    _on_tool_result(ev)
                elif ev_type == "thinking_end":
                    _on_thinking_end(ev)
                elif ev_type == "todo_list":
                    try:
                        db.update_session_todos(session_id, ev["todos"])
                    except Exception as e:
                        logger.warning(f"[{sid}] 持久化 Todo list 失败: {e}")
                elif ev_type == "content":
                    if len(proc.accumulated_response) - last_persisted_len >= 500:
                        last_persisted_len = len(proc.accumulated_response)
                        _persist()
                yield format_sse(ev)

        # ── Post-streaming: 流结束 ──────────────────────────────────────
        logger.info(f"[{sid}] 📢 流式输出结束")

        # 确保 thinking 结束（流结束时若仍在思考）
        for _ev in proc._end_thinking():
            yield format_sse(_ev)

        # 同步本地状态别名（供后续 HITL/持久化/记忆逻辑使用）
        accumulated_response = proc.accumulated_response
        accumulated_thinking = proc.accumulated_thinking
        blocks = proc.blocks
        tool_call_records = _tool_call_records_from_blocks(proc.blocks)
        current_step = proc.current_step
        last_context_tokens = proc.last_context_tokens
        is_in_thinking = proc.is_in_thinking
        thinking_start_time = proc.thinking_start_time
        thinking_end_time = None
        content_start_time = None

        # 发送 content_end
        if accumulated_response:
            yield format_sse({"type": "content_end", "content": ""})

        # ── HITL: 检测中断（文件删除审批） ────────────────────────────
        try:
            graph_state = await agent.agent.aget_state(stream_config)
            if graph_state.next and graph_state.tasks:
                hitl_request = _extract_hitl_request(graph_state)
                if hitl_request:
                    action_requests = hitl_request.get("action_requests", [])
                    review_configs = hitl_request.get("review_configs", [])
                    config_map = _hitl_review_config_map(hitl_request)
                    # 从状态中获取 tool_call_id（ActionRequest 不含 id）
                    # 关键：只取需要审批的工具（与 action_requests 按 tool_name 匹配），
                    # 而非 AIMessage 的全部 tool_calls。否则同一 AIMessage 里无需审批的工具
                    # 也会被标 pending，且 resume 时 decisions 数量对不上 -> 永远显示"待审批"。
                    state_msgs = graph_state.values.get("messages", [])
                    _approval_tool_names = _hitl_action_names(hitl_request)
                    pending_tc_ids = []
                    for msg in reversed(state_msgs):
                        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                            pending_tc_ids = [
                                tc.get("id", "")
                                for tc in msg.tool_calls
                                if tc.get("name", "") in _approval_tool_names
                            ]
                            break
                    # 保存部分 assistant 消息（复用取消路径模式）
                    partial_elapsed = time.time() - start_time
                    partial_msg = build_assistant_message_dict(
                        content=accumulated_response,
                        thinking=accumulated_thinking,
                        thinking_duration=None,
                        tool_call_records=tool_call_records,
                        blocks=blocks,
                        input_tokens=proc.last_usage.get("input_tokens", 0),
                        output_tokens=proc.last_usage.get("output_tokens", 0),
                        reasoning_tokens=proc.last_usage.get("reasoning_tokens", 0),
                        context_tokens=last_context_tokens,
                        elapsed_time=partial_elapsed,
                        step_count=current_step,
                    )
                    # 持久化 HITL 审批状态：将触发中断的工具调用标记为「待审批」
                    # 注意：blocks 也必须同步打标记——前端历史渲染优先读 blocks，
                    # 只标 tool_calls 会导致切回历史会话时审批状态徽章不显示。
                    action_requests_payload = _mark_hitl_pending(
                        partial_msg, pending_tc_ids, action_requests, config_map, thread_id
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
                            "action_requests": action_requests_payload,
                        }
                    )
                    return
        except Exception as e:
            logger.warning(f"[{sid}] HITL 中断检测异常: {e}", exc_info=True)

        elapsed_time = time.time() - start_time

        # 统计
        thinking_time = round(
            sum(b.get("duration", 0) or 0 for b in blocks if b.get("type") == "thinking"), 2
        )
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
        # 展示/持久化口径：**最近一次模型调用**的用量（不跨步累加）；
        # 日志里的"本轮累计"仍用累加值，便于核对计费与排查。
        last_usage = proc.last_usage
        inp_tokens = last_usage["input_tokens"]
        out_tokens = last_usage["output_tokens"]
        rea_tokens = last_usage.get("reasoning_tokens", 0)
        sum_tokens = inp_tokens + out_tokens

        logger.info(
            f"[{sid}] 📊 Token | 当前 step: ↑{inp_tokens} ↓{out_tokens}"
            f"(思考{rea_tokens}) Σ{sum_tokens}"
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
            reasoning_tokens=rea_tokens,
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

        # Workspace rename（抽取为公共函数，主生成器与 HITL resume 生成器复用）
        _maybe_rename_workspace(agent, db, session_id, tool_call_records, sid)

        # 会话级 + 用户级记忆持久化（复用公共函数，主生成器与 HITL resume 生成器一致，
        # 确保首轮即使触发 HITL、在批准后任务真正完成时也会生成 memory.md 并在第二轮加载）
        raw_user_msg = parsed_content or request.message
        _submit_memory_updates(agent, session_id, db, raw_user_msg, accumulated_response, sid)

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
            # 与 token_usage 事件保持一致：只给**最近一次模型调用**的用量（不跨步累加），
            # 否则 done 事件会把前端已显示的值覆盖回累加值。
            "input_tokens": inp_tokens,
            "output_tokens": out_tokens,
            "reasoning_tokens": rea_tokens,
            "context_length": context_length,
            "auto_compress_tokens": auto_compress_tokens,
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
        accumulated_response = proc.accumulated_response
        accumulated_thinking = proc.accumulated_thinking
        blocks = proc.blocks
        tool_call_records = _tool_call_records_from_blocks(proc.blocks)
        current_step = proc.current_step
        last_context_tokens = proc.last_context_tokens
        is_in_thinking = proc.is_in_thinking
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
                input_tokens=proc.last_usage.get("input_tokens", 0),
                output_tokens=proc.last_usage.get("output_tokens", 0),
                reasoning_tokens=proc.last_usage.get("reasoning_tokens", 0),
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
        accumulated_response = proc.accumulated_response
        blocks = proc.blocks
        current_step = proc.current_step
        is_in_thinking = proc.is_in_thinking
        if is_in_thinking and proc.thinking_start_time:
            dur = round(time.time() - proc.thinking_start_time, 2)
            if proc.blocks and proc.blocks[-1]["type"] == "thinking":
                proc.blocks[-1]["duration"] = dur
            proc.is_in_thinking = False
            yield format_sse({"type": "thinking_end", "duration": dur, "step": current_step})
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
    message_id: str = "",
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
    last_context_tokens = 0
    original_user_msg = ""  # 触发 HITL 的原始用户消息，供记忆生成使用

    try:
        session = db.get_session(session_id)
        pending_tool_call_ids = []
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
                            tc.get("approval_status"),
                        ])
                    usage = msg.get("usage") or {}
                    current_step = usage.get("step_count", 0)
                    # 收集历史中已被标记为「待审批」的工具调用（HITL 中断），供后续写回最终决策。
                    # 关键：HITL 中断发生在工具「决定调用但尚未执行」时，此时 tool_call_records
                    # 尚未生成（要等 ToolMessage 即工具执行后才 append），因此 message.tool_calls
                    # 不含该中断工具，approval_status="pending" 只落在 blocks 上。
                    # 故必须从 blocks 取 pending id，tool_calls 仅作补充，否则 pending_tool_call_ids
                    # 恒为空 -> 决策不应用 -> 历史永远显示「待审批」。
                    pending_tool_call_ids: list[str] = []
                    _seen_pending: set = set()
                    for b in (msg.get("blocks") or []):
                        if (
                            b.get("type") == "tool_call"
                            and b.get("approval_status") == "pending"
                        ):
                            tid = b.get("tool_call_id", "")
                            if tid and tid not in _seen_pending:
                                _seen_pending.add(tid)
                                pending_tool_call_ids.append(tid)
                    for tc in (msg.get("tool_calls") or []):
                        if tc.get("approval_status") == "pending":
                            tid = tc.get("tool_call_id", "")
                            if tid and tid not in _seen_pending:
                                _seen_pending.add(tid)
                                pending_tool_call_ids.append(tid)
                    break
    except Exception as e:
        logger.warning(f"[{sid}] HITL 恢复: 加载部分消息失败: {e}")

    # 捕获触发 HITL 的原始用户消息（会话中最后一条 user 消息），供记忆生成使用
    try:
        if session and session.messages:
            for m in session.messages:
                if m.get("role") == "user":
                    original_user_msg = m.get("content", "") or ""
    except Exception:
        pass

    # 将审批决策立即应用到内存中的 blocks / tool_call_records：
    # 1) 前端历史渲染优先读 blocks，只写 tool_calls 不够；
    # 2) 恢复流结束后会用内存数据整条覆盖持久化（build_assistant_message_dict），
    #    若只 patch 数据库（_persist_approval_decisions），最终写回会把状态冲回 "pending"。
    decision_status_by_id: dict[str, str] = {}
    pending_decisions = normalize_resume_decisions(
        decisions, hanging_tool_count=len(pending_tool_call_ids)
    )
    for i, tc_id in enumerate(pending_tool_call_ids):
        if i >= len(pending_decisions):
            break
        decision_item = pending_decisions[i] or {}
        decision_type = decision_item.get("type") if isinstance(decision_item, dict) else None
        decision_status_by_id[tc_id] = (
            "approved" if decision_type == "approve" else "rejected"
        )
    if decision_status_by_id:
        for rec in tool_call_records:
            rec_id = rec[1] if len(rec) > 1 else ""
            if rec_id in decision_status_by_id:
                while len(rec) < 8:
                    rec.append(None)
                rec[7] = decision_status_by_id[rec_id]
        for b in blocks:
            if (
                b.get("type") == "tool_call"
                and b.get("tool_call_id") in decision_status_by_id
            ):
                b["approval_status"] = decision_status_by_id[b["tool_call_id"]]
                b.pop("pending_approval", None)

    logger.info(
        f"[{sid}] 🔔 HITL 恢复 | thread_id={thread_id} | "
        f"已有内容 {len(accumulated_response)} 字符 | "
        f"已有 blocks {len(blocks)} | decisions={decisions}"
    )
    if decisions:
        decision_desc = "、".join(
            ("批准" if (d or {}).get("type") == "approve" else "拒绝")
            if isinstance(d, dict) else str(d)
            for d in decisions
        )
        logger.info(f"[{sid}] 👤 用户审批操作: {decision_desc}")

    stream_config = {"configurable": {"thread_id": thread_id}}
    # 与 chat 流口径一致：取「本次实际使用的模型」的窗口，并按阈值折算自动压缩点。
    # 原实现读的是并不存在的 EasyAgent.context_length / auto_compress_tokens 属性，
    # 恒为 0，导致 HITL 恢复后前端拿不到窗口值（上下文占用率显示为 0/N）。
    context_length = resolve_context_length(
        getattr(agent, "config", None), getattr(agent, "model_name", None)
    )
    auto_compress_tokens = 0
    if context_length:
        _summ_cfg = getattr(getattr(agent, "config", None), "summarization", None)
        _thr = float(getattr(_summ_cfg, "compression_threshold", 0.8) or 0.8)
        auto_compress_tokens = int(context_length * _thr)

    # ── StreamProcessor（用 DB 载入的初始状态初始化）──────────────────
    proc = StreamProcessor(
        sid=sid, session_id=session_id, db=db, message_id=message_id,
        session_logger=session_logger,
        context_length=context_length,
        auto_compress_tokens=auto_compress_tokens,
        current_step=current_step, blocks=list(blocks),
        last_context_tokens=last_context_tokens,
        accumulated_response=accumulated_response,
        accumulated_thinking=accumulated_thinking,
        start_time=start_time,
    )

    def _tool_call_records_from_blocks(blks):
        return _tool_call_tuples(blks, with_approval=True)

    def _persist():
        _persist_incremental(
            proc, db=db, session_id=session_id, sid=sid, start_time=start_time
        )

    def _on_tool_result(ev):
        _handle_tool_result_event(
            ev, db=db, session_id=session_id, message_id=message_id,
            session_logger=session_logger, agent=agent, sid=sid,
        )

    def _on_thinking_end(ev):
        _persist_thinking_event(
            ev, proc=proc, db=db, session_id=session_id,
            message_id=message_id, sid=sid,
        )

    def _persist_approval_decisions():
        """将用户对 HITL 工具调用的审批决策（批准/拒绝）持久化到最近一条 assistant 消息，
        使切换会话/刷新页面后仍能显示审批标记。"""
        if not decision_status_by_id:
            return
        try:
            sess = db.get_session(session_id)
            if not sess or not sess.messages:
                return
            last = sess.messages[-1]
            if last.get("role") != "assistant":
                return
            for tc in last.get("tool_calls") or []:
                if tc.get("tool_call_id") in decision_status_by_id:
                    tc["approval_status"] = decision_status_by_id[tc["tool_call_id"]]
            # blocks 同步写回：前端历史渲染优先读 blocks
            for b in last.get("blocks") or []:
                if (
                    b.get("type") == "tool_call"
                    and b.get("tool_call_id") in decision_status_by_id
                ):
                    b["approval_status"] = decision_status_by_id[b["tool_call_id"]]
                    b.pop("pending_approval", None)
            db.update_last_assistant_message(session_id, last)
        except Exception as e:
            logger.warning(f"[{sid}] 持久化审批决策失败: {e}")

    # 构造 resume 载荷：当图中存在多个 pending interrupt（如并行工具调用/子代理各自触发 HITL）时，
    # LangGraph 要求以 {interrupt_id: value} 映射形式恢复，否则抛
    # "When there are multiple pending interrupts, you must specify the interrupt id when resuming."
    resume_command = None
    try:
        state = await agent.agent.aget_state(stream_config)
        interrupt_items = _hitl_interrupt_items(state)
        if len(interrupt_items) == 1:
            interrupt_id, value = interrupt_items[0]
            hanging_tool_count = len(value.get("action_requests") or [])
            effective_decisions = normalize_resume_decisions(
                decisions, hanging_tool_count=hanging_tool_count
            )
            resume_payload = {"decisions": effective_decisions}
            resume_command = Command(resume=resume_payload)
        elif len(interrupt_items) > 1 and all(
            interrupt_id for interrupt_id, _ in interrupt_items
        ):
            logger.info(
                f"[{sid}] HITL 恢复: 检测到 {len(interrupt_items)} 个 pending interrupt，"
                "按 interrupt_id 与各自挂起工具数映射恢复"
            )
            resume_command = Command(
                resume={
                    interrupt_id: {
                        "decisions": normalize_resume_decisions(
                            decisions,
                            hanging_tool_count=len(value.get("action_requests") or []),
                        )
                    }
                    for interrupt_id, value in interrupt_items
                }
            )
        else:
            hanging_tool_count = hanging_hitl_tool_count(state)
            effective_decisions = normalize_resume_decisions(
                decisions, hanging_tool_count=hanging_tool_count
            )
            resume_command = Command(resume={"decisions": effective_decisions})
    except Exception as e:
        logger.warning(f"[{sid}] HITL 恢复: 检查 pending interrupts 失败（回退默认 resume）: {e}")
    if resume_command is None:
        resume_command = Command(resume={"decisions": decisions})

    try:
        last_persisted_len = 0
        async for event in agent.agent.astream(
            resume_command,
            stream_mode=["messages", "updates"],
            config=stream_config,
        ):
            mode, data = (
                event if isinstance(event, tuple) else ("messages", event)
            )
            for ev in proc.handle(mode, data):
                ev_type = ev.get("type")
                if ev_type == "tool_result":
                    _on_tool_result(ev)
                elif ev_type == "thinking_end":
                    _on_thinking_end(ev)
                elif ev_type == "todo_list":
                    try:
                        db.update_session_todos(session_id, ev["todos"])
                    except Exception as e:
                        logger.warning(f"[{sid}] 持久化 Todo list 失败: {e}")
                elif ev_type == "content":
                    if len(proc.accumulated_response) - last_persisted_len >= 500:
                        last_persisted_len = len(proc.accumulated_response)
                        _persist()
                yield format_sse(ev)

        # ── Post-streaming ─────────────────────────────────────────
        # 确保 thinking 结束（流结束时若仍在思考）
        for _ev in proc._end_thinking():
            yield format_sse(_ev)

        # 同步本地状态别名（供后续嵌套 HITL/持久化/记忆逻辑使用）
        accumulated_response = proc.accumulated_response
        accumulated_thinking = proc.accumulated_thinking
        blocks = proc.blocks
        tool_call_records = _tool_call_records_from_blocks(proc.blocks)
        current_step = proc.current_step
        last_context_tokens = proc.last_context_tokens
        is_in_thinking = proc.is_in_thinking
        logger.info(f"[{sid}] 📢 流式输出结束 | steps={current_step} | blocks={len(blocks)}")

        if accumulated_response:
            yield format_sse({"type": "content_end", "content": ""})

        # ── HITL: 检测嵌套中断 ─────────────────────────────────────
        try:
            graph_state = await agent.agent.aget_state(stream_config)
            if graph_state.next and graph_state.tasks:
                hitl_request = _extract_hitl_request(graph_state)
                if hitl_request:
                    action_requests = hitl_request.get("action_requests", [])
                    review_configs = hitl_request.get("review_configs", [])
                    config_map = _hitl_review_config_map(hitl_request)
                    state_msgs = graph_state.values.get("messages", [])
                    _nested_approval_names = _hitl_action_names(hitl_request)
                    pending_tc_ids = []
                    for msg in reversed(state_msgs):
                        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                            pending_tc_ids = [
                                tc.get("id", "")
                                for tc in msg.tool_calls
                                if tc.get("name", "") in _nested_approval_names
                            ]
                            break
                    partial_elapsed = time.time() - start_time
                    partial_msg = build_assistant_message_dict(
                        content=accumulated_response,
                        thinking=accumulated_thinking,
                        thinking_duration=None,
                        tool_call_records=tool_call_records,
                        blocks=blocks,
                        input_tokens=proc.last_usage.get("input_tokens", 0),
                        output_tokens=proc.last_usage.get("output_tokens", 0),
                        reasoning_tokens=proc.last_usage.get("reasoning_tokens", 0),
                        context_tokens=last_context_tokens,
                        elapsed_time=partial_elapsed,
                        step_count=current_step,
                    )
                    # 持久化 HITL 审批状态：将本层嵌套中断触发的工具调用标记为「待审批」
                    # blocks 同步打标记（前端历史渲染优先读 blocks）
                    action_requests_payload = _mark_hitl_pending(
                        partial_msg, pending_tc_ids, action_requests, config_map, thread_id
                    )
                    db.update_last_assistant_message(session_id, partial_msg)
                    db.update_last_assistant_message_row(session_id, partial_msg)
                    yield format_sse({
                        "type": "approval_required",
                        "thread_id": thread_id,
                        # 嵌套中断时也下发当前 blocks，前端据此刷新已完成的工具结果
                        "blocks": proc._sse_blocks(),
                        "action_requests": action_requests_payload,
                    })
                    return
        except Exception as e:
            logger.warning(f"[{sid}] HITL 恢复: 嵌套中断检测异常: {e}", exc_info=True)
        finally:
            # 无论正常结束、嵌套中断还是异常，均把审批决策写回，保证切换会话后仍可见
            _persist_approval_decisions()

        elapsed_time = time.time() - start_time

        # 恢复流重跑工具时会再次 append 同 tool_call_id 的记录（DB 加载的 + 重跑时新增的），
        # 按 tool_call_id 去重并合并审批状态：保留有结果的记录，继承非空 approval_status，
        # 避免最终消息出现重复工具卡、且审批标记（已批准）不被无状态的新记录冲掉。
        _merged_tcr: dict[str, list] = {}
        for rec in tool_call_records:
            tid = rec[1] if len(rec) > 1 else ""
            status = rec[7] if len(rec) > 7 else None
            base = _merged_tcr.get(tid)
            if base is None:
                _merged_tcr[tid] = list(rec)
            else:
                # rec 来自 _tool_call_records_from_blocks，是 tuple（不可变）；
                # chosen 后续需要做下标赋值（chosen[7] = ...），必须先转成 list，
                # 否则 TypeError: 'tuple' object does not support item assignment。
                chosen = list(rec) if rec[3] else base
                other = base if rec[3] else rec
                if len(chosen) < 8:
                    chosen = chosen + [None] * (8 - len(chosen))
                # 继承非空审批状态
                if chosen[7] is None and other[7] is not None:
                    chosen[7] = other[7]
                _merged_tcr[tid] = chosen
        tool_call_records = list(_merged_tcr.values())

        # blocks 同样需要 finalize：astream 重放可能新建同 tool_call_id/同名 block，
        # 且内存 blocks 的 approval_status 可能在重放中被遗漏。这里：
        # 1) 重新应用审批决策（已批准的 tc_id -> approval_status=approved，移除 pending_approval）；
        # 2) 按 tool_call_id 去重 blocks（保留有结果的，继承非空 approval_status），
        #    避免历史会话出现重复工具卡或残留 pending（误显示"待审批"）。
        if decision_status_by_id:
            for b in blocks:
                if (
                    b.get("type") == "tool_call"
                    and b.get("tool_call_id") in decision_status_by_id
                ):
                    b["approval_status"] = decision_status_by_id[b["tool_call_id"]]
                    b.pop("pending_approval", None)
        _final_blocks: list = []
        _seen_tc_blocks: dict = {}
        for b in blocks:
            if b.get("type") == "tool_call":
                tid = b.get("tool_call_id", "")
                if tid and tid in _seen_tc_blocks:
                    existing = _seen_tc_blocks[tid]
                    # 选取有结果的作为基础，并继承非空审批状态
                    if b.get("result") and not existing.get("result"):
                        merged = dict(b)
                        if not merged.get("approval_status") and existing.get("approval_status"):
                            merged["approval_status"] = existing["approval_status"]
                        _seen_tc_blocks[tid] = merged
                        # 替换列表中的占位
                        idx = _final_blocks.index(existing)
                        _final_blocks[idx] = merged
                    else:
                        if not existing.get("approval_status") and b.get("approval_status"):
                            existing["approval_status"] = b["approval_status"]
                    continue
                elif tid:
                    _seen_tc_blocks[tid] = b
                    _final_blocks.append(b)
                else:
                    _final_blocks.append(b)
            else:
                _final_blocks.append(b)
        blocks = _final_blocks
        proc.blocks = blocks  # 同步去重后的 blocks，确保 done 事件 _sse_blocks() 一致

        # 展示/持久化口径：最近一次模型调用的用量（不跨步累加）
        last_usage = proc.last_usage
        inp_tokens = last_usage["input_tokens"]
        out_tokens = last_usage["output_tokens"]
        rea_tokens = last_usage.get("reasoning_tokens", 0)
        sum_tokens = inp_tokens + out_tokens

        usage_payload = {
            # 同主生成器：done 事件只下发最近一次调用的用量（不跨步累加）
            "input_tokens": inp_tokens,
            "output_tokens": out_tokens,
            "reasoning_tokens": rea_tokens,
            "context_length": context_length,
            "auto_compress_tokens": auto_compress_tokens,
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
            reasoning_tokens=rea_tokens,
            context_tokens=last_context_tokens,
            elapsed_time=elapsed_time,
            step_count=current_step,
        )
        db.update_last_assistant_message(session_id, assistant_message)
        db.update_last_assistant_message_row(session_id, assistant_message)

        # 记忆持久化：HITL 恢复流首轮因中断后，用户批准后任务真正完成时，
        # 也须在工作区生成 memory.md（主生成器在 HITL 中断处提前 return，到不了记忆块；
        # 此处复用与主生成器一致的公共函数，保证第二轮能加载第一轮总结）。
        # 注意：仅在任务真正完成（非嵌套中断，嵌套中断已提前 return）时执行。
        _maybe_rename_workspace(agent, db, session_id, tool_call_records, sid)
        _submit_memory_updates(agent, session_id, db, original_user_msg, accumulated_response, sid)

        logger.info(
            f"[{sid}] ✅ 流式响应完成 | 总步骤: {current_step} | "
            f"总耗时: {elapsed_time:.2f}s | tokens={sum_tokens} "
            f"(in={inp_tokens}, out={out_tokens}, think={rea_tokens})"
        )

        yield format_sse({
            "type": "done",
            "session_id": session_id,
            "elapsed_time": round(elapsed_time, 2),
            "usage": usage_payload,
            # 下发权威最终 blocks（含工具结果/耗时），前端据此替换内存 blocks，
            # 避免依赖 tool_result 事件匹配（HITL 恢复流曾因匹配失败导致工具一直"执行中"）。
            "blocks": proc._sse_blocks(),
        })

    except asyncio.CancelledError:
        logger.info(f"[{sid}] HITL 恢复被取消")
        accumulated_response = proc.accumulated_response
        accumulated_thinking = proc.accumulated_thinking
        blocks = proc.blocks
        tool_call_records = _tool_call_records_from_blocks(proc.blocks)
        last_context_tokens = proc.last_context_tokens
        current_step = proc.current_step
        partial_msg = build_assistant_message_dict(
            content=accumulated_response,
            thinking=accumulated_thinking,
            thinking_duration=None,
            tool_call_records=tool_call_records,
            blocks=blocks,
            input_tokens=proc.last_usage.get("input_tokens", 0),
            output_tokens=proc.last_usage.get("output_tokens", 0),
            reasoning_tokens=proc.last_usage.get("reasoning_tokens", 0),
            context_tokens=last_context_tokens,
            elapsed_time=time.time() - start_time,
            step_count=current_step,
        )
        db.update_last_assistant_message(session_id, partial_msg)
        db.update_last_assistant_message_row(session_id, partial_msg)
        yield format_sse({"type": "error", "content": "请求被取消"})
    except Exception as e:
        logger.error(f"[{sid}] HITL 恢复异常: {str(e)}", exc_info=True)
        # 持久化已生成的部分回复（与取消路径一致），避免流式过程中已完成的
        # 思考/工具/正文在异常后丢失，导致历史会话中 HITL 之后的记录整体消失。
        try:
            _partial = build_assistant_message_dict(
                content=proc.accumulated_response,
                thinking=proc.accumulated_thinking,
                thinking_duration=None,
                tool_call_records=_tool_call_records_from_blocks(proc.blocks),
                blocks=proc.blocks,
                input_tokens=proc.last_usage.get("input_tokens", 0),
                output_tokens=proc.last_usage.get("output_tokens", 0),
                reasoning_tokens=proc.last_usage.get("reasoning_tokens", 0),
                context_tokens=proc.last_context_tokens,
                elapsed_time=time.time() - start_time,
                step_count=proc.current_step,
            )
            db.update_last_assistant_message(session_id, _partial)
            db.update_last_assistant_message_row(session_id, _partial)
        except Exception as persist_err:
            logger.warning(f"[{sid}] HITL 恢复异常时持久化失败: {persist_err}")
        yield format_sse({"type": "error", "content": f"处理失败: {str(e)}"})
