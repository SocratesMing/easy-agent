"""Chat routes - streaming and non-streaming"""

import asyncio
import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from easy_agent.api.sse_response import sse_response
from easy_agent.db.repositories.chat_repository import ChatRepository
from easy_agent.db.repositories.stream_events import clear_stream_events
from easy_agent.models.api import ChatRequest, ResumeRequest
from easy_agent.middleware import get_current_username
from easy_agent.services.agents.chat_turn import (
    RefusalPlan,
    prepare_resume,
    prepare_turn,
    require_turn_content,
)
from easy_agent.services.agents.manager import (
    aregister_stream_task,
    aunregister_stream_task,
    cancel_stream_task,
    get_agent_config as _get_agent_config,
    is_session_streaming,
    remove_session_agent,
)
from easy_agent.services.streaming.emitter import SSEEmitter
from easy_agent.services.streaming.outcome import StreamOutcome
from easy_agent.services.streaming.sse import (
    SSE_HEARTBEAT_FRAME,
    heartbeat_interval_seconds,
)
from easy_agent.services.sessions.runtime import SessionBusyError
from easy_agent.services.usage import with_session_token_totals
from easy_agent.services.streaming.runs import finish_run, renew_run
from easy_agent.services.streaming.hub import (  # noqa: F401 - 兼容旧导入路径
    _HUB_HISTORY_MAX_CHARS,
    _STREAM_HUB_TTL_SECONDS,
    _SUBSCRIBER_QUEUE_SLACK,
    _StreamHub,
    _session_stream_hubs,
    _stream_hub_for,
    get_stream_hub,
    pop_stream_hub,
    session_stream_hubs,
    stream_hub_for,
)
from easy_agent.api.deps import (  # noqa: F401 - 旧模块级名字 re-export
    as_chat_repository as _as_chat_repository,
    get_chat_repository,
)
from easy_agent.api.chat_live import (  # noqa: F401 - 兼容旧导入路径
    _LIVE_IDLE_ROUNDS,
    _LIVE_PAGE_LIMIT,
    _LIVE_POLL_SECONDS,
    _frame_is_terminal,
    chat_stream_live,
    chat_stream_status,
    router as chat_live_router,
)
from easy_agent.knowledges.chat.host_port import (
    register_host_capabilities,
)
from easy_agent.services.streaming.orchestrator import (
    chat_stream_generator as _host_chat_stream_generator,
)
from easy_agent.services.streaming.orchestrator import (
    friendly_error_message as _friendly_error_message,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/agent/chat",
    tags=["Chat"],
)


# 批次 4：repository provider 统一在 ``api.deps``；``get_chat_repository`` /
# ``_as_chat_repository`` 由上方导入在模块级 re-export，旧导入路径不变。
#
# T4：本轮的准备逻辑（鉴权 / 技能解析 / 找会话 / 专家预检 / 知识检索 / 文件上下文 /
# 用户消息落库 / 建 Agent / 登记 run）已下沉到 ``services/chat_turn``，路由只做
# 「参数校验 → prepare_turn / prepare_resume → 构造 SSE 响应」。流的中枢驱动
# （后台任务 + 事件 hub + run/流锁续约）仍留在本模块，因为它只决定「怎么回」而不是
# 「准备什么」。


_detached_bg_tasks: "set[asyncio.Task]" = set()

# 流锁续约间隔：TTL 180 秒，60 秒续一次（必须 ≤ TTL/3）。
# 心跳本身只是一条 ``UPDATE session_runtime``，每会话每分钟一次，可忽略；
# 反过来 TTL 不能太长：Pod 崩溃后锁要挂到过期才失效，这期间任何 Pod 的
# ``/stream/status`` 都报 active，用户刷新后会一直看到"生成中"。
_STREAM_HEARTBEAT_SECONDS = 60.0

# 流锁租约丢失时给客户端的终态文案：不暴露内部细节，只说明"被接走了、要重连"。
_LEASE_LOST_MESSAGE = "本轮生成的会话锁已失效（可能已在其它实例上重新生成），请刷新页面继续"


