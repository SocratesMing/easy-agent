"""手动压缩编排；checkpoint 中的记录与摘要在同一次写入提交。"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import HTTPException
from langchain_core.messages import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

from easy_agent.agent.manual_compaction import estimate_context, prepare_compaction, terminal_update_node
from easy_agent.agent.run_config import build_run_config
from easy_agent.db.providers import get_session_runtime_repository, get_agent_run_repository
from easy_agent.persistence.checkpoints import get_session_checkpointer, sqlite_checkpointer_active
from easy_agent.services.agents.manager import get_or_create_agent_for_session, is_session_streaming
from easy_agent.services.sessions.thread import plan_turn, session_thread_active
from easy_agent.utils.deployment import detect_worker_count, instance_owner
from easy_agent.utils.blocking import run_blocking

logger = logging.getLogger(__name__)
TIMEOUT_SECONDS = 120
_operations: dict[tuple[str, str], asyncio.Task] = {}


async def independent_checkpoint(runtime, saver) -> bool:
    """提交围栏持有业务库写锁，checkpoint 必须使用独立的数据库文件。"""
    db = runtime._db
    if db.db_type != "sqlite":
        return True
    async with saver.conn.execute("PRAGMA database_list") as cursor:
        databases = await cursor.fetchall()
    def different_files():
        business = Path(db.db_path).resolve()
        for _, _, filename in databases:
            if filename:
                checkpoint = Path(filename).resolve()
                if checkpoint == business or (business.exists() and checkpoint.exists() and checkpoint.samefile(business)):
                    return False
        return True
    return await run_blocking(different_files)


def supported(config) -> bool:
    return bool(
        getattr(getattr(config, "agent", None), "session_thread", False)
        and sqlite_checkpointer_active() and detect_worker_count() == 1
    )


async def records(session_id: str) -> list[dict]:
    if not sqlite_checkpointer_active() or detect_worker_count() != 1:
        return []
    checkpoint = await get_session_checkpointer(session_id).aget_tuple(
        {"configurable": {"thread_id": session_id}}
    )
    values = checkpoint.checkpoint.get("channel_values", {}) if checkpoint else {}
    return list((values.get("context_compactions") or {}).values())


async def compact(repo, session_id: str, username: str, model: str | None, request_id: str) -> dict:
    # 已提交请求先读权威结果；客户端超时后以同一个 ID 重试不会再次摘要。
    session = await run_blocking(repo.get_owned_session, session_id, username)
    for result in await records(session_id):
        if result["id"] == request_id:
            return response(session_id, result)
    key = (session_id, request_id)
    task = _operations.get(key)
    if task is None:
        task = asyncio.create_task(_execute(repo, session, username, model, request_id))
        _operations[key] = task
        def finished(done):
            _operations.pop(key, None)
            if not done.cancelled():
                done.exception()  # 请求断开时也消费异常，重试仍查询 checkpoint。
        task.add_done_callback(finished)
    return await asyncio.shield(task)


def response(session_id: str, record: dict) -> dict:
    return {"session_id": session_id, "status": record["status"],
            "message": record["message"], "reason": record.get("reason"), "compaction": record}


def _pending(rows: list[dict]) -> bool:
    return any(row.get("pending_approval") or any(
        item.get("approval_status") == "pending"
        for item in [*(row.get("tool_calls") or []), *(row.get("blocks") or [])]
    ) for row in rows)


def _unfinished_work(state) -> bool:
    """已停止的模型续跑不是活跃任务；审批、工具及未知挂起状态仍拒绝。

    仅在调用方已取得独占租约并确认没有 live run 后使用。LangGraph 会将
    取消/失败的 model 保留在 next/tasks（取消时 error 也可能为空）；压缩提交到
    终止节点时一起结束该任务，不恢复模型运行。
    """
    if not state.next and not state.tasks:
        return False
    stopped_model = state.tasks and all(
        task.name == "model" and not task.interrupts and not task.state
        for task in state.tasks
    )
    return not (stopped_model and set(state.next) == {"model"})


async def _execute(repo, session, username, model, request_id):
    from easy_agent.services.agents.manager import get_agent_config

    config = (get_agent_config() or {}).get("config")
    if not supported(config):
        raise HTTPException(409, "当前运行模式不支持手动压缩（需要单实例、单 worker 的持久会话 checkpoint）")
    if model and model not in config.models:
        raise HTTPException(422, "所选模型不可用")
    sid = session.session_id
    runtime = get_session_runtime_repository()
    owner = f"{instance_owner()}:compact:{uuid.uuid4()}"
    now = datetime.now(UTC)
    until = (now + timedelta(seconds=30)).isoformat(timespec="seconds")
    acquired, _ = await run_blocking(runtime.try_acquire_session_stream, sid, owner, until, now.isoformat(timespec="seconds"))
    if not acquired:
        raise HTTPException(409, "会话正在生成或压缩，请稍后重试")
    heartbeat = None
    try:
        if is_session_streaming(sid):
            raise HTTPException(409, "会话仍在生成，请稍后重试")
        async def heartbeat_loop():
            while True:
                await asyncio.sleep(8)
                if not await run_blocking(runtime.renew_stream_lease, sid, owner, ttl_seconds=30):
                    raise RuntimeError("压缩租约续约失败")

        heartbeat = asyncio.create_task(heartbeat_loop())
        async def prepare():
            if await run_blocking(get_agent_run_repository().has_other_live_run, sid, "", datetime.now(UTC).isoformat(timespec="seconds")):
                raise HTTPException(409, "会话仍有运行任务，请稍后重试")
            agent = await get_or_create_agent_for_session(
                sid, username=username, workspace_name=session.workspace_name, model_name=model,
            )
            if not session_thread_active(agent) or agent.agent.checkpointer is not get_session_checkpointer(sid):
                raise HTTPException(409, "当前实际 checkpointer 不支持手动压缩")
            return await _prepare_and_commit(repo, runtime, agent, session, owner, request_id)

        work = asyncio.create_task(prepare())
        try:
            done, _ = await asyncio.wait([work, heartbeat], timeout=TIMEOUT_SECONDS, return_when=asyncio.FIRST_COMPLETED)
            if work in done:
                return await work
            work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            if heartbeat in done:
                await heartbeat
            raise HTTPException(504, "压缩超时；请使用原请求标识查询结果")
        finally:
            if not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("手动压缩失败 session=%s request=%s", sid, request_id)
        raise HTTPException(502, "压缩失败，请查询原请求结果后重试") from exc
    finally:
        if heartbeat:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
        await run_blocking(runtime.release_session_stream, sid, owner, datetime.now(UTC).isoformat(timespec="seconds"))


async def _prepare_and_commit(repo, runtime, agent, session, owner, request_id):
    sid = session.session_id
    session = await run_blocking(repo.get_owned_session, sid, session.username)
    if _pending(session.messages or []):
        raise HTTPException(409, "会话正在等待审批，请先处理审批")
    graph = agent.agent
    if not await independent_checkpoint(runtime, graph.checkpointer):
        raise HTTPException(409, "手动压缩需要业务库与 checkpoint 使用独立的数据库文件")
    config = build_run_config(agent, sid)
    state = await graph.aget_state(config)
    if _unfinished_work(state):
        raise HTTPException(409, "会话有待执行任务或审批，无法压缩")
    node = terminal_update_node(graph)
    values = state.values or {}
    existing = dict(values.get("context_compactions") or {})
    if request_id in existing:
        return response(sid, existing[request_id])
    history = session.messages or []
    if not any(row.get("role") == "user" for row in history):
        raise HTTPException(409, "请先开始对话")
    plan = await plan_turn(agent, session_id=sid, turn_id="compact-placeholder", history=history, raw="", dry_run=True)
    additions = plan.messages[:-1]  # 不写入伪造的本轮用户消息。
    reset = plan.mode in ("hydrate", "rehydrate")
    messages = additions if reset else [*(values.get("messages") or []), *additions]
    prepared = await prepare_compaction(agent, messages, {} if reset else values) if messages else None
    # DB 消息中的位置锚定到稳定 turn_id（旧消息兼容由同一 helper 提供）。
    from easy_agent.services.streaming.messages import stable_history
    normalized = stable_history(history)
    anchor = next((row["turn_id"] for row in reversed(normalized) if row.get("role") == "user"), None)
    record = {
        "id": request_id, "anchor_turn_id": anchor, "anchor_message_count": len(history),
        "created_at": datetime.now(UTC).isoformat(), "model": agent.model_name,
        "status": "compacted" if prepared else "skipped",
        "message": "上下文已压缩，原始聊天记录已保留" if prepared else "近期内容需保留，暂无可压缩的较早上下文",
        **({k: v for k, v in prepared.items() if k not in ("event", "archive_id")} if prepared else {
            "reason": "insufficient_history", **estimate_context(agent, messages, {} if reset else values),
        }),
    }
    existing[request_id] = record
    update = {"context_compactions": existing}
    if prepared:
        update.update({
            "messages": ([RemoveMessage(id=REMOVE_ALL_MESSAGES)] if plan.mode == "rehydrate" else []) + additions,
            "_summarization_event": prepared["event"],
            "_summarization_session_id": prepared["archive_id"], "turn_context": None,
        })

    loop = asyncio.get_running_loop()
    async def save():
        await graph.aupdate_state(config, update, as_node=node)
        saved = await graph.aget_state(config)
        if saved.next or saved.tasks or (saved.values.get("context_compactions") or {}).get(request_id) != record:
            raise RuntimeError("checkpoint 提交结果未知，请按原请求 ID 查询")
        if prepared and saved.values.get("_summarization_event") != prepared["event"]:
            raise RuntimeError("摘要事件保存校验失败")

    # 行锁保护整个 checkpoint 写入/回读；shield 后取消也必须等实际写入完成再释放租约。
    def commit():
        return asyncio.run_coroutine_threadsafe(save(), loop).result()
    commit_task = asyncio.create_task(run_blocking(runtime.guarded_commit, sid, owner, commit))
    try:
        await asyncio.shield(commit_task)
    except asyncio.CancelledError:
        await commit_task
        raise
    return response(sid, record)


async def context_status(repo, session_id: str, username: str) -> dict:
    from easy_agent.services.agents.manager import get_agent_config
    from easy_agent.services.sessions.runtime import is_stream_active

    session = await run_blocking(repo.get_owned_session, session_id, username)
    config = (get_agent_config() or {}).get("config")
    available = supported(config)
    runtime = get_session_runtime_repository()
    available = available and await independent_checkpoint(runtime, get_session_checkpointer(session_id))
    busy = any(sid == session_id for sid, _ in _operations) or await run_blocking(is_stream_active, runtime, session_id)
    pending = _pending(session.messages or [])
    reason = ("当前模式不支持手动压缩" if not available else
              "会话正在生成或压缩" if busy else
              "请先处理待审批操作" if pending else
              "请先开始对话" if not session.messages else "")
    return {"session_id": session_id, "supported": available, "busy": busy,
            "reason": reason, "compactions": await records(session_id)}
