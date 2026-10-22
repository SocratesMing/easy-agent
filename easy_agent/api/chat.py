"""Chat routes - streaming and non-streaming"""

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from easy_agent.db.repositories.chat_repository import ChatRepository
from easy_agent.db.repositories.stream_events import clear_stream_events
from easy_agent.models.db import SessionModel
from easy_agent.models.api import ChatRequest, ResumeRequest
from easy_agent.middleware import get_current_username
from easy_agent.knowledges.facade import (
    authorize_scoped_chat,
    chat_stream_generator,
    empty_knowledge_context,
    is_knowledge_panel_title,
    prepare_knowledge_chat,
)
from easy_agent.services import (
    resume_stream_generator,
    get_or_create_agent_for_session,
    get_session_expert,
    remove_session_agent,
    is_session_streaming,
    cancel_stream_task,
)
# 流锁、待修改专家要写库：用异步版本，写库放进线程，不占事件循环
from easy_agent.services.agent_manager import (
    aregister_stream_task,
    aset_session_edit_target,
    aunregister_stream_task,
)
from easy_agent.services import skill_library
from easy_agent.services import session_uploads as file_context
from easy_agent.services.hitl_state import HitlDecisionError
from easy_agent.services.sse_emitter import SSEEmitter
from easy_agent.services.session_runtime import SessionBusyError
from easy_agent.services.streaming import validate_resume_decisions
from easy_agent.services.run_registry import finish_run, new_run_id, renew_run, start_run
from easy_agent.services.stream_hub import (  # noqa: F401 - 兼容旧导入路径
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
from easy_agent.utils import (
    parse_file_content,
    SessionLogger,
)
from easy_agent.api.sessions import generate_workspace_name
from easy_agent.knowledges.chat.host_port import (
    register_host_capabilities,
)
from easy_agent.services.streaming import chat_stream_generator as _host_chat_stream_generator
from easy_agent.services import get_agent_config as _get_agent_config
from easy_agent.services.session_thread import (
    StaleApprovalError,
    abandon_pending_approval,
    handle_turn_id,
    resolve_resume_thread,
    session_thread_in_use,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/agent/chat",
    tags=["Chat"],
)


# 批次 4：repository provider 统一在 ``api.deps``；``get_chat_repository`` /
# ``_as_chat_repository`` 由上方导入在模块级 re-export，旧导入路径不变。

# 会话标题最长字数：太短列表里只剩「测试下」这种断句，太长又会撑破侧边栏，
# 具体显示宽度由前端省略号控制，这里保留足够信息量即可。
SESSION_TITLE_MAX_LEN = 30

# 会话标题里的指令前缀：输入框把已选「连接器」与「技能」还原成指令前缀发给模型
# （见前端 utils/skillSlash.js），例如 `使用连接器[高德地图] /docx 帮我排个版`。
# 标题直接取首条消息就会带上整串指令，会话列表看起来就是指令原文。
# 连接器指令格式固定（方括号内即连接器名），技能指令必须命中用户实际拥有的技能。
_CONNECTOR_DIRECTIVE_PATTERN = re.compile(r"^使用连接器\[([^\]]+)\]")
_SKILL_TOKEN_PATTERN = re.compile(r"^/(\S+)")


def _skill_display_name(skill_dir, fallback: str) -> str:
    """技能展示名（SKILL.md frontmatter 的 name），读取失败退回目录名。"""
    try:
        return skill_library.skill_metadata(skill_dir).get("name") or fallback
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"读取技能元数据失败 | {skill_dir} | {exc}")
        return fallback


def _strip_connector_directives(text: str) -> tuple[str, str]:
    """摘掉开头连续拼接的连接器指令，返回 (剩余文本, 首个连接器名)。

    连接器指令由输入框生成、格式固定，不需要校验名字是否存在。
    """
    first_name = ""
    while True:
        matched = _CONNECTOR_DIRECTIVE_PATTERN.match(text)
        if not matched:
            break
        first_name = first_name or matched.group(1).strip()
        text = text[matched.end() :].strip()
    return text, first_name


