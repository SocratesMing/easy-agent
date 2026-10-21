"""会话级 thread（DA-03 P1）：一个会话一个 LangGraph thread。

开关 ``agent.session_thread``（默认关）。关闭时与改造前一致：每条消息一个
``{session_id}-{message_id}`` thread，每轮从 DB 重建完整历史。开启后：

- ``thread_id = session_id``，历史、摘要状态、todos 由 checkpointer 保存，每轮只输入本轮消息；
  DB 的 ``session_messages`` 仍是 UI 的权威投影，写入逻辑不变。
- thread 为空（新会话、老会话第一次走会话 thread、checkpoint 丢失）时，用
  ``build_context_messages`` 从 DB 重建历史一次性写入（水合）。
- DB 里排在 thread 最后一轮之后的轮次（开关关闭期间的对话、定时任务写入、专家预检拒答），
  重建后补进本轮输入；thread 的最后一轮在 DB 里找不到时，删掉 thread 重新水合。
- 上一轮被取消或出错时，用户已看到、thread 里却没有的半截回复补成一条 AIMessage
  （改造前从 DB 重建历史时也会回灌这段正文）。
- 给前端的审批句柄仍是 ``{session_id}-{turn_id}``：前端靠这个格式还原 message_id，
  服务端再映射到真正的 thread（:func:`resolve_resume_thread`）。

见 docs/archive/design/2026-10-19-da03-session-thread.md §6。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_core.messages import AIMessage

from easy_agent.agent.run_config import build_run_config
from easy_agent.persistence.checkpoints import PersistentMemorySaver
from easy_agent.services.hitl_state import pending_hitl_interrupts
from easy_agent.services.stream_messages import build_context_messages

logger = logging.getLogger(__name__)

#: 补回的半截回复末尾的标记，让模型知道这段回复没有说完。
INTERRUPTED_SUFFIX = "\n\n（回复被中断）"


class StaleApprovalError(Exception):
    """审批句柄对不上任何挂起的中断（会话已有新消息，或该审批已处理）。"""


def session_thread_enabled(config) -> bool:
    """``config`` 可以是 ``Config``，也可以是 ``get_agent_config()`` 返回的全局配置字典。"""
    if isinstance(config, dict):
        config = config.get("config")
    return bool(getattr(getattr(config, "agent", None), "session_thread", False))


def session_thread_in_use(config) -> bool:
    """接口层（还没拿到 agent 时）的判断：开关打开，且在用官方 SQLite saver。

    与 :func:`session_thread_active` 口径一致：legacy 快照 saver 下仍是每条消息一个 thread。
    """
    from easy_agent.persistence.checkpoints import sqlite_checkpointer_active

    return session_thread_enabled(config) and sqlite_checkpointer_active()


_legacy_warned = False


def session_thread_active(agent) -> bool:
    """本轮是否走会话 thread：开关打开，且 checkpointer 不是 legacy 快照 saver。

    legacy ``PersistentMemorySaver``（业务库为 MySQL 且没配 SQLite checkpoint 时的回退）
    每次写入都把整个快照 pickle 进业务库，会话 thread 越长越慢，因此仍按每条消息一个 thread。
    要用会话 thread，请配置 ``EASY_CHECKPOINT_SQLITE_PATH``。
    """
    global _legacy_warned
    if not session_thread_enabled(getattr(agent, "config", None)):
        return False
    checkpointer = getattr(getattr(agent, "agent", None), "checkpointer", None)
    if isinstance(checkpointer, PersistentMemorySaver):
        if not _legacy_warned:
            _legacy_warned = True
            logger.warning(
                "agent.session_thread 已打开，但当前是 legacy checkpoint 快照（MySQL 回退），"
                "仍按每条消息一个 thread；配置 EASY_CHECKPOINT_SQLITE_PATH 后生效"
            )
        return False
    return True


def approval_handle(session_id: str, turn_id: str) -> str:
    """给前端的审批句柄（同时也是每消息模式下的 thread_id）。"""
    return f"{session_id}-{turn_id}"


def handle_turn_id(session_id: str, handle: str | None) -> str | None:
    """从审批句柄里取出 turn_id；句柄不属于该会话时返回 ``None``。"""
    prefix = f"{session_id}-"
    if session_id and handle and handle.startswith(prefix) and len(handle) > len(prefix):
        return handle[len(prefix):]
    return None


@dataclass
class TurnPlan:
    """本轮跑在哪个 thread 上、输入哪些消息。"""

    thread_id: str
    messages: list
    #: per-message（开关关闭）/ hydrate（thread 为空）/ rehydrate（thread 与 DB 对不上）/ continue
    mode: str
    #: 本轮开始前 thread 的最新 checkpoint（只有续用会话 thread 时才有），留给将来的编辑 / 重新生成
    checkpoint_id: str | None = None
    #: 补进来的 DB 轮次条数
    caught_up_rows: int = 0
    #: 是否补了上一轮的半截回复
    restored_tail: bool = False


def _row_turn_ids(history: list[dict]) -> dict[str, int]:
    return {
        str(row["turn_id"]): index
        for index, row in enumerate(history)
        if row.get("role") == "user" and row.get("turn_id")
    }


def _last_known_turn(thread_messages: list, turn_rows: dict[str, int]) -> tuple[int, int] | None:
    """thread 里最后一条能在 DB 找到的本轮用户消息：返回（thread 下标, DB 下标）。"""
    for index in range(len(thread_messages) - 1, -1, -1):
        message = thread_messages[index]
        if getattr(message, "type", None) == "human" and getattr(message, "id", None) in turn_rows:
            return index, turn_rows[message.id]
    return None


def _next_user_index(history: list[dict], start: int) -> int:
    for index in range(start, len(history)):
        if history[index].get("role") == "user":
            return index
    return len(history)


def _unsynced_tail(thread_turn: list, db_rows: list[dict]) -> str:
    """DB 里这一轮的正文比 thread 里多出来的部分（取消 / 出错时没写进 thread 的半截回复）。

    只在 DB 正文以 thread 正文开头、且更长时才算；对不上（例如正文里带 ``<think>``）就不补，
    宁可少补也不重复。
    """
    assistant = next((row for row in reversed(db_rows) if row.get("role") == "assistant"), None)
    if assistant is None:
        return ""
    db_text = str(assistant.get("content") or "")
    thread_text = "".join(m.text for m in thread_turn if isinstance(m, AIMessage))
    if len(db_text) <= len(thread_text) or not db_text.startswith(thread_text):
        return ""
    tail = db_text[len(thread_text):]
    return tail if tail.strip() else ""


async def delete_session_thread(agent, session_id: str) -> None:
    checkpointer = getattr(getattr(agent, "agent", None), "checkpointer", None)
    if checkpointer is not None:
        await checkpointer.adelete_thread(session_id)


async def plan_turn(
    agent,
    *,
    session_id: str,
    turn_id: str,
    history: list[dict],
    raw: str,
    provider: str = "",
) -> TurnPlan:
    """决定本轮的 thread 与输入消息（最后一条都是 ``HumanMessage(raw, id=turn_id)``）。"""

    def rebuilt(rows: list[dict]) -> list:
        return build_context_messages(rows, raw, provider=provider, current_message_id=turn_id)

    if not session_thread_active(agent):
        return TurnPlan(approval_handle(session_id, turn_id), rebuilt(history), "per-message")

    state = await agent.agent.aget_state(build_run_config(agent, session_id))
    thread_messages = list((state.values or {}).get("messages") or [])
    if not thread_messages:
        return TurnPlan(session_id, rebuilt(history), "hydrate")

    known = _last_known_turn(thread_messages, _row_turn_ids(history))
    if known is None:
        logger.warning(
            f"[{session_id[-5:]}] 会话 thread 的最后一轮在 DB 里找不到，删除 thread 后从 DB 重新水合"
        )
        await delete_session_thread(agent, session_id)
        return TurnPlan(session_id, rebuilt(history), "rehydrate")

    thread_index, row_index = known
    next_user = _next_user_index(history, row_index + 1)
    tail = _unsynced_tail(thread_messages[thread_index + 1:], history[row_index + 1:next_user])
    missing = history[next_user:]
    messages = [
        *([AIMessage(content=tail + INTERRUPTED_SUFFIX)] if tail else []),
        *rebuilt(missing),
    ]
    checkpoint_id = ((state.config or {}).get("configurable") or {}).get("checkpoint_id")
    return TurnPlan(
        session_id,
        messages,
        "continue",
        checkpoint_id=checkpoint_id,
        caught_up_rows=len(missing),
        restored_tail=bool(tail),
    )


def record_turn_checkpoint(db, session_id: str, turn_id: str, checkpoint_id: str, sid: str = "") -> None:
    """把本轮开始前会话 thread 的 checkpoint id 记到本轮用户消息上（留给将来的编辑 / 重新生成）。"""
    from easy_agent.db.repositories import sessions as sessions_repo

    try:
        sessions_repo.annotate_user_turn(
            db, session_id, turn_id, {"turn_checkpoint_id": checkpoint_id}
        )
    except Exception as e:  # noqa: BLE001 - 只是锚点，失败不影响对话
        logger.warning(f"[{sid}] 记录本轮起点 checkpoint 失败: {e}")


async def resolve_resume_thread(agent, *, session_id: str, handle: str, last_message: dict | None) -> str:
    """把前端的审批句柄解析成要恢复的 thread_id。

    - 不走会话 thread（开关关闭或 legacy saver）：句柄就是 thread_id（与改造前一致）；
    - 会话 thread 当前这一轮正挂着中断：恢复会话 thread；
    - 兼容期：升级前挂起的审批还在旧的每消息 thread 上，且 DB 最后一条仍是这条待审批消息；
    - 其余情况（会话已有新消息、审批已处理）抛 :class:`StaleApprovalError`。

    调用方已校验句柄属于该会话（:func:`handle_turn_id`）。
    """
    if not session_thread_active(agent):
        return handle
    turn_id = handle_turn_id(session_id, handle)
    graph = agent.agent
    state = await graph.aget_state(build_run_config(agent, session_id))
    current = ((state.values or {}).get("turn_context") or {}).get("message_id")
    if current == turn_id and pending_hitl_interrupts(state):
        return session_id

    pending = (last_message or {}).get("pending_approval") or {}
    if (last_message or {}).get("role") == "assistant" and pending.get("thread_id") == handle:
        legacy = await graph.aget_state(build_run_config(agent, handle))
        if pending_hitl_interrupts(legacy):
            return handle
    raise StaleApprovalError("审批已失效：会话已有新消息，或该审批已处理")


def abandon_pending_approval(repo, session_id: str) -> bool:
    """新消息到达时，把上一轮遗留的待审批标成已拒绝（会话 thread 下新输入会丢弃挂起的中断）。

    ``repo`` 需提供 ``get_session`` 与 ``update_last_assistant_message``（Chat / Expert 仓库、
    ``Database`` 均可）。只改最后一条 assistant 消息；没有待审批时什么也不做。
    """
    session = repo.get_session(session_id)
    messages = getattr(session, "messages", None) or []
    last = messages[-1] if messages else None
    if not last or last.get("role") != "assistant":
        return False
    changed = last.pop("pending_approval", None) is not None
    for record in last.get("tool_calls") or []:
        if record.get("approval_status") == "pending":
            record["approval_status"] = "rejected"
            changed = True
    for block in last.get("blocks") or []:
        if block.get("type") == "tool_call" and block.get("approval_status") == "pending":
            block["approval_status"] = "rejected"
            block.pop("pending_approval", None)
            changed = True
    if changed:
        repo.update_last_assistant_message(session_id, last)
        logger.info(f"[{session_id[-5:]}] 新消息到达，上一轮未处理的审批已标为拒绝")
    return changed
