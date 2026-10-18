"""专家 Agent API：创建 / 修改 / 列表 / 删除 / 选中执行。

设计要点（见 ``docs/expert-agent-solution.md`` §5、§6）：

- **鉴权**：全部 ``Depends(get_current_username)``，再用 ``user_id`` 做归属校验；
  非本人资源一律返回 **404**（不泄露存在性），与 ``chat.py`` 现有做法一致；
- **落库与目录**：``db/expert_store.py``（单表 CRUD）+ ``services/expert_service.py``
  （LLM 生成与版本目录），本文件只做编排与参数校验；
- **执行**：``/agent/stream_export_run`` 是薄适配层，内部复用 ``chat.py`` 现成的
  ``create_new_session`` / ``_detached_event_stream`` 与 ``chat_stream_generator``，
  SSE 事件格式与 ``/agent/chat/stream`` 完全一致，前端可复用同一套解析逻辑；
- **专家提示词走 ``system_prompt_extra``**：本方案不改 ``agent.py``，专家人设通过
  现成通道追加到系统提示词末尾（recency 最高，实际效果上主导通用人设）。

批次 2B 起非路由辅助逻辑（用户/记录转换、配置读取、生成落库、提示词加载与护栏、
消息落库、相关性预检、短路拒答流）移到 ``api/expert_helpers.py``；本文件只保留
FastAPI 路由与 repository provider，旧内部名字经 ``__all__`` re-export 继续可用。
"""

