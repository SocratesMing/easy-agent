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
import time
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
from ..services import public_expert_service, skill_library
from ..services import session_uploads as file_context
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
from ..utils.prompt_probe import describe_prompt
from .chat import _detached_event_stream, create_new_session, generate_session_title

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Expert"])


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


def _ensure_name_available(
    store: ExpertStore,
    user_id: str,
    name_cn: str,
    *,
    exclude_agent_id: str = "",
    is_update: bool = False,
) -> None:
    """校验专家中文名在本用户下唯一（创建/修改共用）。

    重名会让同一个列表里出现两张看不出区别的卡片，因此在**落库/生成之前**就
    拦住。比较口径为「忽略空白 + 忽略大小写」；**已删除（软删除）的专家不参与
    校验**——它们已从列表移除，名字视为可复用，否则「删掉重建同名专家」会被
    莫名其妙地拦住。

    Args:
        exclude_agent_id: 修改场景传入自身 ID，避免「和自己重名」。
        is_update: 提示语区分创建与修改（修改时额外引导用户改名）。

    Raises:
        HTTPException: 409 —— 已存在同名专家（``detail`` 为可直接展示的提示语）。
    """
    conflict = store.find_by_name_cn(user_id, name_cn, exclude_agent_id=exclude_agent_id)
    if not conflict:
        return

    detail = "已有同名的专家Agent，请修改专家名称" if is_update else "已有同名的专家Agent"
    raise HTTPException(status_code=409, detail=detail)


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


def _load_expert_prompt(
    db: Database, username: str, expert_agent_id: str, sid: str
) -> tuple[str, str, str]:
    """解析一个专家 ID 对应的提示词（「我的专家」优先，「公共专家」兜底）。

    解析顺序故意如此：同一个 ID 若同时存在于用户库与公共目录，**以用户自己的为准**
    （用户可编辑的那份），避免预置内容无提示地覆盖用户改动。

    Args:
        expert_agent_id: 前端回传的专家 ID（我的专家 ID 或公共专家目录名）。

    Returns:
        ``(expert_id, prompt_text, source)``；``prompt_text`` 为空串表示读取失败
        （调用方降级为通用 Agent，不阻断对话）；``source`` 取 ``"mine"`` / ``"public"``
        / ``""``。

    Raises:
        HTTPException: 404 —— 我的专家与公共专家都找不到该 ID；
            400 —— 命中的是我的专家但状态不是「可用」（生成失败 / 已停用）。
    """
    if not expert_agent_id:
        return "", "", ""

    # ── 1. 我的专家（数据库归属校验） ──────────────────────────────────
    user_id = _current_user_id(db, username)
    record = ExpertStore(db).get_owned(expert_agent_id, user_id)
    if record is not None:
        if record.get("agent_status") != STATUS_ACTIVE:
            raise HTTPException(
                status_code=400, detail="专家已停用或生成失败，无法执行"
            )
        prompt_text, version, version_dir = load_active_prompt(username, expert_agent_id)
        if prompt_text.strip():
            logger.info(
                f"[{sid}] 📌 专家提示词已加载 | 来源=我的专家 | expert={expert_agent_id} | "
                f"v{version} | {describe_prompt('内容', prompt_text)} | dir={version_dir}"
            )
            return expert_agent_id, prompt_text, "mine"
        # 磁盘目录被清 / 文件缺失：降级为通用 Agent，不阻断用户对话
        logger.warning(
            f"[{sid}] 专家提示词读取失败，本轮降级为通用 Agent | 来源=我的专家 | "
            f"expert={expert_agent_id} | v{version} | {version_dir}"
        )
        return "", "", ""

    # ── 2. 公共（预置）专家：只读目录，不落库 ─────────────────────────
    public = public_expert_service.find_public_expert(expert_agent_id)
    if public is None:
        raise HTTPException(status_code=404, detail="专家不存在")

    prompt_text, version, prompt_path = public_expert_service.load_public_prompt(
        expert_agent_id
    )
    if prompt_text.strip():
        logger.info(
            f"[{sid}] 📌 专家提示词已加载 | 来源=公共专家 | expert={expert_agent_id} | "
            f"v{version} | {describe_prompt('内容', prompt_text)} | file={prompt_path}"
        )
        return expert_agent_id, prompt_text, "public"

    logger.warning(
        f"[{sid}] 专家提示词读取失败，本轮降级为通用 Agent | 来源=公共专家 | "
        f"expert={expert_agent_id} | v{version} | {prompt_path}"
    )
    return "", "", ""