def split_leading_directives(message: str, username: str | None) -> tuple[str, str]:
    """拆掉消息开头拼接的内部指令，返回 (剩余正文, 兜底展示名)。

    先摘格式固定的 `使用连接器[名称]`，再摘 `/技能目录名`；技能目录名必须命中用户
    实际拥有的技能（含已停用），据此避免误删 `/workspace`、`/user-skills` 这类路径
    开头——历史上确实有用户这么提问。兜底展示名用于「只选了技能/连接器、没输入内容」
    时充当标题（技能展示名优先，其次连接器名），可能为空字符串。
    """
    text, connector_name = _strip_connector_directives((message or "").strip())
    if not text or not username:
        return text, connector_name

    try:
        skills = {
            item["dir_name"]: item["path"]
            for item in skill_library.list_user_skill_dirs(username)
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"读取用户技能目录失败，会话标题保留原文 | 用户: {username} | {exc}")
        return text, connector_name
    if not skills:
        return text, connector_name

    rest = text
    first_dir = ""
    while True:
        matched = _SKILL_TOKEN_PATTERN.match(rest)
        if not matched or matched.group(1) not in skills:
            break
        if not first_dir:
            first_dir = matched.group(1)
        rest = rest[matched.end() :].strip()

    if not first_dir:
        return text, connector_name
    return rest, _skill_display_name(skills[first_dir], first_dir)


def generate_session_title(
    message: str,
    files: list | None,
    username: str | None = None,
    fallback_name: str = "",
) -> str:
    """根据首条消息或首个上传文件生成会话标题。

    首条消息可能带着旧前端拼接的指令前缀（`使用连接器[名称]` / `/技能名`），标题只
    保留用户真正输入的内容。新前端把这些选择放进请求体结构化字段，消息文本本身就是
    用户原话，此时**只挂技能/连接器、没写正文**的场景由 ``fallback_name``（技能展示名
    优先、其次连接器名）兜底，避免标题只剩「未命名会话」这种信息量为零的结果。
    """
    if message and message.strip():
        body, text_fallback_name = split_leading_directives(message, username)
        title = body or text_fallback_name or fallback_name or message.strip()
    elif files and len(files) > 0:
        title = files[0].get("filename", "文件")
    elif fallback_name:
        title = fallback_name
    else:
        return "未命名会话"
    return title[:SESSION_TITLE_MAX_LEN] + "..." if len(title) > SESSION_TITLE_MAX_LEN else title


def create_new_session(
    repo: ChatRepository,
    session_id: str,
    username: str,
    message: str,
    files: list | None,
    fallback_name: str = "",
) -> str:
    """创建新会话记录并返回其 workspace_name。"""
    now = datetime.now().isoformat()
    session_title = generate_session_title(message, files, username, fallback_name)
    workspace_name = generate_workspace_name(session_id)
    session_data = SessionModel(
        session_id=session_id,
        title=session_title,
        messages=[],
        created_at=now,
        updated_at=now,
        username=username,
        workspace_name=workspace_name,
    )
    repo.create_session(session_data)
    repo.update_session_workspace_name(session_id, workspace_name)
    return workspace_name


_detached_bg_tasks: "set[asyncio.Task]" = set()

# 流锁续约间隔：默认 TTL 30 分钟，5 分钟续一次，避免长任务被其它副本误判过期。
_STREAM_HEARTBEAT_SECONDS = 300.0


