"""本轮聊天的准备工作（T4）：从 ``api/chat.py`` 下沉到服务层。

路由只做参数校验 → ``prepare_turn`` / ``prepare_resume`` → 构造 SSE 响应；本轮需要的
知识库会话鉴权、技能解析、会话打开 / 新建、并发互斥、专家提示词与依赖技能补装、职责
范围预检与拒答、待修改专家绑定、知识库检索、上传文件上下文、用户消息落库、建 Agent、
登记 run 全部在这里完成。这样同一套准备工作可以不经 HTTP 直接调用与测试
（见 ``tests/api/test_chat_turn_prep.py``）。

顺序约束（**不可颠倒**，与改造前 ``api/chat.py`` 逐行一致）：

1. 知识库会话鉴权（fail-closed）最先；
2. 打开 / 新建会话，然后 ``reject_concurrent_stream``——409 必须在用户消息落库之前，
   否则会话里会多出一条没有回复的用户消息；
3. 专家分支：提示词 → 依赖技能补装 → 职责范围预检（硬闸）→ 命中即短路拒答。
   预检必须早于文件解析与知识库检索：被拒答的消息不该为这些付成本，更重要的是
   「Agent 未创建」时任何工具都不可达（提示词层面的边界块做不到这点）；
4. 待修改专家绑定 → 知识库检索 → 上传文件上下文 → 用户消息落库 → 建 Agent → 登记 run。

事件循环约束（第 1 节通用约定第 7 条）：数据库层是同步的，查库 / 写库都经
``asyncio.to_thread`` / ``run_blocking`` 放进线程；进程内状态（Agent 缓存、流锁、
``invalidate_user_agents``）留在事件循环上。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncGenerator

from easy_agent.db.repositories.chat_repository import ChatRepository
from easy_agent.knowledges.facade import (
    authorize_scoped_chat,
    chat_stream_generator,
    is_knowledge_panel_title,
    prepare_knowledge_chat,
)
from easy_agent.models.api import ChatRequest, ResumeRequest
from easy_agent.models.db import SessionModel
from easy_agent.models.exceptions import BadRequestError, ConflictError, NotFoundError
from easy_agent.services import (
    agent_manager,
    expert_runtime,
    run_registry,
    session_thread,
    session_uploads as file_context,
    skill_library,
    skill_provision,
    tool_scope,
)
from easy_agent.services.agent_manager import (
    get_agent_config as _get_agent_config,
    get_or_create_agent_for_session,
)
from easy_agent.services.hitl_state import HitlDecisionError
from easy_agent.services.session_thread import StaleApprovalError
from easy_agent.services.streaming.orchestrator import (
    resume_stream_generator,
    validate_resume_decisions,
)
from easy_agent.utils.blocking import run_blocking
from easy_agent.utils.file_parser import parse_file_content
from easy_agent.utils.prompt_probe import describe_prompt
from easy_agent.utils.session_logger import SessionLogger

logger = logging.getLogger(__name__)

# 会话标题最长字数：太短列表里只剩「测试下」这种断句，太长又会撑破侧边栏，
# 具体显示宽度由前端省略号控制，这里保留足够信息量即可。
SESSION_TITLE_MAX_LEN = 30

# 会话标题里的指令前缀：输入框把已选「连接器」与「技能」还原成指令前缀发给模型
# （见前端 utils/skillSlash.js），例如 `使用连接器[高德地图] /docx 帮我排个版`。
# 标题直接取首条消息就会带上整串指令，会话列表看起来就是指令原文。
# 连接器指令格式固定（方括号内即连接器名），技能指令必须命中用户实际拥有的技能。
_CONNECTOR_DIRECTIVE_PATTERN = re.compile(r"^使用连接器\[([^\]]+)\]")
_SKILL_TOKEN_PATTERN = re.compile(r"^/(\S+)")


def _generate_workspace_name(session_id: str) -> str:
    """生成会话工作目录名（与 ``api/sessions.generate_workspace_name`` 同一格式）。

    ``services`` 是 L4、``api`` 是 L5，服务层不能反向 import 路由层，因此这里保留三个
    字段的格式化逻辑；两处都改时保持格式一致。
    """
    now = datetime.now()
    return f"{now.strftime('%Y%m%d')}_{now.strftime('%H%M%S')}_{session_id[:5]}"


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
    workspace_name = _generate_workspace_name(session_id)
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


# ── 本轮请求的准备步骤：通用对话与专家对话共用（专家对话走同一入口 + expert_agent_id）──


def require_turn_content(message: str | None, files: list | None, skills: list | None) -> None:
    """正文、上传文件和技能三者全空时拒绝请求，与前端 canSend 的判定一致。

    正文本身可以为空：只挂技能、只上传文件时本来就没有正文。
    """
    if not (message or "").strip() and not files and not skills:
        raise BadRequestError("消息内容不能为空")


def reject_concurrent_stream(session_id: str) -> None:
    """同一会话已有流式生成在跑时返回 409（``ConflictError`` → HTTP 409）。

    两条流会共享 agent、last-assistant 行与 hub，互相覆盖 / EOF（实测新流 0 条内容即结束）。
    要在用户消息落库之前调用：``_detached_event_stream`` 里也有同样的兜底，但走到那里时
    用户消息已经落库，会话里会多出一条没有回复的消息。
    """
    if agent_manager.is_session_streaming(session_id):
        raise ConflictError("本会话正在生成回复，请等待完成或先取消当前生成")


@dataclass(frozen=True)
class TurnSkills:
    """本轮技能。"""

    dirs: list[str]  # 命中的技能目录名：落库到用户消息的 skills 字段，气泡据此渲染技能标签
    pinned: list[str]  # 本轮 pin 进对话的技能名（DA-13），只含已启用的
    fallback_name: str  # 只挂技能、没写正文时的会话标题兜底：首个技能的展示名


def resolve_request_skills(
    skills: list[str] | None, message: str, username: str, sid: str
) -> TurnSkills:
    """解析本轮技能：请求体 skills 字段优先，兼容消息开头的 `/目录名` 指令。

    skills 字段是输入框技能胶囊的选中项（技能目录名），`/目录名` 是旧前端或用户手打的
    写法。两者合并去重后校验归属：目录必须真实存在于该用户的技能库（含已停用）。命中且
    已启用的技能在本轮 pin 进对话，框架在第一次模型调用前把技能全文加进对话，不再在
    模型输入前拼 `/目录名`；手打在正文开头的 `/目录名` 原样保留，同样会 pin。落库正文、
    气泡和会话标题用的都是用户原话。
    """
    items, unknown = skill_library.resolve_turn_skills(skills, message, username)
    dirs = [item["dir_name"] for item in items]
    if dirs or unknown:
        logger.info(
            f"[{sid}] 🧩 技能指令 | 命中: {dirs or '无'} | 未识别: {unknown or '无'}"
        )
    not_pinned = [item["dir_name"] for item in items if not item["enabled"]]
    if not_pinned:
        logger.info(f"[{sid}] 🧩 已停用的技能不 pin | {not_pinned}")
    return TurnSkills(
        dirs=dirs,
        pinned=skill_library.pinned_skill_names(items),
        fallback_name=items[0]["name"] if items else "",
    )


def open_turn_session(
    repo: ChatRepository,
    session_id: str | None,
    username: str,
    message: str,
    files: list | None,
    fallback_name: str = "",
) -> tuple[str, str]:
    """取本轮会话，返回 (session_id, workspace_name)。

    没传 session_id、或库里没有这个会话时新建一个；会话属于其他用户时返回 404，不泄露
    会话是否存在。已有会话还没有消息时按本轮消息重新生成标题；知识库面板会话的标题是
    面板标识，不改。
    """
    if session_id is not None:
        session = repo.get_session(session_id)
        if session:
            if (session.username or "") != (username or ""):
                raise NotFoundError("会话不存在")
            if len(session.messages) == 0 and not is_knowledge_panel_title(session.title):
                session.title = generate_session_title(message, files, username, fallback_name)
                session.updated_at = datetime.now().isoformat()
                repo.update_session_meta(
                    session.session_id,
                    session.title,
                    session.updated_at,
                    session.username,
                )
            return session_id, session.workspace_name or ""
    session_id = str(uuid.uuid4())
    workspace_name = create_new_session(repo, session_id, username, message, files, fallback_name)
    return session_id, workspace_name


@dataclass(frozen=True)
class TurnUploads:
    """本轮上传文件给模型的上下文。"""

    model_input: str  # 交给模型的本轮输入：用户原话，前面可能加上传文件的开头预览或全文
    prompt_parts: list[str]  # 追加到系统提示词的段落：回退模式下首轮的文件全文
    parsed_files: int  # 回退模式下解析出正文的文件数


def build_turn_uploads(
    repo: ChatRepository,
    session_id: str,
    username: str,
    files: list | None,
    message: str,
) -> TurnUploads:
    """把本轮上传的文件转成模型上下文。

    默认机制（config.file_context.enabled=true）：解析正文在上传时已落到会话工作区
    uploads/，文件清单 uploads/index.md 作为记忆文件注入系统提示词，之后每一轮模型都
    知道本会话传过哪些文件、正文在哪，按需用内置 grep / read_file 检索，不必每轮把全文
    塞进上下文。这里只在模型输入前补一小段本轮上传文件的开头预览，让「传完立刻提问」
    少一次工具往返。

    回退模式（enabled=false）保持改造前的行为：首轮把解析全文放进系统提示词，之后的
    轮次把全文拼在用户消息前面（「第二轮起看不到文件内容」的老问题就出在这里）。
    """
    sid = session_id[-5:]
    if not files:
        return TurnUploads(message, [], 0)
    if file_context.is_enabled():
        preview = file_context.build_turn_preview(session_id, username, files, repo)
        if not preview:
            return TurnUploads(message, [], 0)
        logger.info(f"[{sid}] 📎 已注入本轮上传文件开头预览 | {len(preview)} 字符")
        return TurnUploads(preview + "\n\n" + message, [], 0)

    # 依次解析上传文件内容（支持 docx/excel/pdf/txt/md 等）
    parsed: list[tuple[str, str]] = []
    for file_info in files:
        file_path = file_info.get("file_path", "")
        if not file_path:
            continue
        filename = file_info.get("filename", "")
        content = parse_file_content(file_path)
        if content:
            parsed.append((filename, content))
            logger.info(
                f"[{sid}] 📎 文件已解析（回退模式）| {filename} | 内容长度: {len(content)} 字符"
            )
        else:
            logger.warning(f"[{sid}] 文件解析为空: {filename}")
    if not parsed:
        return TurnUploads(message, [], 0)

    # 新建会话（首条消息）的文件全文进系统提示词，之后的轮次拼在用户消息前面
    existing = repo.get_session(session_id)
    if not existing or len(existing.messages) == 0:
        sections = [f"### 文件: {fn}\n{c}" for fn, c in parsed]
        prompt = (
            "## 用户上传的文件内容（已为你解析，请基于这些内容回应用户）\n\n"
            + "\n\n".join(sections)
        )
        return TurnUploads(message, [prompt], len(parsed))
    # 文件块在最前，正文在后
    blocks = [f"[文件: {fn}]\n{c}" for fn, c in parsed]
    return TurnUploads("\n\n".join(blocks) + "\n\n" + message, [], len(parsed))


def _session_last_message(repo: ChatRepository, session_id: str) -> dict | None:
    session = repo.get_session(session_id)
    messages = getattr(session, "messages", None) or []
    return messages[-1] if messages else None


def _last_session_message(repo: ChatRepository, session_id: str) -> dict | None:
    """会话最后一条消息（审批兼容期核对旧句柄用）；只在会话 thread 模式下才需要读。"""
    if not session_thread.session_thread_in_use(_get_agent_config()):
        return None
    return _session_last_message(repo, session_id)


# ── 准备结果 ──────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TurnPlan:
    """``prepare_turn`` 的产物：路由拿它直接构造 SSE 响应。

    ``event_stream`` 是已经绑定好本轮参数、尚未开始执行的知识适配流；路由交给
    ``_detached_event_stream`` 后才会真正推进（含 run 生命周期与 hub 广播）。
    """

    session_id: str
    sid: str
    message_id: str
    workspace_name: str
    agent: Any
    run_id: str
    parsed_content: str
    knowledge_chat: Any
    event_stream: AsyncGenerator[str, None]


@dataclass(frozen=True)
class RefusalPlan:
    """专家相关性预检拒答：不建 Agent、不登记 run，只推一条极简拒答流。"""

    session_id: str
    sid: str
    classification: str
    refusal: str
    event_stream: AsyncGenerator[str, None]


@dataclass(frozen=True)
class ResumePlan:
    """``prepare_resume`` 的产物。"""

    session_id: str
    sid: str
    agent: Any
    resume_thread: str
    run_id: str
    event_stream: AsyncGenerator[str, None]


async def prepare_turn(
    *,
    request: ChatRequest,
    http_request: Any,
    repo: ChatRepository,
    username: str,
) -> TurnPlan | RefusalPlan:
    """准备一轮流式聊天：返回 ``TurnPlan``（正常）或 ``RefusalPlan``（专家预检拒答）。

    调用方（路由）负责参数校验；本函数按模块顶部注释的顺序完成全部准备，并把本轮流
    （``event_stream``）一并构造好。异常统一用 ``easy_agent.models.exceptions`` 里的领域
    异常（400 / 404 / 409），由 app 层映射成与改造前逐字一致的 HTTP 错误体。
    """
    session_id = request.session_id
    message_id = request.message_id or str(uuid.uuid4())

    # 知识库会话必须独立执行 fail-closed 鉴权。即使宿主 EasyAgent 在测试、代理层或
    # 行内环境中替换了通用聊天身份依赖，也不能让 X-Username 等兼容头绕过知识权限。
    username = await authorize_scoped_chat(
        request=http_request,
        db=repo.database,
        session_id=session_id,
        username=username,
    )

    sid = session_id[-5:] if session_id else "new"
    turn = resolve_request_skills(request.skills, request.message, username, sid)

    logger.info(
        f"[{sid}] 聊天请求 | message: {request.message[:50]}{'...' if len(request.message) > 50 else ''} | "
        f"deep_think: {request.enable_deep_think} | "
        f"web_search: {request.enable_web_search} | model: {request.model or '(active)'} | "
        f"expert: {request.expert_agent_id or '(未选专家)'} | "
        f"pending_expert: {request.pending_expert_id or '(无)'}"
    )

    # 数据库层是同步的：本轮的查库 / 落库都放进线程，不占事件循环（并发的 SSE 流不跟着停）
    session_id, workspace_name = await asyncio.to_thread(
        open_turn_session,
        repo, session_id, username, request.message, request.files, turn.fallback_name,
    )

    # 并发互斥：同一会话同一时刻只允许一条流式生成（必须在用户消息落库之前）
    reject_concurrent_stream(session_id)

    # ── 专家分支：提示词加载 + 依赖技能补装 + 职责范围预检（硬闸）──────────
    # 顺序不能动：预检必须早于文件解析与知识库检索——被拒答的消息不该为这些付成本，
    # 更重要的是「Agent 未创建」时任何工具都不可达（提示词层面的边界块做不到这点）。
    # 专家对话只有这一个入口（``/agent/chat/stream``），不再有独立的专家执行接口。
    expert_repo = repo.experts
    expert_id = ""
    expert_prompt = ""
    skill_notice = ""
    expert_skills: list[str] = []
    if request.expert_agent_id:
        # 我的专家（数据库）优先，公共专家（目录）兜底；读取失败降级为通用 Agent
        expert_id, expert_prompt, _expert_source = await asyncio.to_thread(
            expert_runtime.load_expert_prompt, expert_repo, username, request.expert_agent_id, sid
        )
        if not expert_prompt:
            expert_id = ""

        # 依赖技能补装：只往用户清单里登记（DA-13 S2），不复制技能包本体。
        # 缺包 / 未配置 / 用户停用都**不阻断对话**，转成一段运行时提示交给模型，
        # 让模型如实说明「技能未就绪」，而不是虚构技能产物。
        # 整段准备（读库 + 清单读改写）放进线程；清单增删已带按用户跨进程文件锁
        # （见 agent/builtin_skills.py），并发请求不会互相覆盖。缓存驱逐是进程内状态，
        # 必须留在事件循环上（T14.2）。
        skill_report = await run_blocking(
            skill_provision.provision_expert_skills,
            username,
            expert_id,
            invalidate_cache=False,
        )
        if skill_report.get("installed"):
            try:
                skill_report["cache_invalidated"] = int(
                    agent_manager.invalidate_user_agents(username) or 0
                )
            except Exception as exc:  # noqa: BLE001 - 驱逐失败最多是下一轮才生效
                logger.warning(f"⚠️ 依赖技能添加后驱逐缓存 Agent 失败 | 用户: {username} | {exc}")
        skill_notice = skill_provision.build_skill_runtime_notice(skill_report)
        # 专家绑定的技能包：自建专家取库字段 export_required_skills，公共专家取
        # config.yaml 的 required_skills（同一入口，见 skill_provision.required_skills_of）
        expert_skills = list(skill_report.get("required") or [])
        logger.info(
            f"[{sid}] 🧩 专家技能包声明 | expert={expert_id or '(无)'} | 来源={_expert_source or '(无)'} | "
            f"声明={expert_skills or '（未绑定 → 技能库全量可见）'} | "
            f"本轮可用={skill_report.get('existing') or []} | 本次补装={skill_report.get('installed') or []} | "
            f"未就绪={[(i.get('skill'), i.get('reason')) for i in (skill_report.get('skipped') or [])] or '无'}"
        )

    # 技能作用域（专家技能硬隔离）：专家绑定了技能包时，只挂「专家技能包 ∪ 本轮另选」；
    # 专家没绑定则保持全量可见（= 改造前行为）。见 services/tool_scope.py。
    scope = tool_scope.resolve_tool_scope(
        username,
        expert_skills=expert_skills,
        turn_skills=turn.dirs,
        expert_id=expert_id,
        sid=sid,
        enabled=tool_scope.tool_scope_enabled((_get_agent_config() or {}).get("config")),
    )
    logger.info(f"[{sid}] 🎛️ 本轮技能作用域 | {scope.describe()}")
    # 本轮技能暂存：save_expert 在创建 / 修改专家时据此落库 export_required_skills
    # （技能胶囊只在请求体里，工具侧看不到）。非空才覆盖，见 set_session_turn_skills。
    cached_turn_skills = agent_manager.set_session_turn_skills(session_id, turn.dirs)
    if turn.dirs:
        logger.info(
            f"[{sid}] 🧩 本轮技能已暂存（供保存专家使用）| 选中={turn.dirs} | "
            f"会话暂存={cached_turn_skills}"
        )

    gate = await expert_runtime.relevance_gate(
        expert_repo,
        session_id,
        username,
        expert_id,
        request.message,
        sid,
        files=request.files,
        prompt_text=expert_prompt,
    )
    if gate is not None:
        classification, refusal = gate
        # 拒答也要落用户消息：否则会话列表里只剩一条 assistant 拒答，上下文断裂
        await asyncio.to_thread(
            expert_runtime.persist_user_turn,
            expert_repo,
            session_id,
            username,
            request,
            expert_id,
            turn.dirs,
            turn_id=message_id,
        )
        expert_runtime.session_logger_for(session_id, username).log_user_message(
            message=request.message,
            files=request.files,
            message_id=message_id,
        )
        logger.info(
            f"[{sid}] 🚫 相关性预检短路拒答 | expert={expert_id} | "
            f"判定={classification} | Agent 未创建 | 拒答长度={len(refusal)}"
        )
        return RefusalPlan(
            session_id=session_id,
            sid=sid,
            classification=classification,
            refusal=refusal,
            event_stream=expert_runtime.refusal_event_stream(
                session_id, expert_repo, refusal
            ),
        )

    # 「待修改专家」绑定：用户在专家面板点「编辑」后，前端把专家 ID 放进请求的
    # 结构化字段（不进消息正文，用户不可见、不可改）。这里做归属校验——只接受
    # 本人创建的可用专家，预置专家 / 他人的专家 / 不存在的 ID 一律忽略并告警。
    # 绑定后由 save_expert 工具在修改时取用（见 tools/expert_writer.py）。
    if request.pending_expert_id:
        from easy_agent.tools.expert_writer import resolve_owned_expert

        owned = await asyncio.to_thread(
            resolve_owned_expert, repo.experts, username, request.pending_expert_id
        )
        if owned is not None:
            await agent_manager.aset_session_edit_target(session_id, request.pending_expert_id)
        else:
            logger.warning(
                f"[{sid}] pending_expert_id 非本人可用专家，忽略绑定 | "
                f"expert={request.pending_expert_id}"
            )

    knowledge_chat = await prepare_knowledge_chat(
        request=http_request,
        db=repo.database,
        session_id=session_id,
        question=request.message,
        metadata_filter=request.knowledge_metadata_filter,
        knowledge_base_ids=request.knowledge_base_ids,
    )
    uploads = await asyncio.to_thread(
        build_turn_uploads, repo, session_id, username, request.files, request.message
    )
    parsed_content = uploads.model_input
    # 专家提示词与技能提示排在最后：agent.py 的拼接顺序为
    # [system_prompt, user_info, web_search_prompt, system_prompt_extra]，
    # 放在 extra 末尾即 recency 最高，专家人设可主导通用人设。
    extra_parts = list(uploads.prompt_parts)
    if skill_notice:
        # 依赖技能不可用时的运行时提示（让模型如实说明，而不是虚构技能产物）
        extra_parts.append(skill_notice)
    if expert_prompt:
        extra_parts.append(expert_prompt)
    system_prompt_extra = "\n\n".join(extra_parts)
    if expert_prompt:
        # 可观测点：交给 agent_manager 的最终附加内容（含文件块与专家提示词）
        logger.info(
            f"[{sid}] 🧩 动态附加内容已组装 | expert={expert_id} | "
            f"文件块={uploads.parsed_files} | {describe_prompt('extra', system_prompt_extra)}"
        )

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
    # 专家随用户消息落库：回答面板取其上一条用户消息的该字段展示头像 + 名称，
    # 刷新 / 历史加载后不丢，同一会话不同轮次各对应自己那一轮的专家。
    expert_meta = await asyncio.to_thread(
        expert_runtime.expert_meta_for_message, expert_repo, username, expert_id
    )
    if expert_meta:
        user_message["expert"] = expert_meta
    # 结构化技能目录名：气泡按它渲染技能标签，不再需要从消息文本里反扒 `/目录名`
    if turn.dirs:
        user_message["skills"] = turn.dirs
    # DA-03：会话 thread 下新输入会丢弃上一轮挂起的中断，DB 里的待审批同步标为拒绝
    if session_thread.session_thread_in_use(_get_agent_config()):
        await asyncio.to_thread(
            session_thread.abandon_pending_approval, repo, session_id
        )
    await asyncio.to_thread(repo.add_message, session_id, user_message)

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
    # expert_agent_id 只用于「专家变更 -> 驱逐缓存 Agent」，提示词已在上面的
    # system_prompt_extra 里拼好（专家切换立即生效，不串用上一轮的人设）。
    agent = await get_or_create_agent_for_session(
        session_id, username, workspace_name, model_name=request.model,
        system_prompt_extra=system_prompt_extra,
        enable_web_search=request.enable_web_search,
        expert_agent_id=expert_id or None,
        # 技能作用域：None = 全量可见（未绑定技能包的专家 / 通用对话），
        # 非 None = 只挂这些技能目录（专家技能包 ∪ 本轮另选）
        skill_whitelist=(
            None if scope.skill_whitelist is None else list(scope.skill_whitelist)
        ),
    )

    # Phase 2B：把本轮 run 持久化（owner + lease）。登记失败只记日志，
    # 不影响聊天；request_json 只含请求元数据（files 已是元数据，不含二进制）。
    run_id = run_registry.new_run_id(session_id, message_id)
    await asyncio.to_thread(
        run_registry.start_run,
        repo.runs,
        run_id,
        session_id,
        username=username or "",
        message_id=message_id,
        request_json=request.model_dump_json(),
    )

    event_stream = chat_stream_generator(
        request=request,
        db=repo.database,
        agent=agent,
        session_id=session_id,
        message_id=message_id,
        username=username,
        http_request=http_request,
        parsed_content=parsed_content,
        session_logger=session_logger,
        pinned_skills=turn.pinned,
        context_prefix=knowledge_chat.context,
        initial_events=knowledge_chat.initial_events,
        assistant_metadata=knowledge_chat.assistant_metadata,
    )
    return TurnPlan(
        session_id=session_id,
        sid=sid,
        message_id=message_id,
        workspace_name=workspace_name,
        agent=agent,
        run_id=run_id,
        parsed_content=parsed_content,
        knowledge_chat=knowledge_chat,
        event_stream=event_stream,
    )


async def prepare_resume(
    *,
    request: ResumeRequest,
    repo: ChatRepository,
    username: str,
) -> ResumePlan:
    """准备一次 HITL 恢复：校验归属 / 并发 / 审批句柄 / 决策，然后登记新的 run。"""
    session_id = request.session_id
    thread_id = request.thread_id
    sid = session_id[-5:] if session_id else "resume"

    logger.info(
        f"[{sid}] 🔔 HITL 恢复请求 | thread_id={thread_id} | "
        f"decisions={request.decisions}"
    )

    # 获取会话的 workspace_name（校验归属，防止跨用户恢复他人会话）
    session_meta = await asyncio.to_thread(repo.get_owned_session_meta, session_id, username)
    workspace_name = session_meta["workspace_name"] or ""

    # 同一会话已有流在跑时拒绝 resume（它依赖进程内缓存与 checkpointer 状态）。
    reject_concurrent_stream(session_id)

    # 审批句柄由后端固定生成 ``f"{session_id}-{message_id}"``；绝不信任前端
    # 传入的任意 thread_id：官方 SQLite checkpointer 是全局共享的，否则可读取/
    # 续跑他人会话的 checkpoint（跨用户隔离绕过）。
    if session_thread.handle_turn_id(session_id, thread_id) is None:
        logger.warning(
            f"[{sid}] 拒绝非法的 HITL thread_id | session={session_id} | thread={thread_id}"
        )
        raise BadRequestError("非法的 thread_id")

    # 多副本按 pod 隔离 checkpoint：审批只能在记录它的那个 pod 上恢复，落错 pod 时不必再建 Agent
    try:
        await asyncio.to_thread(
            session_thread.ensure_approval_on_this_instance,
            lambda: _session_last_message(repo, session_id),
            thread_id,
        )
    except StaleApprovalError as exc:
        logger.warning(f"[{sid}] 审批不在本实例 | thread={thread_id} | {exc}")
        raise ConflictError(str(exc)) from exc

    # 复用缓存的 Agent 实例（保留 checkpointer 中的 interrupt 状态）。
    # 必须带上该会话当前生效的专家 ID，否则「在专家会话里触发 HITL 审批 -> 恢复」
    # 会被判定为「专家变更」而驱逐缓存，重建出通用 Agent（人设丢失）。
    # 取值安全性：SessionState.expert_key 与 SessionState.agent 同生共死（只在 Agent
    # 创建成功后一起写入、清理时一起删除），因此这里取到的值要么与缓存中的专家一致 —— 命中缓存
    # 原样复用、人设保留；要么为 ""（缓存已失效）—— 等价于通用 Agent 重建，
    # 行为与引入专家模块之前逐字一致。
    # 连接器无需传参：agent_manager 直接读该会话的开关记录，缓存失效时按同一套集合重建。
    agent = await get_or_create_agent_for_session(
        session_id,
        username,
        workspace_name,
        expert_agent_id=agent_manager.get_session_expert(session_id),
    )

    # DA-03：句柄映射到真正要恢复的 thread（会话 thread 模式下是 session_id）；
    # 会话已有新消息或审批已处理时句柄失效，返回 409。
    last_message = await asyncio.to_thread(_last_session_message, repo, session_id)
    try:
        resume_thread = await session_thread.resolve_resume_thread(
            agent,
            session_id=session_id,
            handle=thread_id,
            last_message=last_message,
        )
    except StaleApprovalError as exc:
        logger.warning(f"[{sid}] 拒绝已失效的审批 | thread={thread_id} | {exc}")
        raise ConflictError(str(exc)) from exc

    # DA-02：决策必须能对上当前待审批中断（interrupt_id / 数量 / 允许的决策）。
    # 批处理 HITL 会先把恢复值存进 checkpointer 再校验，非法决策会让线程卡死，必须提前拒绝。
    try:
        await validate_resume_decisions(agent, resume_thread, request.decisions)
    except StaleApprovalError as exc:
        logger.warning(f"[{sid}] 拒绝已失效的审批 | thread={thread_id} | {exc}")
        raise ConflictError(str(exc)) from exc
    except HitlDecisionError as exc:
        logger.warning(f"[{sid}] 拒绝无效的审批决策 | thread={thread_id} | {exc}")
        raise BadRequestError(f"审批决策无效：{exc}") from exc

    # Phase 2B：恢复 run 同样是独立 run（message_id 为空则 uuid），request_json
    # 只保存 decisions/session_id/thread_id 元数据，便于审计恢复来源。
    run_message_id = request.message_id or str(uuid.uuid4())
    run_id = run_registry.new_run_id(session_id, run_message_id)
    await asyncio.to_thread(
        run_registry.start_run,
        repo.runs,
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

    event_stream = resume_stream_generator(
        db=repo.database,
        agent=agent,
        session_id=session_id,
        thread_id=resume_thread,
        decisions=request.decisions,
        username=username,
        message_id=request.message_id,
        handle=thread_id,
    )
    return ResumePlan(
        session_id=session_id,
        sid=sid,
        agent=agent,
        resume_thread=resume_thread,
        run_id=run_id,
        event_stream=event_stream,
    )