import logging
import time
import uuid
from datetime import datetime
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from easy_agent.db.repositories.expert_repository import ExpertRepository
from easy_agent.db.expert_store import (
    STATUS_BUILDING,
)
from easy_agent.middleware import get_current_username
from easy_agent.models.api import ChatRequest
from easy_agent.services import (
    chat_stream_generator,
    get_or_create_agent_for_session,
    invalidate_user_agents,
)
from easy_agent.services import public_expert_service, skill_library
from easy_agent.services import session_uploads as file_context
from easy_agent.services.expert_service import (
    build_expert_ids,
    remove_expert_dir,
    validate_create_params,
)
from easy_agent.utils import parse_file_content
from easy_agent.utils.prompt_probe import describe_prompt
from easy_agent.api.chat import _detached_event_stream, create_new_session, generate_session_title
from easy_agent.api.deps import (  # noqa: F401 - 旧模块级名字 re-export
    as_expert_repository as _as_expert_repository,
    get_expert_repository,
)
from easy_agent.api.expert_helpers import (
    _as_str_list,
    _current_user_id,
    _ensure_name_available,
    _expert_flags,
    _expert_meta_for_message,
    _expert_profile,
    _expert_tag_name,
    _generate_and_store,
    _get_config,
    _load_expert_prompt,
    _persist_user_turn,
    _precheck_disable_thinking,
    _precheck_llm,
    _recent_context,
    _refusal_event_stream,
    _relevance_gate,
    _session_logger_for,
    _tag_options,
    _to_public,
    _with_guardrail,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Expert"])

# 批次 2B：辅助逻辑的实现已移至 ``api/expert_helpers.py``，这里按旧名字 re-export，
# 既有 ``from easy_agent.api.expert import _to_public`` / ``expert_api._expert_profile``
# 之类的引用继续可用（同一对象，非拷贝）。
__all__ = [
    "_as_str_list",
    "_current_user_id",
    "_ensure_name_available",
    "_expert_flags",
    "_expert_meta_for_message",
    "_expert_profile",
    "_expert_tag_name",
    "_generate_and_store",
    "_get_config",
    "_load_expert_prompt",
    "_persist_user_turn",
    "_precheck_disable_thinking",
    "_precheck_llm",
    "_recent_context",
    "_refusal_event_stream",
    "_relevance_gate",
    "_session_logger_for",
    "_tag_options",
    "_to_public",
    "_with_guardrail",
]


# 批次 4：repository provider 统一在 ``api.deps``；旧名字在此 re-export。


# ── 请求模型 ──────────────────────────────────────────────────────────


class ExpertAddRequest(BaseModel):
    """创建专家。``type`` 为需求要求的前端组装标识（本期仅做校验）。

    分类标签 ``tag_id`` 不接收前端传参：它由大模型根据配置的 ``expert_tags``
    自动归类（见 ``_generate_and_store``）。
    """

    name_cn: str = Field(..., description="专家中文名（必填）")
    prompt: str = Field(..., description="用户对专家的描述 / 提示词（必填）")
    type: str = Field(default="agent", description="组装标识：agent=创建专家")
    session_id: Optional[str] = Field(default=None, description="创建该专家的会话（溯源用）")
    model: Optional[str] = Field(default=None, description="用于生成配置的模型（config.models 的 key）")


class ExpertUpdateRequest(BaseModel):
    """修改专家：重新生成配置并写入新版本目录。

    同 ``ExpertAddRequest``：``tag_id`` 不接收前端传参，修改后由大模型重新归类。
    """

    export_agent_id: str = Field(..., description="专家 Agent ID")
    name_cn: Optional[str] = Field(default=None, description="专家中文名（不传则沿用）")
    prompt: Optional[str] = Field(default=None, description="专家描述（不传则沿用）")
    modify_type: str = Field(default="edit", description="修改类型标识：edit=编辑")
    model: Optional[str] = Field(default=None, description="用于生成配置的模型")


class ExpertDelRequest(BaseModel):
    export_agent_id: str = Field(..., description="专家 Agent ID")
    hard: bool = Field(default=False, description="True=物理删除（含目录）；False=软删除")


class ExpertRunRequest(BaseModel):
    """选中专家执行（SSE 流式），字段与 ``/agent/chat/stream`` 对齐。"""

    message: str = Field(
        ...,
        description="用户消息内容（可为空：只挂技能、或只上传文件时没有正文）",
        min_length=0,
    )
    session_id: Optional[str] = Field(default=None, description="会话ID；不传则新建会话")
    message_id: Optional[str] = Field(default=None, description="消息ID")
    expert_agent_id: Optional[str] = Field(
        default=None, description="选中的专家 Agent ID；不传=通用 Agent"
    )
    model: Optional[str] = Field(default=None, description="模型名称")
    enable_deep_think: bool = Field(default=False, description="是否启用深度思考")
    enable_web_search: bool = Field(default=False, description="是否开启联网搜索")
    files: Optional[List[dict]] = Field(default=None, description="上传的文件列表")
    skills: Optional[List[str]] = Field(
        default=None,
        description=(
            "本轮选中的技能目录名（输入框技能胶囊选中项），后端据此把 `/目录名` 指令拼进"
            "**模型输入**并落库到用户消息的 skills 字段（气泡按它渲染技能标签）。"
            "为空或未传时回退解析消息文本开头的 `/目录名`（兼容旧前端/手打指令）"
        ),
    )


# ── 接口 ──────────────────────────────────────────────────────────────


@router.post("/agent/add", summary="创建专家 Agent")
async def add_expert(
    request: ExpertAddRequest,
    db: Annotated[ExpertRepository, Depends(get_expert_repository)],
    username: Annotated[str, Depends(get_current_username)],
):
    repo = _as_expert_repository(db)
    if (request.type or "agent").lower() != "agent":
        raise HTTPException(status_code=400, detail="type 仅支持 agent（本期不含技能模块）")
    try:
        validate_create_params(request.name_cn, request.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user_id = _current_user_id(repo, username)
    name_cn = request.name_cn.strip()
    store = repo

    # 中文名唯一：同一用户下不允许出现同名专家（含已软删除的记录）
    _ensure_name_available(store, user_id, name_cn)

    expert_agent_id, name_en = build_expert_ids(request.name_cn)

    # 先落 building 占位行：即使生成途中进程崩溃，也能查到一条记录供前端重试。
    # tag_id 不收前端传参，先置空，由大模型自动归类后回写。
    store.create(
        {
            "export_agent_id": expert_agent_id,
            "session_id": request.session_id or "",
            "user_id": user_id,
            "export_name_en": name_en,
            "export_name_cn": name_cn,
            "export_prompt": request.prompt,
            "export_description": "",
            "export_skills_names": [],
            "export_features_prompt": [],
            "export_file_path": "",
            "agent_status": STATUS_BUILDING,
            "export_version": 1,
        }
    )

    record = await _generate_and_store(
        repo=repo,
        username=username,
        expert_agent_id=expert_agent_id,
        name_cn=name_cn,
        user_prompt=request.prompt,
        version=1,
        model_name=request.model,
        session_id=request.session_id,
    )
    logger.info("专家创建成功 | %s | %s", expert_agent_id, request.name_cn)
    return _to_public(record)


@router.post("/agent/update", summary="修改专家 Agent")
async def update_expert(
    request: ExpertUpdateRequest,
    db: Annotated[ExpertRepository, Depends(get_expert_repository)],
    username: Annotated[str, Depends(get_current_username)],
):
    repo = _as_expert_repository(db)
    user_id = _current_user_id(repo, username)
    store = repo
    record = store.get_owned(request.export_agent_id, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="专家不存在")

    name_cn = (request.name_cn or record.get("export_name_cn") or "").strip()
    user_prompt = request.prompt if request.prompt is not None else record.get("export_prompt", "")
    try:
        validate_create_params(name_cn, user_prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 改名时同样要求唯一（排除自己，否则原样保存也会被拦下）
    _ensure_name_available(
        store,
        user_id,
        name_cn,
        exclude_agent_id=request.export_agent_id,
        is_update=True,
    )

    # ★ 新版本目录 v{n+1}，不覆盖 v{n}：防止运行中的旧会话读到半成品配置
    new_version = int(record.get("export_version") or 1) + 1
    await _generate_and_store(
        repo=repo,
        username=username,
        expert_agent_id=request.export_agent_id,
        name_cn=name_cn,
        user_prompt=user_prompt,
        version=new_version,
        model_name=request.model,
        session_id=record.get("session_id") or None,
    )
    # 元数据（中文名/描述）随本次修改一起刷新；tag_id 不收前端传参，
    # 由 _generate_and_store 里的大模型自动归类结果覆盖。
    meta = {"export_name_cn": name_cn, "export_prompt": user_prompt}
    store.update_fields(request.export_agent_id, meta)

    # 让该用户所有缓存 Agent 失效，下次请求用新版本的提示词重建
    invalidate_user_agents(username)

    updated = store.get_owned(request.export_agent_id, user_id) or {}
    logger.info("专家修改成功 | %s | v%s", request.export_agent_id, new_version)
    return _to_public(updated)


@router.get("/agent/list", summary="获取专家 Agent 列表")
def list_experts(
    db: Annotated[ExpertRepository, Depends(get_expert_repository)],
    username: Annotated[str, Depends(get_current_username)],
    keyword: str = Query("", description="按中英文名模糊搜索"),
    status: str = Query("", description="按状态过滤：active/failed/disabled"),
    include_disabled: bool = Query(False, description="是否包含已删除（软删）的专家"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(10, ge=1, le=20, description="每页条数"),
):
    """我的专家列表（从数据库读取）。

    注意：这里**故意用普通 ``def`` 而非 ``async def``**——本接口内的 DB 访问
    （``get_user_by_username`` / ``list_by_user``）是同步阻塞调用，``async def``
    会让它们直接占用事件循环：聊天流等长任务占住循环时，本接口会被动排队
    （前端「+」面板表现为偶发数秒的「加载中…」）。改成 ``def`` 后 FastAPI 自动
    放到线程池执行，互不拖累。
    """
    repo = _as_expert_repository(db)
    t0 = time.perf_counter()
    user_id = _current_user_id(repo, username)
    store = repo
    total, items = store.list_by_user(
        user_id,
        keyword=keyword or "",
        status=status or "",
        include_disabled=include_disabled,
        page=page,
        page_size=page_size,
    )
    # tags：分类标签候选（配置文件 expert_tags），前端渲染筛选 chips 用；
    # 大模型自动归类后每个专家的 tag_id 即取自这份列表。
    try:
        tags = _tag_options(_get_config())
    except HTTPException:
        tags = []
    # 总耗时日志：覆盖 user_id 解析 + 分页查询 + tags 组装全过程（毫秒）。
    # 偶发秒级耗时多半是等连接池 / SQLite 全局锁，对比这里的耗时与前端体感
    # 即可定位瓶颈在接口内还是事件循环排队。
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "📊 获取用户专家列表接口总耗时 | user=%s | 命中=%s/%s | keyword=%r | 耗时=%.1fms",
        username,
        len(items),
        total,
        keyword or "",
        elapsed_ms,
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "tags": tags,
        "items": [_to_public(r) for r in items],
    }


@router.get("/agent/public-list", summary="获取公共（预置）专家列表")
def list_public_experts():
    """公共专家 = 随项目预置的专家目录（默认 ``experts/``），**只读**。

    不查库、不按用户区分：所有用户看到同一份列表；卡片字段与
    ``/agent/list`` 保持一致（另带 ``scope="public"``），前端可复用同一套
    卡片与召唤链路。列表不下发提示词全文，召唤时按需读取。

    与 ``/agent/list`` 同理用普通 ``def``：目录扫描是同步文件 IO，放线程池
    执行，避免「+」面板打开时被事件循环上的其他任务拖慢。
    """
    t0 = time.perf_counter()
    items = public_expert_service.discover_public_experts()
    # tags：与 /agent/list 同一份分类候选（配置文件 expert_tags），前端 chips 复用
    try:
        tags = _tag_options(_get_config())
    except HTTPException:
        tags = []
    root = public_expert_service.public_experts_root()
    logger.info(
        "📂 获取预置公共专家列表 | 目录: %s | 数量: %s | 耗时: %.1fms",
        root.absolute(),
        len(items),
        (time.perf_counter() - t0) * 1000,
    )
    return {
        "total": len(items),
        "tags": tags,
        "dir": str(root.absolute()),
        "items": items,
    }


@router.post("/agent/del", summary="删除专家 Agent")
async def delete_expert(
    request: ExpertDelRequest,
    db: Annotated[ExpertRepository, Depends(get_expert_repository)],
    username: Annotated[str, Depends(get_current_username)],
):
    repo = _as_expert_repository(db)
    user_id = _current_user_id(repo, username)
    store = repo
    record = store.get_owned(request.export_agent_id, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="专家不存在")

    removed = store.delete(request.export_agent_id, user_id, hard=request.hard)
    if request.hard:
        remove_expert_dir(username, request.export_agent_id)

    invalidate_user_agents(username)
    logger.info(
        "专家删除完成 | %s | 方式: %s",
        request.export_agent_id,
        "物理删除" if request.hard else "软删除",
    )
    return {
        "status": "deleted",
        "export_agent_id": request.export_agent_id,
        "hard": bool(request.hard),
        "removed": bool(removed),
    }


@router.post(
    "/agent/stream_export_run",
    summary="选中专家 Agent 执行（SSE 流式）",
    description=(
        "选中专家后动态构建 Agent 来回答；不传 expert_agent_id 时退化为通用 Agent。"
        "事件格式与 /agent/chat/stream 一致。"
    ),
)
async def stream_export_run(
    request: ExpertRunRequest,
    db: Annotated[ExpertRepository, Depends(get_expert_repository)],
    http_request: Request,
    username: Annotated[str, Depends(get_current_username)],
):
    # 正文可以为空：只挂技能 / 只上传文件时本来就没有正文（与 chat.py 一致）
    repo = _as_expert_repository(db)
    if (
        not (request.message or "").strip()
        and not request.files
        and not request.skills
    ):
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    session_id = request.session_id
    message_id = request.message_id or str(uuid.uuid4())
    sid = session_id[-5:] if session_id else "new"

    # 本轮技能（与 chat.py 一致）：优先用请求体 skills 字段（技能目录名），兼容消息文本
    # 开头的 `/目录名` 指令；命中的指令只拼进模型输入，落库正文保持用户原话。
    turn_skills, unknown_skills = skill_library.resolve_turn_skills(
        request.skills, request.message, username
    )
    turn_skill_dirs = [item["dir_name"] for item in turn_skills]
    if turn_skill_dirs or unknown_skills:
        logger.info(
            f"[{sid}] 🧩 技能指令 | 命中: {turn_skill_dirs or '无'} | "
            f"未识别: {unknown_skills or '无'}"
        )
    if turn_skills:
        turn_fallback_name = turn_skills[0]["name"]
    else:
        turn_fallback_name = ""

    logger.info(
        f"[{sid}] 专家执行请求 | expert: {request.expert_agent_id or '(通用)'} | "
        f"message: {request.message[:50]}{'...' if len(request.message) > 50 else ''}"
    )

    # ── 1. 会话处理：与 /agent/chat/stream 保持一致 ────────────────────
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
            raise HTTPException(status_code=404, detail="会话不存在")
        else:
            workspace_name = session.workspace_name or ""
            if len(session.messages) == 0:
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

    # ── 2. 专家校验与提示词加载 ────────────────────────────────────────
    # 我的专家（数据库）优先，公共专家（experts 目录）兜底；找不到 ID 时 404。
    # 加载日志（含长度/指纹/开头）在 _load_expert_prompt 内统一打印（可观测点 ①）。
    expert_prompt = ""
    expert_id = ""
    if request.expert_agent_id:
        expert_id, expert_prompt, _source = _load_expert_prompt(
            repo, username, request.expert_agent_id, sid
        )
        if not expert_prompt:
            # 读取失败（目录被清 / 文件缺失）：降级为通用 Agent，不阻断对话
            expert_id = ""

    # ── 2.5 相关性预检（硬闸，Agent 不启动） ────────────────────────────
    # 职责边界块只是提示词层面的软约束（模型可能不遵守）；这里先用一次轻量 LLM
    # 调用判断本轮消息是否落在专家领域内：全不相关直接短路拒答——Agent 不创建、
    # 工具零可达。预检关闭/异常/超时一律放行（fail-open）。
    # 放在文件解析之前：拒答的消息不该为解析附件付出代价，也不该触发预览注入。
    gate = await _relevance_gate(
        repo,
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
        _persist_user_turn(repo, session_id, username, request, expert_id, turn_skill_dirs)
        _session_logger_for(session_id, username).log_user_message(
            message=request.message,
            files=request.files,
            message_id=message_id,
        )
        logger.info(
            f"[{sid}] 🚫 相关性预检短路拒答 | expert={expert_id} | "
            f"判定={classification} | Agent 未创建 | 拒答长度={len(refusal)}"
        )
        return StreamingResponse(
            _detached_event_stream(
                _refusal_event_stream(session_id, repo, refusal),
                session_id,
                sid,
                renew_stream=repo.renew_stream,
                db=repo.database,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ── 3. 文件解析 + 系统提示词附加内容 ───────────────────────────────
    # 模型输入 = 技能指令前缀 + 用户正文；落库正文保持用户原话（见请求开头的技能解析）。
    # 旧前端消息文本里已有的 `/目录名` 保持原样，不重复拼。
    model_message = skill_library.inject_skill_directives(
        skill_library.pending_skill_directives(turn_skill_dirs, request.message),
        request.message,
    )
    parsed_content = model_message

    extra_parts: list[str] = []
    file_context_parts: list[tuple[str, str]] = []
    if request.files and not file_context.is_enabled():
        # 回退模式（config.file_context.enabled=false）：与 chat.py 一致，保持改造前
        # 行为——首轮把解析全文放进 system prompt，非首轮拼进用户消息。
        _existing = repo.get_session(session_id)
        is_new_session = (not _existing) or (len(_existing.messages) == 0)

        for file_info in request.files:
            file_path = file_info.get("file_path", "")
            if not file_path:
                continue
            filename = file_info.get("filename", "")
            content = parse_file_content(file_path)
            if content:
                file_context_parts.append((filename, content))
            else:
                logger.warning(f"[{sid}] 文件解析为空: {filename}")

        if file_context_parts:
            if is_new_session:
                sections = [f"### 文件: {fn}\n{c}" for fn, c in file_context_parts]
                extra_parts.append(
                    "## 用户上传的文件内容（已为你解析，请基于这些内容回应用户）\n\n"
                    + "\n\n".join(sections)
                )
            else:
                file_contents = [f"[文件: {fn}]\n{c}" for fn, c in file_context_parts]
                # 技能指令跟在正文前（与改造前前端拼接的顺序一致），文件块仍在最前
                parsed_content = "\n\n".join(file_contents) + "\n\n" + model_message
    elif request.files:
        # 新机制（默认）：解析正文与文件清单已在上传时落到会话工作区，清单作为记忆
        # 注入 system prompt（见 services/session_uploads.py / agent.py），这里只注入
        # 本轮上传文件的开头预览，减少"传完立刻提问"的一次工具往返。
        preview = file_context.build_turn_preview(
            session_id, username, request.files, repo
        )
        if preview:
            parsed_content = preview + "\n\n" + parsed_content
            logger.info(f"[{sid}] 📎 已注入本轮上传文件开头预览 | {len(preview)} 字符")

    # 专家提示词排在最后：agent.py 的拼接顺序为
    # [system_prompt, user_info, web_search_prompt, system_prompt_extra]，
    # 放在 extra 末尾即 recency 最高，专家人设可主导通用人设。
    if expert_prompt:
        extra_parts.append(expert_prompt)
    system_prompt_extra = "\n\n".join(extra_parts)

    # 可观测点 ②（前半）：交给 agent_manager 的动态附加内容已组装完成。
    # 这里能看到专家提示词 + 文件内容拼接后的最终 extra（未挂专家时为空）。
    logger.info(
        f"[{sid}] 🧩 动态附加内容已组装 | expert={expert_id or '(通用)'} | "
        f"文件块={len(file_context_parts)} | {describe_prompt('extra', system_prompt_extra)}"
    )

    # ── 4. 落用户消息（与 chat.py 一致） ───────────────────────────────
    _persist_user_turn(repo, session_id, username, request, expert_id, turn_skill_dirs)

    session_logger = _session_logger_for(session_id, username)
    session_logger.log_user_message(
        message=request.message,
        files=request.files,
        message_id=message_id,
    )

    # ── 5. 获取（必要时驱逐重建）会话 Agent ────────────────────────────
    # 专家变更的驱逐判断在 get_or_create_agent_for_session() 内部统一处理，
    # 这里只需把 expert_agent_id 传进去（不传时等价于通用 Agent，行为与改动前一致）。
    # 连接器不随每轮提交：agent_manager 按会话读取输入框「+」面板开关写入的集合。
    agent = await get_or_create_agent_for_session(
        session_id,
        username,
        workspace_name,
        model_name=request.model,
        system_prompt_extra=system_prompt_extra,
        enable_web_search=request.enable_web_search,
        expert_agent_id=expert_id,
        # save_expert 仅在 expert-creator 技能激活时注入（防止领域专家被借用来
        # 创建/修改其他专家）
        skill_dirs=turn_skill_dirs,
    )

    chat_request = ChatRequest(
        message=request.message,
        session_id=session_id,
        message_id=message_id,
        enable_deep_think=request.enable_deep_think,
        enable_web_search=request.enable_web_search,
        files=request.files,
        model=request.model,
    )

    return StreamingResponse(
        _detached_event_stream(
            chat_stream_generator(
                request=chat_request,
                db=repo.database,
                agent=agent,
                session_id=session_id,
                message_id=message_id,
                username=username,
                http_request=http_request,
                parsed_content=parsed_content,
                session_logger=session_logger,
            ),
            session_id,
            sid,
            renew_stream=repo.renew_stream,
            db=repo.database,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
