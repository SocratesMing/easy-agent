"""流式编排器（C2 第三步）：统一 chat / resume 的流式循环与收尾。

职责边界：

- ``projector``：纯数据整形（chunk/状态 → 事件载荷/持久化结构）；
- ``persistence``：增量落库、最终消息、审批决策；
- ``side_effects``：工作区重命名、技能入库、记忆更新；
- 本模块：只保留「什么时候调用谁」的编排逻辑与对外生成器。

对外契约（SSE 事件名/字段/顺序）与拆分前逐字保持一致；``streaming`` 包的
``__init__`` 负责重导出，旧 ``streaming.py`` 文件保留为按路径加载的兼容垫片。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import AsyncGenerator

from langgraph.types import Command

from easy_agent.agent import EasyAgent
from easy_agent.db import Database
from easy_agent.persistence.checkpoints import checkpoint_instance
from easy_agent.agent.model import resolve_context_length
from easy_agent.agent.run_config import build_run_config
from easy_agent.agent.turn_context import turn_input
from easy_agent.models.api import ChatRequest
from easy_agent.utils.blocking import run_blocking
from easy_agent.utils.session_logger import SessionLogger
from easy_agent.services.agent_manager import get_agent_config
from easy_agent.services.hitl_state import (
    HitlDecisionError,
    ResumePlan,
    normalize_resume_decisions,
    pending_hitl_interrupts,
    plan_resume,
)
from easy_agent.services.sse import SSEEvent
from easy_agent.services.sse_emitter import SSEEmitter
from easy_agent.services.stream_messages import (
    _partial_message_dict,
    _tool_call_tuples,
    build_assistant_message_dict,
    tool_call_records_to_dicts,
    turn_message_text,
)
from easy_agent.services.stream_processor import StreamProcessor
from easy_agent.services.session_thread import (
    approval_handle,
    plan_turn,
    record_turn_checkpoint,
)
from easy_agent.services.streaming.persistence import (
    IncrementalPersister,
    persist_approval_decisions,
    persist_final_message,
    persist_partial,
    persist_thinking_event,
    persist_todos,
    persist_tool_result_event,
    write_session_context_snapshot,
)
from easy_agent.services.streaming.projector import (
    apply_decision_status_to_blocks,
    apply_decision_status_to_records,
    build_usage_payload,
    decision_status_by_interrupt,
    decision_status_map,
    finalize_blocks,
    get_model_context_limit,
    last_user_message,
    load_resume_snapshot,
    mark_hitl_pending_interrupts,
    merge_tool_call_records,
    thinking_duration,
)
from easy_agent.services.streaming.side_effects import (
    maybe_rename_workspace,
    register_session_skills,
    submit_memory_updates,
)

logger = logging.getLogger("easy_agent.chat_service")


@dataclass
class HitlInterruption:
    """一次 HITL 中断的投影结果。"""

    action_requests_payload: list
    partial_message: dict


def resolve_chat_context(agent) -> tuple[int | None, int | None, int]:
    """主生成器的上下文窗口/自动压缩点/工具结果日志截断长度。"""
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
            # LangChain model.profile 的键名，非本项目字段
            context_length = model_instance.profile.get("max_input_tokens")
    if not context_length:
        model_instance = getattr(agent, "model", None)
        context_length = get_model_context_limit(model_instance)
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
    return context_length, auto_compress_tokens, result_log_truncate


def resolve_resume_context(agent) -> tuple[int, int]:
    """恢复流的上下文窗口/自动压缩点。

    与 chat 流口径一致：取「本次实际使用的模型」的窗口，并按阈值折算自动压缩点。
    原实现读的是并不存在的 EasyAgent.context_length / auto_compress_tokens 属性，
    恒为 0，导致 HITL 恢复后前端拿不到窗口值（上下文占用率显示为 0/N）。
    """
    context_length = resolve_context_length(
        getattr(agent, "config", None), getattr(agent, "model_name", None)
    )
    auto_compress_tokens = 0
    if context_length:
        _summ_cfg = getattr(getattr(agent, "config", None), "summarization", None)
        _thr = float(getattr(_summ_cfg, "compression_threshold", 0.8) or 0.8)
        auto_compress_tokens = int(context_length * _thr)
    return context_length, auto_compress_tokens


async def pump_stream(
    *,
    agent,
    stream_input,
    stream_config,
    proc,
    persister: IncrementalPersister,
    db,
    session_id: str,
    sid: str,
    message_id: str,
    session_logger,
    context=None,
):
    """消费 ``agent.astream(['messages','updates'])`` 并逐个产出处理器事件。

    chat / resume 原本各有一份逐行相同的循环（含同样的副作用分派与节流），
    现统一到此处；事件顺序与副作用调用顺序保持不变。
    """
    # D2：编译图按「模型 + 工具集 + 提示词」跨 session 复用，会话差异必须经
    # context 传入（backend callable / context-bound 工具据此解析 workspace）。
    # 兼容旧 fake/独立调用：context 为空时保持原调用形态，不额外传关键字。
    astream_kwargs = {
        "stream_mode": ["messages", "updates"],
        "config": stream_config,
    }
    if context is not None:
        astream_kwargs["context"] = context
    async for event in agent.agent.astream(stream_input, **astream_kwargs):
        mode, data = event if isinstance(event, tuple) else ("messages", event)
        for ev in proc.handle(mode, data):
            ev_type = ev.get("type")
            if ev_type == "tool_result":
                await run_blocking(
                    persist_tool_result_event,
                    ev,
                    db=db,
                    session_id=session_id,
                    message_id=message_id,
                    session_logger=session_logger,
                    agent=agent,
                    sid=sid,
                )
            elif ev_type == "thinking_end":
                await run_blocking(
                    persist_thinking_event,
                    ev,
                    proc=proc,
                    db=db,
                    session_id=session_id,
                    message_id=message_id,
                    sid=sid,
                )
            elif ev_type == "todo_list":
                await run_blocking(persist_todos, db, session_id, ev["todos"], sid)
            elif ev_type == "content":
                if persister.due(proc):
                    await run_blocking(persister.maybe_persist, proc)
            yield ev


async def detect_hitl_interruption(
    *,
    agent,
    stream_config,
    proc,
    elapsed_time: float,
    thread_id: str,
    with_approval: bool = False,
) -> HitlInterruption | None:
    """检测图状态里的 HITL 中断并投影为审批载荷 + 部分消息。

    并行子代理会同时挂起多个中断：全部下发（每条操作带 interrupt_id），而不是只取第一个。
    """
    graph_state = await agent.agent.aget_state(stream_config)
    if not (graph_state.next and graph_state.tasks):
        return None
    interrupts = pending_hitl_interrupts(graph_state)
    if not interrupts:
        return None
    state_msgs = graph_state.values.get("messages", [])
    partial_msg = _partial_message_dict(
        proc, elapsed_time, with_approval=with_approval
    )
    payload = mark_hitl_pending_interrupts(
        partial_msg, interrupts, state_msgs, thread_id
    )
    # 记下审批所在的实例：多副本按 pod 隔离 checkpoint 时，恢复请求必须回到这里（api/chat.py 核对）
    partial_msg["pending_approval"]["instance"] = checkpoint_instance()
    return HitlInterruption(
        action_requests_payload=payload, partial_message=partial_msg
    )


async def plan_resume_for_agent(
    *, agent, stream_config, decisions: list[dict], sid: str = ""
) -> ResumePlan:
    """读取当前待审批中断并规划恢复值；决策与中断对不上时抛 ``HitlDecisionError``。

    读不到图状态（替身 / checkpointer 异常）时退回改造前的直传，不阻断恢复。
    """
    try:
        state = await agent.agent.aget_state(stream_config)
        interrupts = pending_hitl_interrupts(state)
    except Exception as e:  # noqa: BLE001 - 读状态失败不应吞掉用户的审批
        logger.warning(
            f"[{sid}] HITL 恢复: 读取 pending interrupts 失败（回退直传 decisions）: {e}"
        )
        return ResumePlan(
            resume={"decisions": [dict(d) for d in decisions or [] if isinstance(d, dict)]},
            legacy=True,
        )
    plan = plan_resume(interrupts, decisions)
    if len(interrupts) > 1:
        logger.info(
            f"[{sid}] HITL 恢复: {len(interrupts)} 个待审批中断，本次作答 "
            f"{len(plan.answered)} 个（{'旧前端无 interrupt_id，只答第一个' if plan.legacy else '按 interrupt_id'}）"
        )
    return plan


async def validate_resume_decisions(agent, thread_id: str, decisions: list[dict]) -> None:
    """恢复接口的前置校验：决策非法时抛 ``HitlDecisionError``（接口转成 400）。

    批处理 HITL 先存恢复值再校验，非法决策会让线程永远卡在「数量不匹配」；提前拒绝
    才能让用户改正后重试。拿不到图（测试替身等）时跳过，由恢复流自身兜底。
    """
    graph = getattr(agent, "agent", None)
    if graph is None or not hasattr(graph, "aget_state"):
        return
    await plan_resume_for_agent(
        agent=agent,
        stream_config=build_run_config(agent, thread_id),
        decisions=decisions,
    )


async def build_resume_command(
    *, agent, stream_config, decisions: list[dict], sid: str
) -> Command:
    """构造 resume 载荷（兼容入口）：按 interrupt_id 映射，见 :func:`plan_resume`。"""
    plan = await plan_resume_for_agent(
        agent=agent, stream_config=stream_config, decisions=decisions, sid=sid
    )
    return Command(resume=plan.resume)


async def finalize_stream(
    *,
    agent,
    db,
    session_id: str,
    sid: str,
    message_id: str,
    proc,
    start_time: float,
    context_length,
    auto_compress_tokens,
    tool_call_records: list,
    raw_user_msg: str,
    message_content: str = "",
    session_logger=None,
    mode: str = "chat",
    emitter: SSEEmitter | None = None,
) -> AsyncGenerator[str, None]:
    """收尾公共段：最终消息 + 收尾副作用 + ``done`` 事件（chat / resume 共用）。

    chat / resume 在流结束后的差异全部收敛为参数：``mode="chat"`` 的 done 不带
    ``blocks``、usage 不含 ``elapsed_time``，并额外写调试快照与会话日志；
    ``mode="resume"`` 的 done 带权威 ``blocks``、usage 含 ``elapsed_time``。
    resume 特有的 ``merge_tool_call_records`` / ``finalize_blocks`` 由调用方完成后
    传入。本函数只产出最后一帧 ``done``，字段与顺序同拆分前逐字一致。
    """
    is_resume = mode == "resume"
    emitter = emitter or SSEEmitter(session_id)
    accumulated_response = proc.accumulated_response
    accumulated_thinking = proc.accumulated_thinking
    blocks = proc.blocks
    current_step = proc.current_step
    last_context_tokens = proc.last_context_tokens
    elapsed_time = time.time() - start_time

    # 统计（chat 的完成日志先于持久化，resume 的完成日志在副作用之后，沿用原文案）
    thinking_time = None
    if not is_resume:
        thinking_time = thinking_duration(blocks)
        tool_duration = (
            sum(tc[5] for tc in tool_call_records) if tool_call_records else 0
        )
        logger.info(
            f"[{sid}] ✅ 流式响应完成 | 总步骤: {current_step} | "
            f"总耗时: {elapsed_time:.2f}s | 思考: {thinking_time}s | "
            f"回复: 0s | 工具: {tool_duration:.2f}s | "
            f"思考长度: {len(accumulated_thinking)} | "
            f"回复长度: {len(accumulated_response)} 字符"
        )

    # 展示/持久化口径：**最近一次模型调用**的用量（不跨步累加）；
    # 日志里的"本轮累计"仍用累加值，便于核对计费与排查。
    last_usage = proc.last_usage
    inp_tokens = last_usage["input_tokens"]
    out_tokens = last_usage["output_tokens"]
    rea_tokens = last_usage.get("reasoning_tokens", 0)
    sum_tokens = inp_tokens + out_tokens

    if not is_resume:
        logger.info(
            f"[{sid}] 📊 Token | 当前 step: ↑{inp_tokens} ↓{out_tokens}"
            f"(思考{rea_tokens}) Σ{sum_tokens}"
        )
        if accumulated_thinking:
            logger.info(f"[{sid}] 🤔 思考内容(前200字):\n{accumulated_thinking[:200]}")
        if accumulated_response:
            logger.info(f"[{sid}] 💬 回复内容(前200字):\n{accumulated_response[:200]}")

    # 持久化最终消息
    await run_blocking(
        persist_final_message,
        db,
        session_id,
        build_assistant_message_dict(
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
        ),
    )

    if not is_resume:
        # 调试用 context 快照（默认关闭，EASY_WRITE_SESSION_CONTEXT=1 开启）
        write_session_context_snapshot(
            db=db,
            session_id=session_id,
            message_id=message_id,
            message_content=message_content,
            accumulated_response=accumulated_response,
            accumulated_thinking=accumulated_thinking,
            thinking_time=thinking_time,
            tool_call_records=tool_call_records,
            sid=sid,
        )

    # 收尾副作用顺序固定：workspace rename → 技能入库 → 记忆更新（chat / resume 一致）
    await maybe_rename_workspace(agent, db, session_id, tool_call_records, sid)
    await register_session_skills(agent, db, session_id, sid)
    submit_memory_updates(
        agent, session_id, db, raw_user_msg, accumulated_response, sid
    )

    if not is_resume and session_logger:
        session_logger.log_assistant_response(
            content=accumulated_response,
            thinking=accumulated_thinking or None,
            tool_calls=tool_call_records_to_dicts(tool_call_records, 1000) or None,
        )

    usage_payload = build_usage_payload(
        last_usage=last_usage,
        context_length=context_length,
        auto_compress_tokens=auto_compress_tokens,
        last_context_tokens=last_context_tokens,
        step_count=current_step,
        elapsed_time=elapsed_time if is_resume else None,
    )
    if is_resume:
        logger.info(
            f"[{sid}] ✅ 流式响应完成 | 总步骤: {current_step} | "
            f"总耗时: {elapsed_time:.2f}s | tokens={sum_tokens} "
            f"(in={inp_tokens}, out={out_tokens}, think={rea_tokens})"
        )
    # 键序契约（与迁移前字面量一致）：type → session_id → elapsed_time →
    # usage；resume 的 blocks 必须排在 usage 之后（chat 不带 blocks）。
    done_fields = {
        "elapsed_time": round(elapsed_time, 2),
        "usage": usage_payload,
    }
    if is_resume:
        # 权威最终 blocks（含工具结果/耗时），前端据此替换内存 blocks，避免依赖
        # tool_result 事件匹配（HITL 恢复流曾因匹配失败导致工具一直"执行中"）。
        done_fields["blocks"] = proc._sse_blocks()
    yield emitter.done(session_id=session_id, **done_fields)


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
    pinned_skills: list[str] | None = None,
) -> AsyncGenerator[str, None]:
    start_time = time.time()
    sid = session_id[-5:] if session_id else "new"
    emitter = SSEEmitter(session_id)

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

    context_length = None
    auto_compress_tokens = None
    result_log_truncate = 200
    try:

        yield emitter.start()

        # ── 配置 ─────────────────────────────────────────────────
        context_length, auto_compress_tokens, result_log_truncate = (
            resolve_chat_context(agent)
        )

        # ── StreamProcessor（混合流式：updates 权威 + messages token 流式）──
        proc = StreamProcessor(
            sid=sid, session_id=session_id, db=db, message_id=message_id,
            session_logger=session_logger,
            context_length=context_length,
            auto_compress_tokens=auto_compress_tokens,
            start_time=start_time,
        )

        persister = IncrementalPersister(
            db=db,
            session_id=session_id,
            sid=sid,
            start_time=start_time,
        )

        # ── 构建上下文消息 ─────────────────────────────────────────────
        # session.messages 末尾是本次刚写入 DB 的当前用户消息（chat.py 在调用
        # 本生成器前已 add_message），需排除以免与下方 message_content 重复注入。
        # 超长时由官方 SummarizationMiddleware 在模型调用前按 token 阈值自动摘要。
        session = await run_blocking(db.get_session, session_id)
        total_msgs = len(session.messages) if session and session.messages else 0
        history_messages = session.messages[:-1] if total_msgs else []
        provider = agent.config.llm.provider.lower() if agent and agent.config else ""
        # DA-03：thread 里的本轮消息只存用户原话（与 DB 一致）；工作区前缀、知识库 /
        # 页面上下文、上传预览等本轮注入（message_content）走 turn_context，只进本轮
        # 模型请求，见 agent/turn_context.py。开关 agent.session_thread 打开时一个会话
        # 一个 thread，只输入本轮消息（及需要补齐的内容），见 services/session_thread.py。
        plan = await plan_turn(
            agent,
            session_id=session_id,
            turn_id=message_id,
            history=history_messages,
            raw=turn_message_text(request.message, request.files, request.skills),
            provider=provider,
        )
        context_messages = plan.messages
        logger.info(
            f"[{sid}] 📚 上下文消息构建 | DB 消息数: {total_msgs} | thread: {plan.mode} | "
            f"输入消息: {len(context_messages)} | 补齐 DB 行: {plan.caught_up_rows} | "
            f"补回半截回复: {plan.restored_tail} | 压缩: 官方 SummarizationMiddleware"
        )
        if plan.checkpoint_id:
            record_turn_checkpoint(db, session_id, message_id, plan.checkpoint_id, sid)

        # ── 启动流式输出 (stream_mode=['messages', 'updates']) ─────────
        logger.info(f"[{sid}] 🚀 使用 stream_mode=['messages', 'updates'] 流式接口")

        # 审批句柄始终是 {session_id}-{message_id}（前端据此还原 message_id）；
        # 每消息模式下它就是 thread_id，会话 thread 模式下 thread_id = session_id。
        thread_id = plan.thread_id
        handle = approval_handle(session_id, message_id)
        stream_config = build_run_config(agent, thread_id)

        async for ev in pump_stream(
            agent=agent,
            stream_input=turn_input(
                context_messages,
                message_id=message_id,
                rendered=message_content,
                pinned_skills=pinned_skills,
            ),
            stream_config=stream_config,
            proc=proc,
            persister=persister,
            db=db,
            session_id=session_id,
            sid=sid,
            message_id=message_id,
            session_logger=session_logger,
            context=getattr(agent, "session_context", None),
        ):
            yield emitter.frame(ev)

        # ── Post-streaming: 流结束 ──────────────────────────────────────
        logger.info(f"[{sid}] 📢 流式输出结束")

        # 确保 thinking 结束（流结束时若仍在思考）
        for _ev in proc._end_thinking():
            yield emitter.frame(_ev)

        # content_end 需要累计正文；其余状态由 finalize_stream 从 proc 读取
        accumulated_response = proc.accumulated_response

        # 发送 content_end
        if accumulated_response:
            yield emitter.content_end()

        # ── HITL: 检测中断（文件删除审批） ────────────────────────────
        try:
            interruption = await detect_hitl_interruption(
                agent=agent,
                stream_config=stream_config,
                proc=proc,
                elapsed_time=time.time() - start_time,
                thread_id=handle,
            )
            if interruption:
                # 保存部分 assistant 消息（复用取消路径模式）
                await run_blocking(
                    db.update_last_assistant_message,
                    session_id,
                    interruption.partial_message,
                )
                logger.info(
                    f"[{sid}] 🔔 HITL 中断 | thread_id={thread_id} | 审批句柄={handle} | "
                    f"{len(interruption.action_requests_payload)} 个操作待审批"
                )
                yield emitter.approval_required(
                    thread_id=handle,
                    action_requests=interruption.action_requests_payload,
                )
                return
        except Exception as e:
            logger.warning(f"[{sid}] HITL 中断检测异常: {e}", exc_info=True)

        # ── 收尾：最终消息 + 副作用 + done（chat：无 blocks、usage 无 elapsed_time）──
        async for frame in finalize_stream(
            agent=agent,
            db=db,
            session_id=session_id,
            sid=sid,
            message_id=message_id,
            proc=proc,
            start_time=start_time,
            context_length=context_length,
            auto_compress_tokens=auto_compress_tokens,
            tool_call_records=_tool_call_tuples(proc.blocks),
            raw_user_msg=parsed_content or request.message,
            message_content=message_content,
            session_logger=session_logger,
            emitter=emitter,
        ):
            yield frame

    except asyncio.CancelledError:
        accumulated_response = proc.accumulated_response
        accumulated_thinking = proc.accumulated_thinking
        blocks = proc.blocks
        tool_call_records = _tool_call_tuples(proc.blocks)
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
            await run_blocking(
                persist_partial,
                proc,
                db=db,
                session_id=session_id,
                sid=sid,
                start_time=start_time,
                raise_on_error=True,
            )
            logger.info(
                f"[{sid}] 💾 取消时已保存部分回复 | 长度: {len(accumulated_response)}"
            )
        except Exception as e:
            logger.warning(f"[{sid}] 取消时保存失败: {e}")
        yield emitter.error("请求被取消")
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
            yield emitter.emit(
                SSEEvent.THINKING_END, duration=dur, step=current_step
            )
        for blk in reversed(blocks):
            if blk.get("type") == "tool_call" and blk.get("duration") is None:
                blk["success"] = False
                blk["result"] = f"执行中断: {str(e)[:result_log_truncate]}"
                blk["duration"] = 0
                break
        yield emitter.error(f"处理失败: {str(e)}")

async def resume_stream_generator(
    db: Database,
    agent: EasyAgent,
    session_id: str,
    thread_id: str,
    decisions: list[dict],
    username: str = "",
    session_logger: SessionLogger = None,
    message_id: str = "",
    handle: str | None = None,
) -> AsyncGenerator[str, None]:
    """HITL 恢复流：用户审批后继续执行 Agent。

    从 DB 加载中断前的部分 assistant 消息，初始化状态，
    调用 agent.astream(Command(resume=...)) 继续执行。

    ``thread_id`` 是真正恢复的 thread；``handle`` 是给前端的审批句柄（DA-03：会话 thread
    模式下两者不同），缺省时与 ``thread_id`` 相同。
    """
    handle = handle or thread_id
    start_time = time.time()
    sid = session_id[-5:] if session_id else "resume"
    emitter = SSEEmitter(session_id)

    yield emitter.start()

    # 从 DB 加载部分 assistant 消息
    session = None
    try:
        session = await run_blocking(db.get_session, session_id)
        snapshot = load_resume_snapshot(session.messages if session else [])
    except Exception as e:
        logger.warning(f"[{sid}] HITL 恢复: 加载部分消息失败: {e}")
        snapshot = load_resume_snapshot([])

    accumulated_response = snapshot.accumulated_response
    accumulated_thinking = snapshot.accumulated_thinking
    blocks = snapshot.blocks
    tool_call_records = snapshot.tool_call_records
    current_step = snapshot.current_step
    last_context_tokens = 0
    pending_tool_call_ids = snapshot.pending_tool_call_ids
    original_user_msg = ""  # 触发 HITL 的原始用户消息，供记忆生成使用

    # 捕获触发 HITL 的原始用户消息（会话中最后一条 user 消息），供记忆生成使用
    try:
        if session and session.messages:
            original_user_msg = last_user_message(session.messages)
    except Exception:
        pass

    # 与主生成器同一套 config（含按编译图节点数计算的 recursion_limit 兜底）。
    stream_config = build_run_config(agent, thread_id)

    # 按 interrupt_id 规划恢复值（DA-02）；接口已前置校验，这里只处理并发竞态。
    try:
        plan = await plan_resume_for_agent(
            agent=agent, stream_config=stream_config, decisions=decisions, sid=sid
        )
    except HitlDecisionError as e:
        logger.warning(f"[{sid}] HITL 恢复: 审批决策无效: {e}")
        yield emitter.error(f"审批决策无效：{e}")
        return

    # 将审批决策立即应用到内存中的 blocks / tool_call_records：
    # 1) 前端历史渲染优先读 blocks，只写 tool_calls 不够；
    # 2) 恢复流结束后会用内存数据整条覆盖持久化（build_assistant_message_dict），
    #    若只 patch 数据库（_persist_approval_decisions），最终写回会把状态冲回 "pending"。
    # 只标记本次作答的中断；改造前落库的记录没有 interrupt_id，退回按 pending 块顺序的旧口径。
    decision_status_by_id = decision_status_by_interrupt(
        plan.answered, snapshot.pending_action_requests
    )
    snapshot_has_interrupt_ids = any(
        ar.get("interrupt_id") for ar in snapshot.pending_action_requests
    )
    if not decision_status_by_id and not snapshot_has_interrupt_ids:
        pending_decisions = normalize_resume_decisions(
            decisions, hanging_tool_count=len(pending_tool_call_ids)
        )
        decision_status_by_id = decision_status_map(
            pending_tool_call_ids, pending_decisions
        )
    apply_decision_status_to_records(tool_call_records, decision_status_by_id)
    apply_decision_status_to_blocks(blocks, decision_status_by_id)

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

    context_length, auto_compress_tokens = resolve_resume_context(agent)

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

    # 多个 pending interrupt（并行子代理各自触发 HITL）时 LangGraph 要求
    # {interrupt_id: value} 映射；未作答的中断保持挂起，流结束后再次下发审批。
    resume_command = Command(resume=plan.resume)

    persister = IncrementalPersister(
        db=db,
        session_id=session_id,
        sid=sid,
        start_time=start_time,
    )

    try:
        async for ev in pump_stream(
            agent=agent,
            stream_input=resume_command,
            stream_config=stream_config,
            proc=proc,
            persister=persister,
            db=db,
            session_id=session_id,
            sid=sid,
            message_id=message_id,
            session_logger=session_logger,
            context=getattr(agent, "session_context", None),
        ):
            yield emitter.frame(ev)

        # ── Post-streaming ─────────────────────────────────────────
        # 确保 thinking 结束（流结束时若仍在思考）
        for _ev in proc._end_thinking():
            yield emitter.frame(_ev)

        # 同步本地状态别名（供 content_end / 去重 / finalize_stream 使用）
        accumulated_response = proc.accumulated_response
        blocks = proc.blocks
        tool_call_records = _tool_call_tuples(proc.blocks, with_approval=True)
        current_step = proc.current_step
        logger.info(
            f"[{sid}] 📢 流式输出结束 | steps={current_step} | blocks={len(blocks)}"
        )

        if accumulated_response:
            yield emitter.content_end()

        # ── HITL: 检测嵌套中断 ─────────────────────────────────────
        try:
            interruption = await detect_hitl_interruption(
                agent=agent,
                stream_config=stream_config,
                proc=proc,
                elapsed_time=time.time() - start_time,
                thread_id=handle,
                with_approval=True,
            )
            if interruption:
                await run_blocking(
                    db.update_last_assistant_message,
                    session_id,
                    interruption.partial_message,
                )
                # 键序契约（与迁移前字面量一致）：type → thread_id → blocks →
                # action_requests。快捷方法把 action_requests 放在 blocks 之前，
                # 故此处必须用 emit() 显式排序，不得改用 approval_required()。
                yield emitter.emit(
                    SSEEvent.APPROVAL_REQUIRED,
                    thread_id=handle,
                    # 嵌套中断时也下发当前 blocks，前端据此刷新已完成的工具结果
                    blocks=proc._sse_blocks(),
                    action_requests=interruption.action_requests_payload,
                )
                return
        except Exception as e:
            logger.warning(
                f"[{sid}] HITL 恢复: 嵌套中断检测异常: {e}", exc_info=True
            )
        finally:
            # 无论正常结束、嵌套中断还是异常，均把审批决策写回，保证切换会话后仍可见
            await run_blocking(
                persist_approval_decisions, db, session_id, decision_status_by_id, sid
            )

        # 恢复流重跑工具时会再次 append 同 tool_call_id 的记录（DB 加载的 + 重跑时新增的），
        # 按 tool_call_id 去重并合并审批状态：保留有结果的记录，继承非空 approval_status，
        # 避免最终消息出现重复工具卡、且审批标记（已批准）不被无状态的新记录冲掉。
        tool_call_records = merge_tool_call_records(tool_call_records)

        # blocks 同样需要 finalize：astream 重放可能新建同 tool_call_id/同名 block，
        # 且内存 blocks 的 approval_status 可能在重放中被遗漏。
        blocks = finalize_blocks(blocks, decision_status_by_id)
        proc.blocks = blocks  # 同步去重后的 blocks，确保 done 事件 _sse_blocks() 一致

        # ── 收尾：最终消息 + 副作用 + done（resume：带权威 blocks、usage 含 elapsed_time）──
        async for frame in finalize_stream(
            agent=agent,
            db=db,
            session_id=session_id,
            sid=sid,
            message_id=message_id,
            proc=proc,
            start_time=start_time,
            context_length=context_length,
            auto_compress_tokens=auto_compress_tokens,
            tool_call_records=tool_call_records,
            raw_user_msg=original_user_msg,
            session_logger=session_logger,
            mode="resume",
            emitter=emitter,
        ):
            yield frame

    except asyncio.CancelledError:
        logger.info(f"[{sid}] HITL 恢复被取消")
        accumulated_response = proc.accumulated_response
        accumulated_thinking = proc.accumulated_thinking
        blocks = proc.blocks
        tool_call_records = _tool_call_tuples(proc.blocks, with_approval=True)
        last_context_tokens = proc.last_context_tokens
        current_step = proc.current_step
        await run_blocking(
            persist_partial,
            proc,
            db=db,
            session_id=session_id,
            sid=sid,
            start_time=start_time,
            with_approval=True,
            raise_on_error=True,
        )
        yield emitter.error("请求被取消")
    except Exception as e:
        logger.error(f"[{sid}] HITL 恢复异常: {str(e)}", exc_info=True)
        # 持久化已生成的部分回复（与取消路径一致），避免流式过程中已完成的
        # 思考/工具/正文在异常后丢失，导致历史会话中 HITL 之后的记录整体消失。
        await run_blocking(
            persist_partial,
            proc,
            db=db,
            session_id=session_id,
            sid=sid,
            start_time=start_time,
            with_approval=True,
        )
        yield emitter.error(f"处理失败: {str(e)}")
