"""专家 Agent API：创建 / 修改 / 列表 / 删除。

设计要点（见 ``docs/expert-agent-solution.md`` §5、§6）：

- **鉴权**：全部 ``Depends(get_current_username)``，再用 ``user_id`` 做归属校验；
  非本人资源一律返回 **404**（不泄露存在性），与 ``chat.py`` 现有做法一致；
- **落库与目录**：``db/expert_store.py``（单表 CRUD）+ ``services/expert_service.py``
  （LLM 生成与版本目录），本文件只做编排与参数校验；
- **执行走唯一入口 ``/agent/chat/stream``**：专家对话不再是独立接口。前端在聊天请求里
  带上 ``expert_agent_id``，由 ``api/chat.py`` 的专家分支完成「加载提示词 → 补装依赖技能
  → 职责范围预检 → 建 Agent」，与通用对话共用同一套 SSE 事件与会话/run 生命周期。
  运行时逻辑在 ``services/expert_runtime.py``，本文件只保留 CRUD；
- **专家提示词走 ``system_prompt_extra``**：不改 ``agent.py``，专家人设通过现成通道
  追加到系统提示词末尾（recency 最高，实际效果上主导通用人设）。

批次 2B 起非路由辅助逻辑（用户/记录转换、配置读取、生成落库）移到
``api/expert_helpers.py``；2026-10 起对话运行时（提示词加载与护栏、消息落库、相关性
预检、短路拒答流）下沉到 ``services/expert_runtime.py``。旧内部名字经 ``__all__``
re-export 继续可用。
"""

import logging
import time
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from easy_agent.db.repositories.expert_repository import ExpertRepository
from easy_agent.db.expert_store import (
    STATUS_BUILDING,
)
from easy_agent.middleware import get_current_username
from easy_agent.services import (
    invalidate_user_agents,
)
from easy_agent.services import public_expert_service
from easy_agent.services.expert_service import (
    build_expert_ids,
    load_active_prompt_file,
    remove_expert_dir,
    validate_create_params,
)
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
    required_skills: Optional[list[str]] = Field(
        default=None,
        description="专家绑定的技能包目录名（用户在输入框选定的技能）；不传 = 不绑定（对话时技能库全量可见）",
    )


class ExpertUpdateRequest(BaseModel):
    """修改专家：重新生成配置并写入新版本目录。

    同 ``ExpertAddRequest``：``tag_id`` 不接收前端传参，修改后由大模型重新归类。
    """

    export_agent_id: str = Field(..., description="专家 Agent ID")
    name_cn: Optional[str] = Field(default=None, description="专家中文名（不传则沿用）")
    prompt: Optional[str] = Field(default=None, description="专家描述（不传则沿用）")
    modify_type: str = Field(default="edit", description="修改类型标识：edit=编辑")
    model: Optional[str] = Field(default=None, description="用于生成配置的模型")
    required_skills: Optional[list[str]] = Field(
        default=None,
        description="专家绑定的技能包目录名；不传 = 沿用原绑定（要清空传空数组）",
    )


class ExpertDelRequest(BaseModel):
    export_agent_id: str = Field(..., description="专家 Agent ID")
    hard: bool = Field(default=True, description="True=物理删除（含目录）；False=软删除")


# 说明：原 ``ExpertRunRequest`` 与 ``POST /agent/stream_export_run`` 已删除。
# 专家对话统一走 ``POST /agent/chat/stream``（带 ``expert_agent_id`` 字段），
# 不再有第二个 SSE 入口——两个入口会让会话/run 注册、拒答链路各写一套，极易分叉。


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

    # 专家绑定的技能包（用户选择）：归一成 canonical 目录名后落库；不传 = 不绑定
    from easy_agent.services import tool_scope

    selected_skills: list[str] = []
    if request.required_skills:
        selected_skills, dropped = tool_scope.normalize_expert_skills(
            username, request.required_skills, "add"
        )
        if dropped:
            logger.warning(
                "创建专家：%s 个技能包不存在，已忽略 | 用户=%s | %s",
                len(dropped),
                username,
                dropped,
            )

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
            "export_required_skills": selected_skills,
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
    if request.required_skills is not None:
        from easy_agent.services import tool_scope

        kept, dropped = tool_scope.normalize_expert_skills(
            username, request.required_skills, "update"
        )
        if dropped:
            logger.warning(
                "修改专家：%s 个技能包不存在，已忽略 | 用户=%s | 专家=%s | %s",
                len(dropped),
                username,
                request.export_agent_id,
                dropped,
            )
        meta["export_required_skills"] = kept
        logger.info(
            "修改专家：技能包已更新 | 用户=%s | 专家=%s | %s",
            username,
            request.export_agent_id,
            kept or "（清空 → 技能库全量可见）",
        )
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


