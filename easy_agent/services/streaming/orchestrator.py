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
from easy_agent.services.agents.manager import get_agent_config
from easy_agent.services.streaming.approvals import (
    HitlDecisionError,
    ResumePlan,
    normalize_resume_decisions,
    pending_hitl_interrupts,
    plan_resume,
)
from easy_agent.services.streaming.sse import SSEEvent
from easy_agent.services.streaming.emitter import SSEEmitter
from easy_agent.services.streaming.messages import (
    _partial_message_dict,
    _tool_call_tuples,
    build_assistant_message_dict,
    tool_call_records_to_dicts,
    turn_message_text,
)
from easy_agent.services.streaming.processor import StreamProcessor
from easy_agent.services.sessions.thread import (
    StaleApprovalError,
    approval_handle,
    plan_turn,
    record_turn_checkpoint,
)
from easy_agent.services.streaming.persistence import (
    IncrementalPersister,
    persist_approval_decisions,
    persist_final_message,
    persist_partial,
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

#: 主代理模型调用次数用尽时的对外文案。
#:
#: 原始异常文本是 ``Model call limits exceeded: run limit (100/100)``——``run limit``
#: 对业务方没有任何意义，还容易被当成系统故障。日志里仍保留原始异常与堆栈，
#: 只有给用户/调用方看的那一句换掉。
LIMIT_EXCEEDED_MESSAGE = "执行步数用尽，请缩小分析范围或明确数据位置"

#: 模型流式静默超时的对外文案（**常驻**在消息尾部，不是弹框）。
#:
#: 触发场景：``langchain_openai`` 的 ``stream_chunk_timeout``（默认 120s）在「已收到
#: 若干 chunk 之后」突然收不到任何 chunk 时抛 ``StreamChunkTimeoutError``。此时上游
#: 已经吐出的正文/工具结果都还在，只是**这一轮没跑完**——所以不换成红色报错，而是
#: 在回答下方挂一条中性说明，并在 error 帧带 ``code="timeout"`` 让前端据此分流。
TIMEOUT_FALLBACK_NOTE = "agent服务出现异常情况，访问超时，数据返回不全，稍后请重试！"

#: ``error`` 事件的可选机器可读标识（见 ``sse.py`` 的 ``EVENT_SPECS[ERROR]``）。
ERROR_CODE_TIMEOUT = "timeout"

#: 按**类名**识别超时异常的兜底集合。
#:
#: ``StreamChunkTimeoutError``（langchain_openai）、``APITimeoutError``（openai）、
#: ``TimeoutException`` 及其子类（httpx）来自第三方库，硬 import 会把它们变成运行期
#: 必需依赖（单测环境可能只装了部分）；按 MRO 类名匹配既无依赖风险，又能覆盖这些库
#: 任意版本的继承关系。``asyncio.TimeoutError`` 另由 ``isinstance`` 直接判定。
_TIMEOUT_EXC_NAMES = frozenset(
    {
        "StreamChunkTimeoutError",
        "APITimeoutError",
        "TimeoutException",
        "ReadTimeout",
        "WriteTimeout",
        "ConnectTimeout",
        "PoolTimeout",
        "Timeout",
    }
)

try:  # 旧版 langchain 没有该中间件；导入失败时按普通异常处理
    from langchain.agents.middleware.model_call_limit import (
        ModelCallLimitExceededError as _ModelCallLimitExceededError,
    )
except ImportError:  # pragma: no cover - 依赖版本兜底
    _ModelCallLimitExceededError = None  # type: ignore[assignment]


def friendly_error_message(exc: BaseException) -> str:
    """超限异常换成可读文案，其余异常原样返回（保留类型与细节，便于排查）。"""
    if _ModelCallLimitExceededError is not None and isinstance(
        exc, _ModelCallLimitExceededError
    ):
        return LIMIT_EXCEEDED_MESSAGE
    return str(exc)


def is_timeout_error(exc: BaseException | None) -> bool:
    """判断异常是否为「等待上游超时」（模型流式静默 / HTTP 超时）。

    判定顺序（任一命中即真）：

    1. ``asyncio.TimeoutError``（``StreamChunkTimeoutError`` 就是它的子类）；
    2. MRO 里出现 ``_TIMEOUT_EXC_NAMES`` 中的类名（覆盖 openai / httpx 各自的
       超时体系，它们并不都继承 ``TimeoutError``）；
    3. 顺着 ``__cause__`` / ``__context__`` 往下看，最多 5 层——SDK 常把底层超时包成
       ``APIConnectionError`` 之类再抛出，只看最外层会漏判。

    只用于「异常兜底怎么展示」这一个决策，不参与业务分支；判定失败的最坏结果是
    按原有的「处理失败: xxx」展示，不会吞掉内容。
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    depth = 0
    while current is not None and depth < 5 and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (asyncio.TimeoutError, TimeoutError)):
            return True
        if any(cls.__name__ in _TIMEOUT_EXC_NAMES for cls in type(current).__mro__):
            return True
        current = current.__cause__ or current.__context__
        depth += 1
    return False


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
    if not interrupts:
        raise StaleApprovalError(
            "找不到这条审批的状态（可能在另一个实例上，或已过期），请重新发送消息"
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


@dataclass(frozen=True)
class _FinalizeStats:
    """收尾阶段的一次性统计（纯计算，无副作用；chat / resume 共用）。"""

    elapsed_time: float
    thinking_time: float | None
    tool_duration: float
    inp_tokens: int
    out_tokens: int
    rea_tokens: int
    sum_tokens: int


def _finalize_stats(proc, tool_call_records: list, start_time: float, *, is_resume: bool) -> _FinalizeStats:
    """汇总耗时与 token：展示/持久化口径是**最近一次模型调用**的用量（不跨步累加）。"""
    elapsed_time = time.time() - start_time
    # thinking_time / tool_duration 只在 chat 分支计算（resume 的历史 tool_call_records
    # 允许 duration=None，原实现同样不在 resume 分支求和）。
    thinking_time = None
    tool_duration = 0.0
    if not is_resume:
        thinking_time = thinking_duration(proc.blocks)
        tool_duration = (
            sum(tc[5] for tc in tool_call_records) if tool_call_records else 0
        )
    last_usage = proc.last_usage
    inp_tokens = last_usage["input_tokens"]
    out_tokens = last_usage["output_tokens"]
    rea_tokens = last_usage.get("reasoning_tokens", 0)
    return _FinalizeStats(
        elapsed_time=elapsed_time,
        thinking_time=thinking_time,
        tool_duration=tool_duration,
        inp_tokens=inp_tokens,
        out_tokens=out_tokens,
        rea_tokens=rea_tokens,
        sum_tokens=inp_tokens + out_tokens,
    )


def _log_stream_completion(sid: str, proc, stats: _FinalizeStats) -> None:
    """chat 的完成日志（先于持久化，沿用原文案）。"""
    logger.info(
        f"[{sid}] ✅ 流式响应完成 | 总步骤: {proc.current_step} | "
        f"总耗时: {stats.elapsed_time:.2f}s | 思考: {stats.thinking_time}s | "
        f"回复: 0s | 工具: {stats.tool_duration:.2f}s | "
        f"思考长度: {len(proc.accumulated_thinking)} | "
        f"回复长度: {len(proc.accumulated_response)} 字符"
    )


def _log_token_usage(sid: str, proc, stats: _FinalizeStats) -> None:
    """chat 的 token / 思考与回复预览日志（先于持久化）。"""
    logger.info(
        f"[{sid}] 📊 Token | 当前 step: ↑{stats.inp_tokens} ↓{stats.out_tokens}"
        f"(思考{stats.rea_tokens}) Σ{stats.sum_tokens}"
    )
    if proc.accumulated_thinking:
        logger.info(f"[{sid}] 🤔 思考内容(前200字):\n{proc.accumulated_thinking[:200]}")
    if proc.accumulated_response:
        logger.info(f"[{sid}] 💬 回复内容(前200字):\n{proc.accumulated_response[:200]}")


def _build_final_message(
    proc, stats: _FinalizeStats, tool_call_records: list, context_length
) -> dict:
    """组装最终 assistant 消息（纯计算）。"""
    return build_assistant_message_dict(
        content=proc.accumulated_response,
        thinking=proc.accumulated_thinking,
        thinking_duration=stats.thinking_time,
        tool_call_records=tool_call_records,
        blocks=proc.blocks,
        input_tokens=stats.inp_tokens,
        output_tokens=stats.out_tokens,
        reasoning_tokens=stats.rea_tokens,
        context_tokens=proc.last_context_tokens,
        elapsed_time=stats.elapsed_time,
        step_count=proc.current_step,
        result_limit=None,
        context_length=context_length,
    )


async def _persist_final_message(db, session_id: str, message: dict) -> None:
    """最终消息落库（放进线程，避免阻塞事件循环）。"""
    await run_blocking(persist_final_message, db, session_id, message)


async def _write_debug_snapshot(
    *,
    db,
    session_id: str,
    message_id: str,
    message_content: str,
    proc,
    stats: _FinalizeStats,
    tool_call_records: list,
    sid: str,
) -> None:
    """调试用 context 快照（默认关闭，EASY_WRITE_SESSION_CONTEXT=1 开启）。"""
    await run_blocking(
        write_session_context_snapshot,
        db=db,
        session_id=session_id,
        message_id=message_id,
        message_content=message_content,
        accumulated_response=proc.accumulated_response,
        accumulated_thinking=proc.accumulated_thinking,
        thinking_time=stats.thinking_time,
        tool_call_records=tool_call_records,
        sid=sid,
    )


async def _run_post_stream_side_effects(
    *,
    agent,
    db,
    session_id: str,
    sid: str,
    tool_call_records: list,
    raw_user_msg: str,
    accumulated_response: str,
    accumulated_thinking: str,
    session_logger,
    is_resume: bool,
) -> None:
    """收尾副作用：顺序固定为 workspace rename → 技能入库 → 记忆更新（chat / resume 一致）。"""
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


def _build_done_payload(
    proc,
    stats: _FinalizeStats,
    *,
    context_length,
    auto_compress_tokens,
    is_resume: bool,
) -> tuple[dict, dict]:
    """组装 usage 与 done 字段（键序与迁移前字面量一致）。"""
    usage_payload = build_usage_payload(
        last_usage=proc.last_usage,
        context_length=context_length,
        auto_compress_tokens=auto_compress_tokens,
        last_context_tokens=proc.last_context_tokens,
        step_count=proc.current_step,
        elapsed_time=stats.elapsed_time if is_resume else None,
    )
    # 键序契约（与迁移前字面量一致）：type → session_id → elapsed_time →
    # usage；resume 的 blocks 必须排在 usage 之后（chat 不带 blocks）。
    done_fields = {
        "elapsed_time": round(stats.elapsed_time, 2),
        "usage": usage_payload,
    }
    if is_resume:
        # 权威最终 blocks（含工具结果/耗时），前端据此替换内存 blocks，避免依赖
        # tool_result 事件匹配（HITL 恢复流曾因匹配失败导致工具一直"执行中"）。
        done_fields["blocks"] = proc._sse_blocks()
    return usage_payload, done_fields


def _log_resume_completion(sid: str, proc, stats: _FinalizeStats) -> None:
    """resume 的完成日志（在副作用之后，沿用原文案）。"""
    logger.info(
        f"[{sid}] ✅ 流式响应完成 | 总步骤: {proc.current_step} | "
        f"总耗时: {stats.elapsed_time:.2f}s | tokens={stats.sum_tokens} "
        f"(in={stats.inp_tokens}, out={stats.out_tokens}, think={stats.rea_tokens})"
    )



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
    stats = _finalize_stats(
        proc, tool_call_records, start_time, is_resume=is_resume
    )

    if not is_resume:
        _log_stream_completion(sid, proc, stats)
        _log_token_usage(sid, proc, stats)

    # 持久化最终消息
    await _persist_final_message(
        db, session_id, _build_final_message(proc, stats, tool_call_records, context_length)
    )

    if not is_resume:
        await _write_debug_snapshot(
            db=db,
            session_id=session_id,
            message_id=message_id,
            message_content=message_content,
            proc=proc,
            stats=stats,
            tool_call_records=tool_call_records,
            sid=sid,
        )

    await _run_post_stream_side_effects(
        agent=agent,
        db=db,
        session_id=session_id,
        sid=sid,
        tool_call_records=tool_call_records,
        raw_user_msg=raw_user_msg,
        accumulated_response=proc.accumulated_response,
        accumulated_thinking=proc.accumulated_thinking,
        session_logger=session_logger,
        is_resume=is_resume,
    )

    _usage_payload, done_fields = _build_done_payload(
        proc,
        stats,
        context_length=context_length,
        auto_compress_tokens=auto_compress_tokens,
        is_resume=is_resume,
    )
    if is_resume:
        _log_resume_completion(sid, proc, stats)
    yield emitter.done(session_id=session_id, **done_fields)


@dataclass
class StreamRun:
    """一次 chat / resume 流式运行的共享上下文（主循环、收尾与兜底所需）。

    两个公开生成器只负责准备各自的输入（``turn_input(...)`` / ``Command(resume=...)``）
    与这里的元数据差异；事件顺序、副作用调用顺序、落库时机由 :func:`_run_stream`
    唯一实现。
    """

    agent: EasyAgent
    db: Database
    session_id: str
    sid: str
    message_id: str
    emitter: SSEEmitter
    proc: StreamProcessor
    persister: IncrementalPersister
    stream_config: dict
    start_time: float
    context_length: int | None
    auto_compress_tokens: int | None
    thread_id: str
    handle: str
    raw_user_msg: str
    mode: str = "chat"
    message_content: str = ""
    session_logger: SessionLogger | None = None
    result_log_truncate: int = 200
    decision_status_by_id: dict | None = None


async def _save_partial(
    run: StreamRun,
    *,
    with_approval: bool = False,
    raise_on_error: bool = False,
    fallback_note: str | None = None,
) -> None:
    """在线程里保存「进行中」的 assistant 消息（取消 / 异常兜底共用）。

    ``fallback_note`` 由异常兜底传入超时文案，随消息落库（前端整表刷新后仍能显示）。
    """
    await run_blocking(
        persist_partial,
        run.proc,
        db=run.db,
        session_id=run.session_id,
        sid=run.sid,
        start_time=run.start_time,
        with_approval=with_approval,
        raise_on_error=raise_on_error,
        fallback_note=fallback_note,
    )


async def _stream_cancelled_frames(run: StreamRun) -> AsyncGenerator[str, None]:
    """取消兜底：先等线程里在途的写入完成，再保存部分回复（chat / resume 共用）。

    ``run_blocking`` 在调用方被取消时会等线程里的写库做完再抛出，保证「取消时保存
    的部分回复」不会和在途写入交叉覆盖（见 ``utils/blocking.py``）。
    """
    proc = run.proc
    if run.mode == "resume":
        logger.info(f"[{run.sid}] HITL 恢复被取消")
        await _save_partial(run, with_approval=True, raise_on_error=True)
    else:
        logger.info(
            f"[{run.sid}] ❌ 请求被取消 | 当前 step={proc.current_step} | "
            f"已累积回复 {len(proc.accumulated_response)} 字符 | "
            f"已记录 {len(_tool_call_tuples(proc.blocks))} 个工具调用 | "
            f"思考中={proc.is_in_thinking}"
        )
        try:
            await _save_partial(run, raise_on_error=True)
            logger.info(
                f"[{run.sid}] 💾 取消时已保存部分回复 | "
                f"长度: {len(proc.accumulated_response)}"
            )
        except Exception as e:
            logger.warning(f"[{run.sid}] 取消时保存失败: {e}")
    yield run.emitter.error("请求被取消")


async def _stream_error_frames(
    run: StreamRun, exc: BaseException
) -> AsyncGenerator[str, None]:
    """异常兜底：chat 修正思考 / 工具状态，resume 保存部分回复，最后统一 error 帧。

    超时类异常（模型流式静默，见 :func:`is_timeout_error`）额外做三件事，三件事
    **必须按此顺序**，否则前端看到的与落库的会不一致：

    1. 修正 blocks（思考结束、未完成工具标记「执行中断」）；
    2. ``_save_partial(fallback_note=...)``：把半成品**连同兜底文案**落库——前端刷新
       走 ``syncSessionFromServer`` 整表替换，不落库就会被更短的 DB 版本覆盖；
    3. 最后才发 ``error`` 帧（带 ``code="timeout"``），前端据此只渲染常驻灰条。

    非超时异常完全走改动前的路径（文案、字段、是否落库的差异见下方注释）。
    """
    proc = run.proc
    timed_out = is_timeout_error(exc)
    fallback_note = TIMEOUT_FALLBACK_NOTE if timed_out else None
    if timed_out:
        # 结构化 stall 日志：这三个属性由 StreamChunkTimeoutError 自带，便于统计
        # 「哪个模型 / 什么时段爱卡」。中文用 info 级不够——它是真故障，仍用 error。
        logger.error(
            f"[{run.sid}] ⏱️ 模型流式超时（{type(exc).__name__}）| "
            f"step={proc.current_step} | "
            f"已累积回复 {len(proc.accumulated_response)} 字符 | "
            f"已记录 {len(_tool_call_tuples(proc.blocks))} 个工具调用 | "
            f"timeout_s={getattr(exc, 'timeout_s', None)} | "
            f"model={getattr(exc, 'model_name', None)} | "
            f"chunks_received={getattr(exc, 'chunks_received', None)} | "
            f"detail={exc}"
        )
    if run.mode == "resume":
        logger.error(f"[{run.sid}] HITL 恢复异常: {str(exc)}", exc_info=True)
        # 持久化已生成的部分回复（与取消路径一致），避免流式过程中已完成的
        # 思考/工具/正文在异常后丢失，导致历史会话中 HITL 之后的记录整体消失。
        await _save_partial(
            run, with_approval=True, fallback_note=fallback_note
        )
    else:
        logger.error(f"[{run.sid}] ❌ 聊天异常: {str(exc)}", exc_info=True)
        blocks = proc.blocks
        if proc.is_in_thinking and proc.thinking_start_time:
            dur = round(time.time() - proc.thinking_start_time, 2)
            if blocks and blocks[-1]["type"] == "thinking":
                blocks[-1]["duration"] = dur
            proc.is_in_thinking = False
            yield run.emitter.emit(
                SSEEvent.THINKING_END, duration=dur, step=proc.current_step
            )
        for blk in reversed(blocks):
            if blk.get("type") == "tool_call" and blk.get("duration") is None:
                blk["success"] = False
                blk["result"] = (
                    f"执行中断: {friendly_error_message(exc)[:run.result_log_truncate]}"
                )
                blk["duration"] = 0
                break
        # 修正历史遗留：普通对话的异常分支此前**从不落库**（取消 / HITL 恢复都会存），
        # 于是刷新后 DB 里那条更短的旧版本会把屏幕上已生成的内容顶掉。这里补齐，
        # 与取消路径一致；失败只记日志，不影响下面的 error 帧。
        try:
            await _save_partial(run, fallback_note=fallback_note)
            logger.info(
                f"[{run.sid}] 💾 异常时已保存部分回复 | "
                f"长度: {len(proc.accumulated_response)}"
            )
        except Exception as save_exc:  # noqa: BLE001 - 保存失败不能吃掉 error 帧
            logger.warning(f"[{run.sid}] 异常时保存部分回复失败: {save_exc}")
    if fallback_note:
        # 超时：文案即兜底说明，code 让前端走「常驻灰条」而不是红色报错。
        yield run.emitter.error(fallback_note, code=ERROR_CODE_TIMEOUT)
    else:
        yield run.emitter.error(f"处理失败: {friendly_error_message(exc)}")


async def _run_stream(run: StreamRun, stream_input) -> AsyncGenerator[str, None]:
    """chat / resume 共用的流主循环、HITL 收尾与取消 / 异常兜底。

    ``stream_input`` 是唯一的输入差异（新消息 vs ``Command(resume=...)``）；事件顺序、
    副作用调用顺序与落库时机与合并前逐行一致。
    """
    is_resume = run.mode == "resume"
    proc = run.proc
    try:
        async for ev in pump_stream(
            agent=run.agent,
            stream_input=stream_input,
            stream_config=run.stream_config,
            proc=proc,
            persister=run.persister,
            db=run.db,
            session_id=run.session_id,
            sid=run.sid,
            message_id=run.message_id,
            session_logger=run.session_logger,
            context=getattr(run.agent, "session_context", None),
        ):
            yield run.emitter.frame(ev)

        # 确保 thinking 结束（流结束时若仍在思考）
        for _ev in proc._end_thinking():
            yield run.emitter.frame(_ev)

        if proc.accumulated_response:
            yield run.emitter.content_end()

        if is_resume:
            logger.info(
                f"[{run.sid}] 📢 流式输出结束 | steps={proc.current_step} | "
                f"blocks={len(proc.blocks)}"
            )
        else:
            logger.info(f"[{run.sid}] 📢 流式输出结束")

        # ── HITL: chat 检测新中断 / resume 检测嵌套中断 ────────────────
        try:
            interruption = await detect_hitl_interruption(
                agent=run.agent,
                stream_config=run.stream_config,
                proc=proc,
                elapsed_time=time.time() - run.start_time,
                thread_id=run.handle,
                with_approval=is_resume,
            )
            if interruption:
                # 保存部分 assistant 消息（复用取消路径模式）
                await run_blocking(
                    run.db.update_last_assistant_message,
                    run.session_id,
                    interruption.partial_message,
                )
                if is_resume:
                    # 键序契约（与迁移前字面量一致）：type → thread_id → blocks →
                    # action_requests。快捷方法把 action_requests 放在 blocks 之前，
                    # 故此处必须用 emit() 显式排序，不得改用 approval_required()。
                    yield run.emitter.emit(
                        SSEEvent.APPROVAL_REQUIRED,
                        thread_id=run.handle,
                        # 嵌套中断时也下发当前 blocks，前端据此刷新已完成的工具结果
                        blocks=proc._sse_blocks(),
                        action_requests=interruption.action_requests_payload,
                    )
                else:
                    logger.info(
                        f"[{run.sid}] 🔔 HITL 中断 | thread_id={run.thread_id} | "
                        f"审批句柄={run.handle} | "
                        f"{len(interruption.action_requests_payload)} 个操作待审批"
                    )
                    yield run.emitter.approval_required(
                        thread_id=run.handle,
                        action_requests=interruption.action_requests_payload,
                    )
                return
        except Exception as e:
            if is_resume:
                logger.warning(
                    f"[{run.sid}] HITL 恢复: 嵌套中断检测异常: {e}", exc_info=True
                )
            else:
                logger.warning(f"[{run.sid}] HITL 中断检测异常: {e}", exc_info=True)
        finally:
            if is_resume:
                # 无论正常结束、嵌套中断还是异常，均把审批决策写回，保证切换会话后仍可见
                await run_blocking(
                    persist_approval_decisions,
                    run.db,
                    run.session_id,
                    run.decision_status_by_id,
                    run.sid,
                )

        # 流末工具状态：chat 直接取当前 blocks；resume 需按 tool_call_id 去重并合并
        # 审批状态（astream 重放会再次 append 同 id 记录、内存 blocks 可能丢失审批标记）。
        tool_call_records = _tool_call_tuples(proc.blocks, with_approval=is_resume)
        if is_resume:
            tool_call_records = merge_tool_call_records(tool_call_records)
            # blocks 同样需要 finalize：去重 + 重新应用审批决策；同步回 proc，
            # 保证 done 事件的 _sse_blocks() 用的是同一份。
            proc.blocks = finalize_blocks(proc.blocks, run.decision_status_by_id)

        async for frame in finalize_stream(
            agent=run.agent,
            db=run.db,
            session_id=run.session_id,
            sid=run.sid,
            message_id=run.message_id,
            proc=proc,
            start_time=run.start_time,
            context_length=run.context_length,
            auto_compress_tokens=run.auto_compress_tokens,
            tool_call_records=tool_call_records,
            raw_user_msg=run.raw_user_msg,
            message_content=run.message_content,
            session_logger=run.session_logger,
            mode=run.mode,
            emitter=run.emitter,
        ):
            yield frame
    except asyncio.CancelledError:
        async for frame in _stream_cancelled_frames(run):
            yield frame
    except Exception as e:
        async for frame in _stream_error_frames(run, e):
            yield frame


async def chat_stream_generator(
    request: ChatRequest,
    db: Database,
    agent: EasyAgent,
    session_id: str,
    message_id: str,
    username: str,
    http_request=None,
    parsed_content: str | None = None,
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
        history_messages = (
            session.messages[:-1] if session and session.messages else []
        )
        provider = agent.config.llm.provider.lower() if agent and agent.config else ""
        # DA-03：thread 里的本轮消息只存用户原话（与 DB 一致）；工作区前缀、知识库 /
        # 页面上下文、上传预览等本轮注入（message_content）走 turn_context，只进本轮
        # 模型请求，见 agent/turn_context.py。开关 agent.session_thread 打开时一个会话
        # 一个 thread，只输入本轮消息（及需要补齐的内容），见 services/sessions/thread.py。
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
            await run_blocking(
                record_turn_checkpoint, db, session_id, message_id, plan.checkpoint_id, sid
            )

        # ── 启动流式输出 (stream_mode=['messages', 'updates']) ─────────
        logger.info(f"[{sid}] 🚀 使用 stream_mode=['messages', 'updates'] 流式接口")

        # 审批句柄始终是 {session_id}-{message_id}（前端据此还原 message_id）；
        # 每消息模式下它就是 thread_id，会话 thread 模式下 thread_id = session_id。
        thread_id = plan.thread_id
        handle = approval_handle(session_id, message_id)
        stream_config = build_run_config(agent, thread_id)

        run = StreamRun(
            agent=agent,
            db=db,
            session_id=session_id,
            sid=sid,
            message_id=message_id,
            emitter=emitter,
            proc=proc,
            persister=persister,
            stream_config=stream_config,
            start_time=start_time,
            context_length=context_length,
            auto_compress_tokens=auto_compress_tokens,
            thread_id=thread_id,
            handle=handle,
            raw_user_msg=parsed_content or request.message,
            message_content=message_content,
            session_logger=session_logger,
            result_log_truncate=result_log_truncate,
        )
        # 主循环、收尾与兜底统一在 _run_stream（与 resume 共用）；这里只构造本轮输入。
        async for frame in _run_stream(
            run,
            turn_input(
                context_messages,
                message_id=message_id,
                rendered=message_content,
                pinned_skills=pinned_skills,
            ),
        ):
            yield frame
    except asyncio.CancelledError:
        # 取消发生在准备阶段（主循环内的取消由 _run_stream 兜底）：还没有产出任何
        # token，不写空 assistant 消息，取消继续向上传播（/cancel 语义不变）。
        logger.info(f"[{sid}] ❌ 准备阶段被取消（未产生回复）")
        raise
    except Exception as e:
        # 准备阶段异常（查库 / 上下文构建）：还没产出任何 token，按统一文案补终态帧。
        logger.error(f"[{sid}] ❌ 聊天准备异常: {str(e)}", exc_info=True)
        yield emitter.error(f"处理失败: {friendly_error_message(e)}")


async def resume_stream_generator(
    db: Database,
    agent: EasyAgent,
    session_id: str,
    thread_id: str,
    decisions: list[dict],
    username: str = "",
    session_logger: SessionLogger | None = None,
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
    # 计时接着中断前已用的耗时往下算（等待审批的时间不计）：续跑下发和落库的
    # elapsed_time 与 step_count 一样是这条消息的累计值，落库不再丢掉中断前那段。
    start_time -= snapshot.elapsed_time
    last_context_tokens = 0
    pending_tool_call_ids = snapshot.pending_tool_call_ids
    original_user_msg = ""  # 触发 HITL 的原始用户消息，供记忆生成使用

    # 捕获触发 HITL 的原始用户消息（会话中最后一条 user 消息），供记忆生成使用
    try:
        if session and session.messages:
            original_user_msg = last_user_message(session.messages)
    except Exception as exc:  # noqa: BLE001 - 只用于记忆生成，取不到不影响恢复流
        logger.debug(f"[{sid}] HITL 恢复: 读取原始用户消息失败: {exc}")

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
    except StaleApprovalError as e:
        logger.warning(f"[{sid}] HITL 恢复: 审批已不在本实例: {e}")
        yield emitter.error(str(e))
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

    run = StreamRun(
        agent=agent,
        db=db,
        session_id=session_id,
        sid=sid,
        message_id=message_id,
        emitter=emitter,
        proc=proc,
        persister=persister,
        stream_config=stream_config,
        start_time=start_time,
        context_length=context_length,
        auto_compress_tokens=auto_compress_tokens,
        thread_id=thread_id,
        handle=handle,
        raw_user_msg=original_user_msg,
        mode="resume",
        session_logger=session_logger,
        decision_status_by_id=decision_status_by_id,
    )
    # 主循环、收尾与兜底统一在 _run_stream（与 chat 共用）；这里只构造恢复输入。
    async for frame in _run_stream(run, resume_command):
        yield frame