async def _record_run_end(runs, run_id: str, status: str, error: str, sid: str) -> None:
    """落 run 终态（Phase 2B）；写库失败只告警，不改变本轮流的结束方式。"""
    try:
        await asyncio.to_thread(finish_run, runs, run_id, status, error)
    except Exception as exc:  # noqa: BLE001 - 收尾失败不改变错误流
        logger.warning(f"[{sid}] 写入 run 终态失败（忽略）: {exc}")


async def _renew_run_lease(runs, run_id: str, sid: str) -> None:
    """续约 run 租约：失败只告警（可能已被启动恢复标记 interrupted），不打断正在执行的流。"""
    try:
        renewed = await asyncio.to_thread(renew_run, runs, run_id)
        if not renewed:
            logger.warning(
                f"[{sid}] run 租约续约失败"
                f"（可能已被恢复标记 interrupted）: {run_id}"
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{sid}] run 租约续约异常（忽略本次）: {exc}")


async def _renew_stream_lock(renew_stream, session_id: str, sid: str) -> bool:
    """续约跨副本流锁；返回 False 表示锁已被其它实例接管，应停止续约。"""
    try:
        renewed = await asyncio.to_thread(renew_stream, session_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[{sid}] 流锁续约异常（忽略本次）: {exc}")
        return True
    if not renewed:
        logger.warning(f"[{sid}] 流锁续约失败（可能已被其它实例接管），停止续约")
    return bool(renewed)


async def _stream_heartbeat(
    session_id: str,
    sid: str,
    renew_stream,
    runs,
    run_id,
    owner_task: "asyncio.Task | None" = None,
    lease_lost: "asyncio.Event | None" = None,
) -> None:
    """每隔 ``_STREAM_HEARTBEAT_SECONDS`` 续约一次：run 租约与流锁同频。

    **续约失败 = 租约已丢，必须终止本轮**。旧实现只是停止续约、让流继续跑，于是：
    Pod1 因网络抖动丢锁 → Pod2 在 TTL 到点后接管并清空事件表 → Pod1 还在往同一会话
    写事件和 checkpoint，构成双写。租约丢了就不该继续写，这是租约的字面语义。

    终止方式：置 ``lease_lost``（让 ``_drive_stream`` 把 run 记成 failed 而不是
    cancelled）并 cancel 持有流的那条任务。
    """
    while True:
        await asyncio.sleep(_STREAM_HEARTBEAT_SECONDS)
        if run_id is not None and runs is not None:
            await _renew_run_lease(runs, run_id, sid)
        if renew_stream is None:
            continue
        if await _renew_stream_lock(renew_stream, session_id, sid):
            continue
        logger.error(
            f"[{sid}] 流锁租约丢失（已被其它实例接管或续约失败），终止本轮生成，"
            "不再写事件/checkpoint"
        )
        if lease_lost is not None:
            lease_lost.set()
        if owner_task is not None and not owner_task.done():
            owner_task.cancel()
        return


async def _stop_heartbeat(heartbeat: "asyncio.Task | None") -> None:
    if heartbeat is None:
        return
    heartbeat.cancel()
    try:
        await heartbeat
    except (asyncio.CancelledError, Exception):  # noqa: BLE001, S110 - 心跳任务收尾噪声，无需处理
        pass


async def _clear_previous_events(stream_events, db, session_id: str, sid: str) -> None:
    # 拿到跨副本流锁后再清旧轮次事件：避免把另一个 Pod 正在写的流删掉。
    store = stream_events if stream_events is not None else db
    if store is None:
        return
    try:
        clear = getattr(store, "clear_stream_events", None)
        if callable(clear):
            await asyncio.to_thread(clear, session_id)
        else:
            await asyncio.to_thread(clear_stream_events, store, session_id)
    except Exception as exc:  # noqa: BLE001 - 清理失败不影响本轮流
        logger.warning(f"[{sid}] 清理旧 stream_events 失败: {exc}")


async def _drive_stream(
    gen, hub, queue: asyncio.Queue, emitter: SSEEmitter,
    session_id: str, sid: str, renew_stream, db, runs, stream_events, run_id,
    keep_events: bool = False,
    outcome: StreamOutcome | None = None,
) -> None:
    """后台任务：推进生成器，把每个 SSE 分片广播给 hub 的全部订阅者（原始客户端队列也是其一）。

    run 终态默认 failed，生成器正常耗尽且共享结果没有失败才改为 completed；
    CancelledError 分两种：``/cancel`` → cancelled，**流锁租约丢失 → failed**
    （租约丢了还继续写就是双写，见 :func:`_stream_heartbeat`）。

    ``keep_events``：HITL 恢复是**同一轮的延续**，不能清掉中断前已产生的事件，
    否则跨 Pod 打开时会话只剩落库的半截消息、思考过程永久消失。
    """
    bg = asyncio.current_task()
    registered = False
    heartbeat = None
    lease_lost = asyncio.Event()
    run_status = "failed"
    run_error = ""
    try:
        await aregister_stream_task(session_id, bg)
        registered = True
        if (db is not None or stream_events is not None) and not keep_events:
            await _clear_previous_events(stream_events, db, session_id, sid)
        if renew_stream is not None or (run_id is not None and runs is not None):
            heartbeat = asyncio.create_task(
                _stream_heartbeat(
                    session_id, sid, renew_stream, runs, run_id, bg, lease_lost
                )
            )
        async for chunk in gen:
            hub.broadcast(chunk)
        # 编排器将异常转成事件后正常返回，使用序列化前记录的结果，无需反解析 SSE。
        run_error = outcome.error if outcome is not None else ""
        # 流编排器也可能捕获取消、保存部分内容后返回 error 帧。
        if lease_lost.is_set():
            run_status = "failed"
            run_error = _LEASE_LOST_MESSAGE
        elif bg is not None and bg.cancelling():
            run_status = "cancelled"
        else:
            run_status = "failed" if run_error else "completed"
    except SessionBusyError as e:
        # 抢锁失败（通常意味着另一副本已经接管）。**必须推一条明确终态帧**：
        # 旧实现只 ``queue.put_nowait(None)``，客户端拿到一个空流，表现为"发完没反应"。
        # 这里只写本请求自己的队列，不 hub.broadcast：本会话此刻可能真有另一条流在本进程
        # 广播，往 hub 里塞错误帧会把别人的流打断。
        logger.warning(f"[{sid}] 会话已被占用，拒绝并发流: {e}")
        run_status = "failed"
        run_error = f"SessionBusyError: {e}"
        try:
            queue.put_nowait(
                emitter.error(
                    "本会话正在其它实例上生成回复，请稍候；已生成的内容会自动同步显示"
                )
            )
            queue.put_nowait(None)
        except asyncio.QueueFull:
            pass
    except asyncio.CancelledError:
        if lease_lost.is_set():
            logger.error(f"[{sid}] 流锁丢失，本轮已终止（run 记 failed）: {run_id}")
            run_status = "failed"
            run_error = _LEASE_LOST_MESSAGE
            hub.broadcast(emitter.error(_LEASE_LOST_MESSAGE))
        else:
            logger.info(f"[{sid}] 后台流式任务被取消（/cancel）")
            run_status = "cancelled"
        # 消费一次取消请求：否则 finally 里的落库 await 会被立刻再打断，
        # run 终态就写不进去了（异常仍会继续向上抛，任务最终仍是 cancelled）。
        if bg is not None and hasattr(bg, "uncancel"):
            bg.uncancel()
        raise
    except Exception as e:
        logger.error(f"[{sid}] 后台流式任务异常: {type(e).__name__}: {e}")
        run_status = "failed"
        run_error = f"{type(e).__name__}: {e}"
        hub.broadcast(emitter.error(f"处理失败: {_friendly_error_message(e)}"))
    finally:
        await _stop_heartbeat(heartbeat)
        if run_id is not None and runs is not None:
            # 先落 run 终态再关闭 hub：即使随后进程崩溃，恢复扫描也不会把它
            # 当成“仍在运行”（最坏情况是重复写一次 error，前端幂等展示）。
            try:
                await _record_run_end(runs, run_id, run_status, run_error, sid)
            except asyncio.CancelledError:
                logger.warning(f"[{sid}] 写入 run 终态被取消（忽略）: {run_id}")
        if registered:
            # 先关闭 hub（把终态交给持久化写入器），再释放 DB 流锁：
            # /stream/live 在别的 Pod 上看到 owner 失效时，终态事件已经入队。
            hub.close()
            await aunregister_stream_task(session_id, bg)


async def _detached_event_stream(
    gen, session_id: str, sid: str, renew_stream=None, db=None, runs=None,
    stream_events=None, run_id=None, keep_events: bool = False,
    outcome: StreamOutcome | None = None,
):
    """以「后台任务 + 事件中枢」驱动流式生成器，与客户端断开解耦。

    后台任务推进 astream 并把 SSE 分片入队；客户端断开（登出/超时/关页）时仅停止
    向前端推送，后台任务继续运行至完成（含 DB 持久化），长任务因此不会被中断。
    /cancel 仍生效：后台任务已 register_stream_task 注册，可被 task.cancel() 取消。
    刷新页面后，新客户端可通过 /stream/live 订阅同一中枢继续接收事件。

    ``run_id``（Phase 2B）：传入时后台任务与流锁心跳同频续约 run 租约，
    并在正常结束/取消/异常时落 ``completed`` / ``cancelled`` / ``failed`` 终态；
    不传（专家拒答流等非 run 生命周期）行为与改造前一致。``runs`` 是 run 记录域
    repository（``ChatRepository.runs``）；迁移期传 ``db`` 也兼容（内部会适配）。
    ``outcome`` 必须与会吞异常并转换为错误事件的生成器共享，驱动不从 SSE 推断结果。
    """
    emitter = SSEEmitter(session_id)
    # run 生命周期优先用领域 repository（``runs``）；迁移期仍允许只传 ``db`` 的旧调用方
    # （测试 / 尚未迁移的入口），由 run_registry 的适配层恢复成 AgentRunRepository。
    run_store = runs if runs is not None else db
    # 兜底：同会话已有运行中的流时，返回独立错误流（绝不加入对方 hub，
    # 否则新订阅者会收到旧流的 EOF/事件）。端点层通常已返回 409。
    if is_session_streaming(session_id):
        # 端点已登记 run；这条竞态路径不进入后台任务，需在这里落 failed 终态，
        # 否则只能等租约过期由启动恢复标记 interrupted。
        if run_id is not None and run_store is not None:
            await _record_run_end(run_store, run_id, "failed", "会话已被占用", sid)
        yield emitter.error("本会话正在生成回复，请等待完成或先取消当前生成")
        return

    hub = _session_stream_hubs.get(session_id)
    if hub is None or hub.done:
        hub = _StreamHub(session_id, db=db, stream_events=stream_events)
        _session_stream_hubs[session_id] = hub
    queue: asyncio.Queue = hub.subscribe()

    bg_task = asyncio.create_task(
        _drive_stream(
            gen, hub, queue, emitter, session_id, sid, renew_stream, db,
            run_store, stream_events, run_id, keep_events=keep_events,
            outcome=outcome,
        )
    )
    _detached_bg_tasks.add(bg_task)
    bg_task.add_done_callback(_detached_bg_tasks.discard)
    # 空闲心跳：模型长思考/长工具调用期间流上可能几十秒没有事件，反向代理
    # （nginx 默认 proxy_read_timeout 60s）会掐断连接，浏览器报
    # ERR_INCOMPLETE_CHUNKED_ENCODING。心跳只发给本订阅者，不进 hub 历史、不落库。
    heartbeat_seconds = heartbeat_interval_seconds()
    try:
        while True:
            if heartbeat_seconds > 0:
                try:
                    item = await asyncio.wait_for(
                        queue.get(), timeout=heartbeat_seconds
                    )
                except (asyncio.TimeoutError, TimeoutError):
                    yield SSE_HEARTBEAT_FRAME
                    continue
            else:
                item = await queue.get()
            if item is None:
                break
            yield item
    except asyncio.CancelledError:
        hub.unsubscribe(queue)
        logger.info(f"[{sid}] 客户端断开连接，后台流式任务继续执行（不取消）")


@router.post(
    "/stream",
    summary="流式聊天",
    description="发送聊天消息并接收实时流式响应，包括思考过程和最终内容。",
)
async def chat_stream(
    request: ChatRequest,
    db: Annotated[ChatRepository, Depends(get_chat_repository)],
    http_request: Request,
    username: Annotated[str, Depends(get_current_username)],
):
    repo = _as_chat_repository(db)
    # 参数校验留在路由：prepare_turn 只处理业务准备
    require_turn_content(request.message, request.files, request.skills)

    plan = await prepare_turn(
        request=request,
        http_request=http_request,
        repo=repo,
        username=username,
    )
    if isinstance(plan, RefusalPlan):
        # 专家相关性预检短路：不建 Agent、不登记 run，只推一条极简拒答流
        return sse_response(
            _detached_event_stream(
                with_session_token_totals(plan.event_stream, username, plan.session_id),
                plan.session_id,
                plan.sid,
                renew_stream=repo.renew_stream,
                stream_events=repo.stream_events,
                runs=repo.runs,
            )
        )
    return sse_response(
        _detached_event_stream(
            with_session_token_totals(plan.event_stream, username, plan.session_id),
            plan.session_id,
            plan.sid,
            renew_stream=repo.renew_stream,
            stream_events=repo.stream_events,
            runs=repo.runs,
            run_id=plan.run_id,
            outcome=plan.outcome,
        )
    )


@router.post(
    "/cancel",
    summary="取消当前聊天",
    description="取消当前正在进行的流式聊天请求，中断后端 astream 执行。",
)
async def cancel_chat(
    session_id: str,
    username: Annotated[str, Depends(get_current_username)],
):
    sid = session_id[-5:] if session_id else "unknown"
    logger.info(
        f"[{sid}] 🛑 收到终止请求 | 用户: {username} | "
        f"完整 session_id: {session_id} | 时间: {datetime.now().isoformat()}"
    )

    # 1. 取消正在运行的流式任务（向 astream 注入 CancelledError，
    #    streaming.py 的 except 分支会保存已生成的部分回复）
    cancelled = await cancel_stream_task(session_id)
    if cancelled:
        logger.info(f"[{sid}] 🛑 流式任务已中断")
    else:
        logger.info(f"[{sid}] 🛑 无正在运行的流式任务（可能已结束或前端 abort）")

    # 2. 清除 Agent 缓存，使下次请求重新创建 Agent
    remove_session_agent(session_id)

    logger.info(f"[{sid}] 🛑 终止处理完成")
    return {"status": "cancelled", "session_id": session_id}


@router.post(
    "/resume",
    summary="恢复 HITL 审批",
    description="用户审批文件删除操作后，恢复 Agent 执行流。",
)
async def chat_resume(
    request: ResumeRequest,
    db: Annotated[ChatRepository, Depends(get_chat_repository)],
    http_request: Request,
    username: Annotated[str, Depends(get_current_username)],
):
    repo = _as_chat_repository(db)
    plan = await prepare_resume(
        request=request,
        repo=repo,
        username=username,
    )
    return sse_response(
        _detached_event_stream(
            with_session_token_totals(plan.event_stream, username, plan.session_id),
            plan.session_id,
            plan.sid,
            renew_stream=repo.renew_stream,
            stream_events=repo.stream_events,
            runs=repo.runs,
            run_id=plan.run_id,
            outcome=plan.outcome,
            # 恢复是同一轮的延续：保留中断前的思考过程，跨 Pod 打开时才能回放完整过程
            keep_events=True,
        )
    )


# chat_live.router 自带 /agent/chat 前缀；本模块 router 也带同一前缀。
# FastAPI 的 include_router 会再次叠加父前缀（/agent/chat/agent/chat/...），
# 因此先挂到无前缀 router 上再并入，公开 URL 仍为 /agent/chat/stream/*。
_chat_live_mount = APIRouter()
_chat_live_mount.include_router(chat_live_router)
router.routes.extend(_chat_live_mount.routes)


# A1：知识域通过端口取宿主能力；api.chat 是应用启动必经路径，负责注册真实实现。
register_host_capabilities(
    chat_stream=chat_stream,
    host_chat_stream_generator=_host_chat_stream_generator,
    cancel_stream_task=cancel_stream_task,
    remove_session_agent=remove_session_agent,
    get_agent_config=_get_agent_config,
)