async def _detached_event_stream(
    gen, session_id: str, sid: str, renew_stream=None, db=None, run_id=None
):
    """以「后台任务 + 事件中枢」驱动流式生成器，与客户端断开解耦。

    后台任务推进 astream 并把 SSE 分片入队；客户端断开（登出/超时/关页）时仅停止
    向前端推送，后台任务继续运行至完成（含 DB 持久化），长任务因此不会被中断。
    /cancel 仍生效：后台任务已 register_stream_task 注册，可被 task.cancel() 取消。
    刷新页面后，新客户端可通过 /stream/live 订阅同一中枢继续接收事件。

    ``run_id``（Phase 2B）：传入时在 _drive 内与流锁心跳同频续约 run 租约，
    并在正常结束/取消/异常时落 ``completed`` / ``cancelled`` / ``failed`` 终态；
    不传（专家拒答流等非 run 生命周期）行为与改造前一致。
    """
    emitter = SSEEmitter(session_id)
    # 兜底：同会话已有运行中的流时，返回独立错误流（绝不加入对方 hub，
    # 否则新订阅者会收到旧流的 EOF/事件）。端点层通常已返回 409。
    if is_session_streaming(session_id):
        # 端点已登记 run；这条竞态路径不进入 _drive，需在这里落 failed 终态，
        # 否则只能等租约过期由启动恢复标记 interrupted。
        if run_id is not None and db is not None:
            try:
                await asyncio.to_thread(
                    finish_run, db, run_id, "failed", "会话已被占用"
                )
            except Exception as exc:  # noqa: BLE001 - 收尾失败不改变错误流
                logger.warning(f"[{sid}] 写入 run 终态失败（忽略）: {exc}")
        yield emitter.error("本会话正在生成回复，请等待完成或先取消当前生成")
        return

    hub = _session_stream_hubs.get(session_id)
    if hub is None or hub.done:
        hub = _StreamHub(session_id, db=db)
        _session_stream_hubs[session_id] = hub
    queue: asyncio.Queue = hub.subscribe()
    state = {"disconnected": False}

    async def _drive():
        bg = asyncio.current_task()
        registered = False
        heartbeat = None
        # run 终态：默认 failed（任何未预期退出都不能留下 running 假象），
        # 只有生成器正常耗尽才改为 completed；CancelledError 标记 cancelled。
        run_status = "failed"
        run_error = ""
        try:
            await aregister_stream_task(session_id, bg)
            registered = True
            if db is not None:
                # 拿到跨副本流锁后再清旧轮次事件：避免把另一个 Pod 正在写的流删掉。
                try:
                    await asyncio.to_thread(clear_stream_events, db, session_id)
                except Exception as exc:  # noqa: BLE001 - 清理失败不影响本轮流
                    logger.warning(f"[{sid}] 清理旧 stream_events 失败: {exc}")

            async def _heartbeat():
                while True:
                    await asyncio.sleep(_STREAM_HEARTBEAT_SECONDS)
                    # run 租约与流锁同频续约：run 续约失败只告警（可能已被启动
                    # 恢复标记 interrupted），不打断正在执行的流。
                    if run_id is not None and db is not None:
                        try:
                            renewed_run = await asyncio.to_thread(
                                renew_run, db, run_id
                            )
                            if not renewed_run:
                                logger.warning(
                                    f"[{sid}] run 租约续约失败"
                                    f"（可能已被恢复标记 interrupted）: {run_id}"
                                )
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                f"[{sid}] run 租约续约异常（忽略本次）: {exc}"
                            )
                    if renew_stream is None:
                        continue
                    try:
                        renewed = await asyncio.to_thread(
                            renew_stream, session_id
                        )
                        if not renewed:
                            logger.warning(
                                f"[{sid}] 流锁续约失败（可能已被其它实例接管），停止续约"
                            )
                            return
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(f"[{sid}] 流锁续约异常（忽略本次）: {exc}")

            heartbeat = (
                asyncio.create_task(_heartbeat())
                if renew_stream is not None
                or (run_id is not None and db is not None)
                else None
            )
            async for chunk in gen:
                # 广播给所有订阅者（原始客户端队列也是订阅者之一）
                hub.broadcast(chunk)
            run_status = "completed"
        except SessionBusyError as e:
            # 进程内兜底：不向共享 hub 广播（会污染正在运行的流），只结束本请求队列。
            logger.warning(f"[{sid}] 会话已被占用，拒绝并发流: {e}")
            run_error = f"SessionBusyError: {e}"
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        except asyncio.CancelledError:
            logger.info(f"[{sid}] 后台流式任务被取消（/cancel）")
            run_status = "cancelled"
            raise
        except Exception as e:
            logger.error(f"[{sid}] 后台流式任务异常: {type(e).__name__}: {e}")
            run_status = "failed"
            run_error = f"{type(e).__name__}: {e}"
            hub.broadcast(emitter.error(f"处理失败: {e}"))
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                try:
                    await heartbeat
                except (asyncio.CancelledError, Exception):  # noqa: BLE001
                    pass
            if run_id is not None and db is not None:
                # 先落 run 终态再关闭 hub：即使随后进程崩溃，恢复扫描也不会把它
                # 当成“仍在运行”（最坏情况是重复写一次 error，前端幂等展示）。
                try:
                    await asyncio.to_thread(
                        finish_run, db, run_id, run_status, run_error
                    )
                except asyncio.CancelledError:
                    logger.warning(f"[{sid}] 写入 run 终态被取消（忽略）: {run_id}")
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"[{sid}] 写入 run 终态失败（忽略）: {exc}")
            if registered:
                # 先关闭 hub（把终态交给持久化写入器），再释放 DB 流锁：
                # /stream/live 在别的 Pod 上看到 owner 失效时，终态事件已经入队。
                hub.close()
                await aunregister_stream_task(session_id, bg)

    bg_task = asyncio.create_task(_drive())
    _detached_bg_tasks.add(bg_task)
    bg_task.add_done_callback(_detached_bg_tasks.discard)
    try:
        while True:
            item = await queue.get()
            if item is None:
                break
            yield item
    except asyncio.CancelledError:
        state["disconnected"] = True
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

    # 正文可以为空：只挂技能 / 只上传文件时本来就没有正文，
    # 三者全空才视为无效请求（与前端 canSend 的判定一致）。
    repo = _as_chat_repository(db)
    if (
        not (request.message or "").strip()
        and not request.files
        and not request.skills
    ):
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    session_id = request.session_id
    message_id = request.message_id or str(uuid.uuid4())
    is_knowledge_panel_session = False

    # 知识库会话必须独立执行 fail-closed 鉴权。即使宿主 EasyAgent 在测试、代理层或
    # 行内环境中替换了通用聊天身份依赖，也不能让 X-Username 等兼容头绕过知识权限。
    username = await authorize_scoped_chat(
        request=http_request,
        db=repo.database,
        session_id=session_id,
        username=username,
    )

    sid = session_id[-5:] if session_id else "new"

    # 本轮技能：优先用请求体的结构化字段 skills（输入框技能胶囊选中项，技能目录名），
    # 同时兼容消息文本开头的 `/目录名` 指令（旧前端 / 手打指令），合并去重后校验归属
    # （目录必须真实存在于该用户技能库，含已停用）。命中且已启用的技能在本轮 pin 进
    # 对话（DA-13，见下文 pinned_skills）；落库正文、气泡与会话标题用的都是用户原话。
    turn_skills, unknown_skills = skill_library.resolve_turn_skills(
        request.skills, request.message, username
    )
    turn_skill_dirs = [item["dir_name"] for item in turn_skills]
    if turn_skill_dirs or unknown_skills:
        logger.info(
            f"[{sid}] 🧩 技能指令 | 命中: {turn_skill_dirs or '无'} | "
            f"未识别: {unknown_skills or '无'}"
        )
    # 只挂技能、没写正文时的标题兜底（取技能展示名）
    if turn_skills:
        turn_fallback_name = turn_skills[0]["name"]
    else:
        turn_fallback_name = ""

    logger.info(
        f"[{sid}] 聊天请求 | message: {request.message[:50]}{'...' if len(request.message) > 50 else ''} | "
        f"deep_think: {request.enable_deep_think} | "
        f"web_search: {request.enable_web_search} | model: {request.model or '(active)'}"
    )

    if session_id is None:
        session_id = str(uuid.uuid4())
        workspace_name = create_new_session(
            repo, session_id, username, request.message, request.files, turn_fallback_name
        )
    else:
        session = repo.get_session(session_id)
        if not session:
            session_id = str(uuid.uuid4())
            workspace_name = create_new_session(
                repo, session_id, username, request.message, request.files, turn_fallback_name
            )
        elif (session.username or "") != (username or ""):
            # 会话属于其他用户 -> 拒绝访问，不泄露会话是否存在
            raise HTTPException(status_code=404, detail="会话不存在")
        else:
            workspace_name = session.workspace_name or ""
            is_knowledge_panel_session = is_knowledge_panel_title(session.title)
            if (
                len(session.messages) == 0
                and not is_knowledge_panel_session
            ):
                session.title = generate_session_title(
                    request.message, request.files, username, turn_fallback_name
                )
                session.updated_at = datetime.now().isoformat()
                repo.update_session_meta(
                    session.session_id,
                    session.title,
                    session.updated_at,
                    session.username,
                )

    # 并发互斥：同一会话同一时刻只允许一条流式生成。否则两条流会共享 agent、
    # last-assistant 行与 hub，互相覆盖/EOF（实测新流 0 条内容即结束）。
    if is_session_streaming(session_id):
        raise HTTPException(
            status_code=409,
            detail="本会话正在生成回复，请等待完成或先取消当前生成",
        )

    # DA-13：选中的技能 pin 进本轮（框架在第一次模型调用前把技能全文加进对话），
    # 不再在模型输入前拼 `/目录名`；用户手打在正文开头的 `/目录名` 原样保留，同样会 pin。
    model_message = request.message
    pinned_skills = skill_library.pinned_skill_names(turn_skills)
    not_pinned = [item["dir_name"] for item in turn_skills if not item["enabled"]]
    if not_pinned:
        logger.info(f"[{sid}] 🧩 已停用的技能不 pin | {not_pinned}")

    # 「待修改专家」绑定：用户在专家面板点「编辑」后，前端把专家 ID 放进请求的
    # 结构化字段（不进消息正文，用户不可见、不可改）。这里做归属校验——只接受
    # 本人创建的可用专家，预置专家 / 他人的专家 / 不存在的 ID 一律忽略并告警。
    # 绑定后由 save_expert 工具在修改时取用（见 tools/expert_writer.py）。
    if request.pending_expert_id:
        from easy_agent.tools.expert_writer import resolve_owned_expert

        owned = resolve_owned_expert(repo.database, username, request.pending_expert_id)
        if owned is not None:
            await aset_session_edit_target(session_id, request.pending_expert_id)
        else:
            logger.warning(
                f"[{sid}] pending_expert_id 非本人可用专家，忽略绑定 | "
                f"expert={request.pending_expert_id}"
            )

    parsed_content = model_message
    knowledge_chat = await prepare_knowledge_chat(
        request=http_request,
        db=repo.database,
        session_id=session_id,
        question=request.message,
    )
    # ── 上传文件内容 ─────────────────────────────────────────────────
    # 新机制（config.file_context.enabled=true，默认）：
    #   解析正文在**上传时**已落盘到会话工作区 uploads/，文件清单 uploads/index.md
    #   由 agent.py 作为记忆文件注入 system prompt —— 后续每一轮（不必再上传）模型
    #   都知道本会话传过哪些文件、正文在哪，并按需用内置 grep/read_file 检索，
    #   不必每轮把全文塞进上下文。这里只补一小段"本轮上传文件的开头预览"，
    #   让"传完立刻提问"少一次工具往返。
    # 回退（enabled=false）：保持改造前行为——首轮把解析全文放进 system prompt，
    #   非首轮把全文拼进用户消息（"第二轮起看不到文件内容"的旧问题即源于此）。
    system_prompt_parts = []
    if request.files and not file_context.is_enabled():
        # 判断是否为新建会话（首次消息）：新建会话的上传文件内容注入系统提示词，
        # 非首次会话则拼接到用户消息（保持原有行为）。
        _existing = repo.get_session(session_id)
        is_new_session = (not _existing) or (len(_existing.messages) == 0)

        # 依次解析上传文件内容（支持 docx/excel/pdf/txt/md 等）
        file_context_parts = []
        for file_info in request.files:
            file_path = file_info.get("file_path", "")
            if not file_path:
                continue
            filename = file_info.get("filename", "")
            content = parse_file_content(file_path)
            if content:
                file_context_parts.append((filename, content))
                logger.info(
                    f"[{session_id[-5:]}] 📎 文件已解析（回退模式）| {filename} | "
                    f"内容长度: {len(content)} 字符"
                )
            else:
                logger.warning(f"[{session_id[-5:]}] 文件解析为空: {filename}")

        if file_context_parts:
            if is_new_session:
                sections = [
                    f"### 文件: {fn}\n{c}" for fn, c in file_context_parts
                ]
                system_prompt_parts.append(
                    "## 用户上传的文件内容（已为你解析，请基于这些内容回应用户）\n\n"
                    + "\n\n".join(sections)
                )
            else:
                file_contents = [
                    f"[文件: {fn}]\n{c}" for fn, c in file_context_parts
                ]
                # 文件块在最前，正文在后
                parsed_content = "\n\n".join(file_contents) + "\n\n" + model_message
    elif request.files:
        preview = file_context.build_turn_preview(
            session_id, username, request.files, repo
        )
        if preview:
            parsed_content = preview + "\n\n" + parsed_content
            logger.info(
                f"[{session_id[-5:]}] 📎 已注入本轮上传文件开头预览 | {len(preview)} 字符"
            )

    system_prompt_extra = "\n\n".join(system_prompt_parts)

    page_context = (request.page_context or "").strip()
    if page_context:
        parsed_content = (
            "<page_context>\n"
            "以下是用户发起提问时所在页面的上下文，仅供参考；与问题无关时忽略。\n"
            f"{page_context}\n"
            "</page_context>\n\n"
            + parsed_content
        )

    user_message = {
        "role": "user",
        "content": request.message,
        "timestamp": datetime.now().isoformat(),
        # DA-03：本轮 id（= message_id），与 thread 里本轮 HumanMessage 的 id 一致
        "turn_id": message_id,
    }
    if request.files:
        user_message["files"] = request.files
    if page_context:
        user_message["page_context"] = page_context
    # 结构化技能目录名：气泡按它渲染技能标签，不再需要从消息文本里反扒 `/目录名`
    if turn_skill_dirs:
        user_message["skills"] = turn_skill_dirs
    # DA-03：会话 thread 下新输入会丢弃上一轮挂起的中断，DB 里的待审批同步标为拒绝
    if session_thread_in_use(_get_agent_config()):
        abandon_pending_approval(repo, session_id)
    repo.add_message(session_id, user_message)

    session_logger = SessionLogger(
        session_id=session_id,
        username=username,
        workspace="",
        system_prompt="",
    )
    session_logger.log_user_message(
        message=request.message,
        files=request.files,
        message_id=message_id,
    )

    # 连接器不再随每轮消息提交：本会话启用哪些连接器由输入框「+」面板的开关决定
    # （见 api/connectors.py 的 /agent/connectors/session），agent_manager 按会话读取。
    agent = await get_or_create_agent_for_session(
        session_id, username, workspace_name, model_name=request.model,
        system_prompt_extra=system_prompt_extra,
        enable_web_search=request.enable_web_search,
    )

    # Phase 2B：把本轮 run 持久化（owner + lease）。登记失败只记日志，
    # 不影响聊天；request_json 只含请求元数据（files 已是元数据，不含二进制）。
    run_id = new_run_id(session_id, message_id)
    start_run(
        repo.database,
        run_id,
        session_id,
        username=username or "",
        message_id=message_id,
        request_json=request.model_dump_json(),
    )

    return StreamingResponse(
        _detached_event_stream(
            chat_stream_generator(
                request=request,
                db=repo.database,
                agent=agent,
                session_id=session_id,
                message_id=message_id,
                username=username,
                http_request=http_request,
                parsed_content=parsed_content,
                session_logger=session_logger,
                pinned_skills=pinned_skills,
                context_prefix=knowledge_chat.context,
                initial_events=knowledge_chat.initial_events,
                assistant_metadata=knowledge_chat.assistant_metadata,
            ),
            session_id,
            sid,
            renew_stream=repo.renew_stream,
            db=repo.database,
            run_id=run_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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


def _last_session_message(repo, session_id: str) -> dict | None:
    """会话最后一条消息（审批兼容期核对旧句柄用）；只在会话 thread 模式下才需要读。"""
    if not session_thread_in_use(_get_agent_config()):
        return None
    session = repo.get_session(session_id)
    messages = getattr(session, "messages", None) or []
    return messages[-1] if messages else None


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
    session_id = request.session_id
    thread_id = request.thread_id
    sid = session_id[-5:] if session_id else "resume"

    logger.info(
        f"[{sid}] 🔔 HITL 恢复请求 | thread_id={thread_id} | "
        f"decisions={request.decisions}"
    )

    # 获取会话的 workspace_name（校验归属，防止跨用户恢复他人会话）
    session_meta = repo.get_owned_session_meta(session_id, username)
    workspace_name = session_meta["workspace_name"] or ""

    # 同一会话已有流在跑时拒绝 resume（它依赖进程内缓存与 checkpointer 状态）。
    if is_session_streaming(session_id):
        raise HTTPException(
            status_code=409,
            detail="本会话正在生成回复，请等待完成或先取消当前生成",
        )

    # 审批句柄由后端固定生成 ``f"{session_id}-{message_id}"``；绝不信任前端
    # 传入的任意 thread_id：官方 Postgres checkpointer 是全局共享的，否则可读取/
    # 续跑他人会话的 checkpoint（跨用户隔离绕过）。
    if handle_turn_id(session_id, thread_id) is None:
        logger.warning(
            f"[{sid}] 拒绝非法的 HITL thread_id | session={session_id} | thread={thread_id}"
        )
        raise HTTPException(status_code=400, detail="非法的 thread_id")

    # 复用缓存的 Agent 实例（保留 checkpointer 中的 interrupt 状态）。
    # 必须带上该会话当前生效的专家 ID，否则「在专家会话里触发 HITL 审批 -> 恢复」
    # 会被判定为「专家变更」而驱逐缓存，重建出通用 Agent（人设丢失）。
    # 取值安全性：_session_expert 与 _session_agents 同生共死（只在 Agent 创建成功后
    # 一起写入、清理时一起删除），因此这里取到的值要么与缓存中的专家一致 —— 命中缓存
    # 原样复用、人设保留；要么为 ""（缓存已失效）—— 等价于通用 Agent 重建，
    # 行为与引入专家模块之前逐字一致。
    # 连接器无需传参：agent_manager 直接读该会话的开关记录，缓存失效时按同一套集合重建。
    agent = await get_or_create_agent_for_session(
        session_id,
        username,
        workspace_name,
        expert_agent_id=get_session_expert(session_id),
    )

    # DA-03：句柄映射到真正要恢复的 thread（会话 thread 模式下是 session_id）；
    # 会话已有新消息或审批已处理时句柄失效，返回 409。
    try:
        resume_thread = await resolve_resume_thread(
            agent,
            session_id=session_id,
            handle=thread_id,
            last_message=_last_session_message(repo, session_id),
        )
    except StaleApprovalError as exc:
        logger.warning(f"[{sid}] 拒绝已失效的审批 | thread={thread_id} | {exc}")
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # DA-02：决策必须能对上当前待审批中断（interrupt_id / 数量 / 允许的决策）。
    # 批处理 HITL 会先把恢复值存进 checkpoint 再校验，非法决策会让线程卡死，必须提前拒绝。
    try:
        await validate_resume_decisions(agent, resume_thread, request.decisions)
    except HitlDecisionError as exc:
        logger.warning(f"[{sid}] 拒绝无效的审批决策 | thread={thread_id} | {exc}")
        raise HTTPException(status_code=400, detail=f"审批决策无效：{exc}") from exc

    # Phase 2B：恢复 run 同样是独立 run（message_id 为空则 uuid），request_json
    # 只保存 decisions/session_id/thread_id 元数据，便于审计恢复来源。
    run_message_id = request.message_id or str(uuid.uuid4())
    run_id = new_run_id(session_id, run_message_id)
    start_run(
        repo.database,
        run_id,
        session_id,
        username=username or "",
        message_id=run_message_id,
        request_json=json.dumps(
            {
                "session_id": session_id,
                "thread_id": thread_id,
                "decisions": request.decisions,
            },
            ensure_ascii=False,
        ),
    )

    return StreamingResponse(
        _detached_event_stream(
            resume_stream_generator(
                db=repo.database,
                agent=agent,
                session_id=session_id,
                thread_id=resume_thread,
                decisions=request.decisions,
                username=username,
                message_id=request.message_id,
                handle=thread_id,
            ),
            session_id,
            sid,
            renew_stream=repo.renew_stream,
            db=repo.database,
            run_id=run_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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