def _expert_meta_for_message(
    db: Database, username: str, expert_id: str
) -> Optional[dict]:
    """取「随消息落库」的专家元信息（``{"id": ..., "name": ...}``）。

    用于回答面板的专家标识：把本轮专家写进用户消息随消息持久化，前端渲染 assistant
    消息时取其上一条用户消息的该字段展示「头像 + 名称」，因此**刷新 / 历史加载后不丢**，
    同一会话中不同轮次选了不同专家也能各自对应。

    「我的专家」（数据库）优先，「公共专家」（目录）兜底；两处都取不到中文名时返回
    ``None``（调用方不写该字段，前端按无专家兜底），不阻断本轮对话。
    """
    if not expert_id:
        return None

    name_cn = ""
    try:
        record = ExpertStore(db).get_owned(expert_id, _current_user_id(db, username))
        if record:
            name_cn = str(record.get("export_name_cn") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("专家名称读取失败（我的专家）| expert=%s | %s", expert_id, exc)

    if not name_cn:
        try:
            public = public_expert_service.find_public_expert(expert_id)
            if public:
                name_cn = str(public.get("export_name_cn") or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("专家名称读取失败（公共专家）| expert=%s | %s", expert_id, exc)

    if not name_cn:
        return None
    return {"id": expert_id, "name": name_cn}


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
    name_cn = request.name_cn.strip()
    store = ExpertStore(db)

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
        db=db,
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
        db=db,
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
    db: Annotated[Database, Depends(get_database)],
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
    t0 = time.perf_counter()
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
    # 正文可以为空：只挂技能 / 只上传文件时本来就没有正文（与 chat.py 一致）
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
            db, session_id, username, request.message, request.files, turn_fallback_name
        )
    else:
        session = db.get_session(session_id)
        if not session:
            session_id = str(uuid.uuid4())
            workspace_name = create_new_session(
                db, session_id, username, request.message, request.files, turn_fallback_name
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
                db.update_session(session)

    # ── 2. 专家校验与提示词加载 ────────────────────────────────────────
    # 我的专家（数据库）优先，公共专家（experts 目录）兜底；找不到 ID 时 404。
    # 加载日志（含长度/指纹/开头）在 _load_expert_prompt 内统一打印（可观测点 ①）。
    expert_prompt = ""
    expert_id = ""
    if request.expert_agent_id:
        expert_id, expert_prompt, _source = _load_expert_prompt(
            db, username, request.expert_agent_id, sid
        )
        if not expert_prompt:
            # 读取失败（目录被清 / 文件缺失）：降级为通用 Agent，不阻断对话
            expert_id = ""

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
        _existing = db.get_session(session_id)
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
            session_id, username, request.files, db
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
    # 本轮使用的专家随用户消息一起落库（回答面板标识的数据来源，见
    # _expert_meta_for_message）：刷新/历史加载后 assistant 消息仍能显示专家头像+名称，
    # 同一会话不同轮次各自对应自己那一轮的专家。
    expert_meta = _expert_meta_for_message(db, username, expert_id)
    user_message = {
        "role": "user",
        "content": request.message,
        "timestamp": datetime.now().isoformat(),
    }
    if expert_meta:
        user_message["expert"] = expert_meta
    if request.files:
        user_message["files"] = request.files
    # 结构化技能目录名：气泡按它渲染技能标签，不再需要从消息文本里反扒 `/目录名`
    if turn_skill_dirs:
        user_message["skills"] = turn_skill_dirs
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
    # 连接器不随每轮提交：agent_manager 按会话读取输入框「+」面板开关写入的集合。
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
