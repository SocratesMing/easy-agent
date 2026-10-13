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
"""

import logging
import uuid
from datetime import datetime
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..db import Database, get_database
from ..db.expert_store import (
    STATUS_ACTIVE,
    STATUS_BUILDING,
    STATUS_FAILED,
    ExpertStore,
)
from ..middleware import get_current_username
from ..model import create_model
from ..models.api import ChatRequest
from ..services import (
    chat_stream_generator,
    get_agent_config,
    get_or_create_agent_for_session,
    invalidate_user_agents,
)
from ..services.expert_service import (
    build_expert_ids,
    generate_expert_config,
    load_active_prompt,
    remove_expert_dir,
    slugify,
    validate_create_params,
    write_expert_version,
)
from ..utils import SessionLogger, parse_file_content
from .chat import _detached_event_stream, create_new_session, generate_session_title

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Expert"])


# ── 请求模型 ──────────────────────────────────────────────────────────


class ExpertAddRequest(BaseModel):
    """创建专家。``type`` 为需求要求的前端组装标识（本期仅做校验）。"""

    name_cn: str = Field(..., description="专家中文名（必填）")
    prompt: str = Field(..., description="用户对专家的描述 / 提示词（必填）")
    type: str = Field(default="agent", description="组装标识：agent=创建专家")
    tag_id: str = Field(default="", description="分类标签（选填；留空则由大模型按 expert_tags 自动归类）")
    session_id: Optional[str] = Field(default=None, description="创建该专家的会话（溯源用）")
    model: Optional[str] = Field(default=None, description="用于生成配置的模型（config.models 的 key）")


class ExpertUpdateRequest(BaseModel):
    """修改专家：重新生成配置并写入新版本目录。"""

    export_agent_id: str = Field(..., description="专家 Agent ID")
    name_cn: Optional[str] = Field(default=None, description="专家中文名（不传则沿用）")
    prompt: Optional[str] = Field(default=None, description="专家描述（不传则沿用）")
    tag_id: Optional[str] = Field(default=None, description="分类标签")
    modify_type: str = Field(default="edit", description="修改类型标识：edit=编辑")
    model: Optional[str] = Field(default=None, description="用于生成配置的模型")


class ExpertDelRequest(BaseModel):
    export_agent_id: str = Field(..., description="专家 Agent ID")
    hard: bool = Field(default=False, description="True=物理删除（含目录）；False=软删除")


class ExpertRunRequest(BaseModel):
    """选中专家执行（SSE 流式），字段与 ``/agent/chat/stream`` 对齐。"""

    message: str = Field(..., description="用户消息内容", min_length=1)
    session_id: Optional[str] = Field(default=None, description="会话ID；不传则新建会话")
    message_id: Optional[str] = Field(default=None, description="消息ID")
    expert_agent_id: Optional[str] = Field(
        default=None, description="选中的专家 Agent ID；不传=通用 Agent"
    )
    model: Optional[str] = Field(default=None, description="模型名称")
    enable_deep_think: bool = Field(default=False, description="是否启用深度思考")
    enable_web_search: bool = Field(default=False, description="是否开启联网搜索")
    files: Optional[List[dict]] = Field(default=None, description="上传的文件列表")


# ── 辅助 ──────────────────────────────────────────────────────────────


def _current_user_id(db: Database, username: str) -> str:
    """取当前用户的 ``user_id``（专家表的归属键）。

    用户记录缺失时回退为用户名，保证「归属校验」在同一口径下自洽，
    不会因为一个查不到的用户而让整个接口 500。
    """
    try:
        user = db.get_user_by_username(username)
        if user and getattr(user, "user_id", ""):
            return user.user_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取用户 user_id 失败，回退为用户名: %s", exc)
    return username


def _to_public(record: dict) -> dict:
    """把库记录转成对外的专家信息（隐藏无关列，列表型字段已经是数组）。"""
    if not record:
        return {}
    return {
        "export_agent_id": record.get("export_agent_id", ""),
        "session_id": record.get("session_id", ""),
        "tag_id": record.get("tag_id", ""),
        "export_name_en": record.get("export_name_en", ""),
        "export_name_cn": record.get("export_name_cn", ""),
        "export_prompt": record.get("export_prompt", ""),
        "export_description": record.get("export_description", ""),
        "export_skills_names": record.get("export_skills_names", []),
        "export_features_prompt": record.get("export_features_prompt", []),
        "export_file_path": record.get("export_file_path", ""),
        "agent_status": record.get("agent_status", ""),
        "export_version": record.get("export_version", 1),
        "create_timestamp": record.get("create_timestamp", ""),
        "update_timestamp": record.get("update_timestamp", ""),
    }


def _get_config():
    _cfg = get_agent_config()
    if not _cfg or not _cfg.get("config"):
        raise HTTPException(status_code=503, detail="Agent 配置未初始化，服务不可用")
    return _cfg["config"]


def _tag_options(config) -> list[dict]:
    """取配置里的专家分类标签候选（``expert_tags``）。

    兼容 Pydantic 模型与裸 dict 两种形态（测试里可能注入 dict），统一成
    ``[{"tag_id": ..., "tag_name": ...}]``；配置缺失时返回空列表。
    """
    raw = getattr(config, "expert_tags", None) or []
    options: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            tag_id = str(item.get("tag_id") or "").strip()
            tag_name = str(item.get("tag_name") or "").strip()
        else:
            tag_id = str(getattr(item, "tag_id", "") or "").strip()
            tag_name = str(getattr(item, "tag_name", "") or "").strip()
        if tag_id and tag_name:
            options.append({"tag_id": tag_id, "tag_name": tag_name})
    return options


async def _generate_and_store(
    *,
    db: Database,
    username: str,
    expert_agent_id: str,
    name_cn: str,
    user_prompt: str,
    version: int,
    model_name: Optional[str],
    session_id: Optional[str],
) -> dict:
    """调 LLM 生成配置 -> 写版本目录 -> 刷新库记录。返回启用后的完整记录。

    生成内容包含卡片/详情页展示用的 ``export_description``（≤300 字）与
    ``export_skills_names``（3 个技能标签），以及大模型自动归类的 ``tag_id``
    （候选来自配置文件的 ``expert_tags``，不在候选内的会被清空后回退原值）。

    失败时把 ``agent_status`` 置为 ``failed``（前端可重试），并抛出可读错误。
    """
    config = _get_config()
    store = ExpertStore(db)
    tag_options = _tag_options(config)
    try:
        llm = create_model(config, model_name, session_id)
        generated = await generate_expert_config(
            llm, name_cn, user_prompt, tag_options=tag_options
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("专家配置生成失败 | expert=%s | %s", expert_agent_id, exc)
        store.update_fields(expert_agent_id, {"agent_status": STATUS_FAILED})
        raise HTTPException(status_code=500, detail=f"专家配置生成失败：{exc}") from exc

    # 英文标识名：优先用 LLM 生成的 name_en（如 stock-invest-advisor），
    # 缺失/非法时回退为创建时的占位 slug。文件名不含随机数字（需求 §1）。
    name_en = slugify(generated.get("name_en") or "", max_len=48)

    try:
        file_path = write_expert_version(
            username,
            expert_agent_id,
            version,
            name_cn,
            generated,
            name_en=name_en,
            model_name=model_name or getattr(config, "active_model", ""),
            raw_output=generated,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("专家版本目录写入失败 | expert=%s | %s", expert_agent_id, exc)
        store.update_fields(expert_agent_id, {"agent_status": STATUS_FAILED})
        raise HTTPException(status_code=500, detail=f"专家配置落盘失败：{exc}") from exc

    # 大模型自动归类：生成的 tag_id 有效时覆盖创建时传入的值；
    # 无效/缺失（模型没选或选了候选之外的标签）时保留原值不动。
    generated_tag = generated.get("tag_id") or ""
    current_tag = (store.get(expert_agent_id) or {}).get("tag_id", "")

    fields = {
        "export_file_path": file_path,
        "export_features_prompt": generated.get("features_prompt", []),
        # 卡片/详情页展示字段
        "export_description": generated.get("description", ""),
        "export_skills_names": generated.get("skills_names", []),
        "tag_id": generated_tag or current_tag or "",
        "agent_status": STATUS_ACTIVE,
        "export_version": int(version),
    }
    if name_en:
        # LLM 生成了更贴切的英文标识名，回写覆盖创建时的占位 slug
        fields["export_name_en"] = name_en
    store.update_fields(expert_agent_id, fields)
    return store.get(expert_agent_id) or {}


# ── 接口 ──────────────────────────────────────────────────────────────


@router.post("/agent/add", summary="创建专家 Agent")
async def add_expert(
    request: ExpertAddRequest,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    if (request.type or "agent").lower() != "agent":
        raise HTTPException(status_code=400, detail="type 仅支持 agent（本期不含技能模块）")
    try:
        validate_create_params(request.name_cn, request.prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    user_id = _current_user_id(db, username)
    expert_agent_id, name_en = build_expert_ids(request.name_cn)
    store = ExpertStore(db)

    # 先落 building 占位行：即使生成途中进程崩溃，也能查到一条记录供前端重试
    store.create(
        {
            "export_agent_id": expert_agent_id,
            "session_id": request.session_id or "",
            "user_id": user_id,
            "tag_id": request.tag_id or "",
            "export_name_en": name_en,
            "export_name_cn": request.name_cn.strip(),
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
        db=db,
        username=username,
        expert_agent_id=expert_agent_id,
        name_cn=request.name_cn.strip(),
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
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    user_id = _current_user_id(db, username)
    store = ExpertStore(db)
    record = store.get_owned(request.export_agent_id, user_id)
    if record is None:
        raise HTTPException(status_code=404, detail="专家不存在")

    name_cn = (request.name_cn or record.get("export_name_cn") or "").strip()
    user_prompt = request.prompt if request.prompt is not None else record.get("export_prompt", "")
    try:
        validate_create_params(name_cn, user_prompt)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ★ 新版本目录 v{n+1}，不覆盖 v{n}：防止运行中的旧会话读到半成品配置
    new_version = int(record.get("export_version") or 1) + 1
    await _generate_and_store(
        db=db,
        username=username,
        expert_agent_id=request.export_agent_id,
        name_cn=name_cn,
        user_prompt=user_prompt,
        version=new_version,
        model_name=request.model,
        session_id=record.get("session_id") or None,
    )
    # 元数据（中文名/描述）随本次修改一起刷新；tag_id 走大模型自动归类，
    # 仅在调用方**显式**传入非空 tag_id 时才人工覆盖自动结果。
    meta = {"export_name_cn": name_cn, "export_prompt": user_prompt}
    if request.tag_id:
        meta["tag_id"] = request.tag_id
    store.update_fields(request.export_agent_id, meta)

    # 让该用户所有缓存 Agent 失效，下次请求用新版本的提示词重建
    invalidate_user_agents(username)

    updated = store.get_owned(request.export_agent_id, user_id) or {}
    logger.info("专家修改成功 | %s | v%s", request.export_agent_id, new_version)
    return _to_public(updated)


@router.get("/agent/list", summary="获取专家 Agent 列表")
async def list_experts(
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
    keyword: str = Query("", description="按中英文名模糊搜索"),
    status: str = Query("", description="按状态过滤：active/failed/disabled"),
    include_disabled: bool = Query(False, description="是否包含已删除（软删）的专家"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(10, ge=1, le=20, description="每页条数"),
):
    user_id = _current_user_id(db, username)
    store = ExpertStore(db)
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
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "tags": tags,
        "items": [_to_public(r) for r in items],
    }


@router.post("/agent/del", summary="删除专家 Agent")
async def delete_expert(
    request: ExpertDelRequest,
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
):
    user_id = _current_user_id(db, username)
    store = ExpertStore(db)
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
    db: Annotated[Database, Depends(get_database)],
    http_request: Request,
    username: Annotated[str, Depends(get_current_username)],
):
    if not request.message:
        raise HTTPException(status_code=400, detail="消息内容不能为空")

    session_id = request.session_id
    message_id = request.message_id or str(uuid.uuid4())
    sid = session_id[-5:] if session_id else "new"

    logger.info(
        f"[{sid}] 专家执行请求 | expert: {request.expert_agent_id or '(通用)'} | "
        f"message: {request.message[:50]}{'...' if len(request.message) > 50 else ''}"
    )

    # ── 1. 会话处理：与 /agent/chat/stream 保持一致 ────────────────────
    if session_id is None:
        session_id = str(uuid.uuid4())
        workspace_name = create_new_session(
            db, session_id, username, request.message, request.files
        )
    else:
        session = db.get_session(session_id)
        if not session:
            session_id = str(uuid.uuid4())
            workspace_name = create_new_session(
                db, session_id, username, request.message, request.files
            )
        elif (session.username or "") != (username or ""):
            raise HTTPException(status_code=404, detail="会话不存在")
        else:
            workspace_name = session.workspace_name or ""
            if len(session.messages) == 0:
                session.title = generate_session_title(request.message, request.files)
                session.updated_at = datetime.now().isoformat()
                db.update_session(session)

    # ── 2. 专家校验与提示词加载 ────────────────────────────────────────
    expert_prompt = ""
    expert_id = ""
    if request.expert_agent_id:
        user_id = _current_user_id(db, username)
        record = ExpertStore(db).get_owned(request.expert_agent_id, user_id)
        if record is None:
            raise HTTPException(status_code=404, detail="专家不存在")
        if record.get("agent_status") != STATUS_ACTIVE:
            raise HTTPException(
                status_code=400, detail="专家已停用或生成失败，无法执行"
            )

        prompt_text, version, version_dir = load_active_prompt(
            username, request.expert_agent_id
        )
        if prompt_text.strip():
            expert_prompt = prompt_text
            expert_id = request.expert_agent_id
            logger.info(
                f"[{sid}] 已加载专家提示词 | expert={request.expert_agent_id} | "
                f"v{version} | {len(prompt_text)} 字符 | {version_dir}"
            )
        else:
            # 磁盘目录被清 / 文件缺失：降级为通用 Agent，不阻断用户对话
            logger.warning(
                f"[{sid}] 专家提示词读取失败，本轮降级为通用 Agent | "
                f"expert={request.expert_agent_id} | v{version} | {version_dir}"
            )

    # ── 3. 文件解析 + 系统提示词附加内容 ───────────────────────────────
    parsed_content = request.message
    _existing = db.get_session(session_id)
    is_new_session = (not _existing) or (len(_existing.messages) == 0)

    extra_parts: list[str] = []
    file_context_parts: list[tuple[str, str]] = []
    if request.files:
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
            parsed_content = "\n\n".join(file_contents) + "\n\n" + request.message

    # 专家提示词排在最后：agent.py 的拼接顺序为
    # [system_prompt, user_info, web_search_prompt, system_prompt_extra]，
    # 放在 extra 末尾即 recency 最高，专家人设可主导通用人设。
    if expert_prompt:
        extra_parts.append(expert_prompt)
    system_prompt_extra = "\n\n".join(extra_parts)

    # ── 4. 落用户消息（与 chat.py 一致） ───────────────────────────────
    user_message = {
        "role": "user",
        "content": request.message,
        "timestamp": datetime.now().isoformat(),
    }
    if request.files:
        user_message["files"] = request.files
    db.add_message(session_id, user_message)
    db.add_message_row(session_id, user_message)

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

    # ── 5. 获取（必要时驱逐重建）会话 Agent ────────────────────────────
    # 专家变更的驱逐判断在 get_or_create_agent_for_session() 内部统一处理，
    # 这里只需把 expert_agent_id 传进去（不传时等价于通用 Agent，行为与改动前一致）。
    agent = await get_or_create_agent_for_session(
        session_id,
        username,
        workspace_name,
        model_name=request.model,
        system_prompt_extra=system_prompt_extra,
        enable_web_search=request.enable_web_search,
        expert_agent_id=expert_id,
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
                db=db,
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
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
