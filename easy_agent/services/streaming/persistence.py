"""流式持久化适配器（C2 拆分第二步）：增量落库 / 最终消息 / 审批决策。

半纯模块：只依赖 ``Database`` 协议（duck typing）与 ``stream_messages`` 的
消息构造函数，不 import Agent / SSE / 图状态。异常策略与原 ``streaming.py``
保持一致：增量写入失败只记日志，不能中断流式输出。
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from easy_agent.bootstrap.env import env_bool
from easy_agent.services.stream_messages import _partial_message_dict, tool_call_records_to_dicts

logger = logging.getLogger("easy_agent.chat_service")


def persist_incremental(
    proc, *, db, session_id: str, sid: str, start_time: float
) -> None:
    """流式过程中增量持久化当前 assistant 消息（正文 / 思考 / 工具记录）。

    chat 与 resume 共用；内部吞掉异常——持久化失败不应中断流式输出。
    """
    try:
        msg = _partial_message_dict(proc, time.time() - start_time)
        db.update_last_assistant_message(session_id, msg)
    except Exception as e:
        logger.warning(f"[{sid}] 增量持久化失败: {e}")


# 兼容旧符号：``streaming._persist_incremental`` 仍然可用。
_persist_incremental = persist_incremental


class IncrementalPersister:
    """增量落库节流器：字符阈值 + 时间窗口双阈值。

    仅按字符阈值会在长回复时每 ~500 字做一次全量序列化 + UPDATE（实测 50KB
    回复阻塞事件循环 0.75s）。双阈值兼顾崩溃丢失窗口与写放大。
    """

    def __init__(
        self,
        *,
        db,
        session_id: str,
        sid: str,
        start_time: float,
        min_interval: float = 2.0,
        max_chars: int = 20000,
    ) -> None:
        self.db = db
        self.session_id = session_id
        self.sid = sid
        self.start_time = start_time
        self.min_interval = min_interval
        self.max_chars = max_chars
        self.last_persisted_len = 0
        self.last_persist_ts = time.time()

    def due(self, proc) -> bool:
        """是否到了增量落库的时候（双阈值，无副作用）。

        编排器先在事件循环上用它判断，确实要写时才把 :meth:`maybe_persist` 放进线程，
        避免每个正文 chunk 都切一次线程。
        """
        growth = len(proc.accumulated_response) - self.last_persisted_len
        if growth < 500:
            return False
        return growth >= self.max_chars or (
            time.time() - self.last_persist_ts >= self.min_interval
        )

    def maybe_persist(self, proc) -> bool:
        """按双阈值判断是否需要增量落库；写入返回 True。"""
        if not self.due(proc):
            return False
        self.last_persisted_len = len(proc.accumulated_response)
        self.last_persist_ts = time.time()
        persist_incremental(
            proc,
            db=self.db,
            session_id=self.session_id,
            sid=self.sid,
            start_time=self.start_time,
        )
        return True


def persist_partial(
    proc,
    *,
    db,
    session_id: str,
    sid: str,
    start_time: float,
    with_approval: bool = False,
    raise_on_error: bool = False,
) -> None:
    """保存「进行中」的 assistant 消息（取消 / 异常 / HITL 中断共用）。

    ``raise_on_error=True`` 用于恢复流取消路径：原实现不吞异常，保持行为一致。
    """
    try:
        partial_msg = _partial_message_dict(
            proc, time.time() - start_time, with_approval=with_approval
        )
        db.update_last_assistant_message(session_id, partial_msg)
    except Exception as e:
        if raise_on_error:
            raise
        logger.warning(f"[{sid}] 保存部分回复失败: {e}")


def persist_tool_result_event(
    ev, *, db, session_id, message_id, session_logger, agent, sid
) -> None:
    """工具结果写会话日志，并登记生成文件。

    思考和工具参数已经在 assistant 消息的 extra_data 里，不再另写记录表。
    chat 与 resume 共用。
    """
    tool_name = ev.get("tool_name", "")
    tool_call_id = ev.get("tool_call_id", "")
    tool_args = ev.get("arguments", {})
    result_content = ev.get("result", "")
    success = ev.get("success", True)
    tool_duration = ev.get("duration", 0)
    step = ev.get("step", 0)
    if session_logger:
        try:
            session_logger.log_tool_call(
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                arguments=tool_args,
                result=str(result_content)[:5000],
                success=success,
                duration=tool_duration,
                step=step,
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


def persist_todos(db, session_id: str, todos: list, sid: str) -> None:
    """持久化 Todo list（失败只记日志）。"""
    try:
        db.update_session_todos(session_id, todos)
    except Exception as e:
        logger.warning(f"[{sid}] 持久化 Todo list 失败: {e}")


def persist_final_message(db, session_id: str, message: dict) -> None:
    """写入最终 assistant 消息（异常向上抛，由生成器统一转 error 事件）。"""
    db.update_last_assistant_message(session_id, message)


def persist_approval_decisions(
    db, session_id: str, status_by_id: dict[str, str], sid: str
) -> None:
    """将用户对 HITL 工具调用的审批决策（批准/拒绝）持久化到最近一条 assistant
    消息，使切换会话/刷新页面后仍能显示审批标记。"""
    if not status_by_id:
        return
    try:
        sess = db.get_session(session_id)
        if not sess or not sess.messages:
            return
        last = sess.messages[-1]
        if last.get("role") != "assistant":
            return
        for tc in last.get("tool_calls") or []:
            if tc.get("tool_call_id") in status_by_id:
                tc["approval_status"] = status_by_id[tc["tool_call_id"]]
        # blocks 同步写回：前端历史渲染优先读 blocks
        for b in last.get("blocks") or []:
            if (
                b.get("type") == "tool_call"
                and b.get("tool_call_id") in status_by_id
            ):
                b["approval_status"] = status_by_id[b["tool_call_id"]]
                b.pop("pending_approval", None)
        db.update_last_assistant_message(session_id, last)
    except Exception as e:
        logger.warning(f"[{sid}] 持久化审批决策失败: {e}")


def write_session_context_snapshot(
    *,
    db,
    session_id: str,
    message_id: str,
    message_content: str,
    accumulated_response: str,
    accumulated_thinking: str,
    thinking_time: float,
    tool_call_records: list,
    sid: str,
) -> None:
    """调试用 context 快照：默认关闭，``EASY_WRITE_SESSION_CONTEXT=1`` 开启。"""
    if not env_bool("EASY_WRITE_SESSION_CONTEXT"):
        return
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
            json.dump(context_data, f, ensure_ascii=False)
        logger.info(f"[{sid}] Context file saved: {context_filename}")
    except Exception as e:
        logger.warning(f"[{sid}] Failed to save context file: {e}")


__all__ = [
    "IncrementalPersister",
    "persist_approval_decisions",
    "persist_final_message",
    "persist_incremental",
    "persist_partial",
    "persist_todos",
    "persist_tool_result_event",
    "write_session_context_snapshot",
]