@router.get(
    "/agent/expert-md",
    summary="读取专家提示词 MD（只读预览）",
)
def get_expert_md(
    db: Annotated[ExpertRepository, Depends(get_expert_repository)],
    username: Annotated[str, Depends(get_current_username)],
    expert_id: str = Query(..., description="专家 ID（我的专家或公共专家）"),
):
    """返回专家提示词 MD 的**文件名与正文**，供详情弹窗与工作区只读预览。

    只读接口：专家提示词由 ``save_expert`` 工具在对话里生成落盘，前端不提供
    编辑写入口。归属校验与 ``/agent/list`` 一致——本人记录或公共预置专家，
    其余一律 404（不泄露存在性）。

    ``my`` = 本人专家（``export_file_path`` 是版本目录，正文取 active 版本里的
    提示词文件）；``public`` = 公共专家（``export_file_path`` 就是 MD 本身）。
    """
    repo = _as_expert_repository(db)
    user_id = _current_user_id(repo, username)

    record = repo.get_owned(expert_id, user_id)
    if record is not None:
        file_name, content, version = load_active_prompt_file(username, expert_id)
        if not content:
            raise HTTPException(status_code=404, detail="该专家暂无可读的提示词文件")
        logger.info(
            "读取专家提示词 MD | user=%s | expert=%s | 文件=%s | 版本=v%s",
            username,
            expert_id,
            file_name,
            version,
        )
        return {
            "expert_agent_id": expert_id,
            "scope": "mine",
            "file_name": file_name,
            "content": content,
            "version": version,
        }

    public = public_expert_service.find_public_expert(expert_id)
    if public is None:
        raise HTTPException(status_code=404, detail="专家不存在")

    prompt_path = Path(str(public.get("export_file_path") or ""))
    try:
        content = prompt_path.read_text(encoding="utf-8") if prompt_path.is_file() else ""
    except OSError as exc:  # noqa: BLE001 - 读不到按 404 处理，不 500
        logger.warning("读取公共专家提示词 MD 失败 | %s | %s", prompt_path, exc)
        content = ""
    if not content:
        raise HTTPException(status_code=404, detail="该专家暂无可读的提示词文件")

    logger.info(
        "读取公共专家提示词 MD | user=%s | expert=%s | 文件=%s",
        username,
        expert_id,
        prompt_path.name,
    )
    return {
        "expert_agent_id": expert_id,
        "scope": "public",
        "file_name": prompt_path.name,
        "content": content,
        "version": int(public.get("export_version") or 1),
    }


@router.get(
    "/agent/session-experts",
    summary="列出某会话里创建的专家（工作区只读条目）",
)
def list_session_experts(
    db: Annotated[ExpertRepository, Depends(get_expert_repository)],
    username: Annotated[str, Depends(get_current_username)],
    session_id: str = Query(..., description="会话 ID"),
):
    """在创建该专家的会话工作区里展示其提示词 MD（只读）。

    专家记录创建时就带 ``session_id``，这里按会话反查本人创建的专家；前端把返回
    条目作为**虚拟文件**并入工作区文件树，点击走 ``/agent/expert-md`` 取正文。
    只读：不提供删除/编辑入口，专家本体仍由专家面板管理。
    """
    repo = _as_expert_repository(db)
    user_id = _current_user_id(repo, username)
    records = repo.list_by_session(user_id, session_id)
    items = []
    for r in records:
        expert_id = r.get("export_agent_id", "")
        # 真实文件名（active 版本里的提示词文件），取不到时前端按 name_en 兜底
        file_name, _content, version = load_active_prompt_file(username, expert_id)
        items.append(
            {
                "expert_agent_id": expert_id,
                "name_cn": r.get("export_name_cn", ""),
                "name_en": r.get("export_name_en", ""),
                "file_name": file_name,
                "version": version or int(r.get("export_version") or 1),
                "update_timestamp": r.get("update_timestamp", ""),
            }
        )
    logger.info(
        "会话内专家列表 | user=%s | session=%s | 数量=%s",
        username,
        session_id[-6:] if session_id else "-",
        len(items),
    )
    return {"items": items}


@router.get(
    "/agent/expert/required-skills",
    summary="查看专家依赖技能状态（排查用，只读）",
)
def expert_required_skills(
    username: Annotated[str, Depends(get_current_username)],
    expert_id: str = Query(..., description="公共专家 ID，如 preset_factor_research"),
):
    """返回该专家声明的依赖技能、解析到的公共技能目录、当前用户是否已可用。

    现场排查「选了这个专家，但模型说扫不到技能」时先看这里：``items[].state``
    直接给出原因——``source_missing`` 表示**服务端公共技能目录里没有这个技能包**
    （需要运维放包），``source_unconfigured`` 表示 ``tools.skills_dir`` 没配好，
    ``disabled`` 表示用户自己在技能中心停用了。

    声明值可以写目录名，也可以写 SKILL.md 的 ``name``（常见中文），两条通道等价：
    ``items[].declared`` 是原声明，``resolved_dir`` 是解析到的 canonical 目录名，
    ``matched_by`` 说明命中方式（``dir`` / ``dir_normalized`` / ``name`` /
    ``name_normalized``）；解析失败时 ``candidates`` 给出近似技能（**仅供参考，
    不会据此自动安装**）。

    本接口**只读**：不会触发安装，也不会驱逐缓存（触发补装的是真正发消息那一刻）。
    """
    from easy_agent.services import skill_provision

    report = skill_provision.required_skills_status(username, expert_id)
    if not report["required"]:
        raise HTTPException(
            status_code=404, detail=f"公共专家 '{expert_id}' 不存在或未声明依赖技能"
        )
    logger.info(
        "🔎 专家依赖技能状态查询 | 用户: %s | 专家: %s | 公共目录: %s | 状态: %s",
        username,
        expert_id,
        report["skills_root"] or "(未配置)",
        [(item["skill"], item["state"]) for item in report["items"]],
    )
    return report


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
