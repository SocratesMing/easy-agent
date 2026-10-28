"""专家 Agent API：创建 / 修改 / 列表 / 删除。

设计要点（见 ``docs/expert-agent-solution.md`` §5、§6）：

- **鉴权**：全部 ``Depends(get_current_username)``，再用 ``user_id`` 做归属校验；
  非本人资源一律返回 **404**（不泄露存在性），与 ``chat.py`` 现有做法一致；
- **落库与目录**：``db/expert_store.py``（单表 CRUD）+ ``services/expert_service.py``
  （LLM 生成与版本目录），本文件只做编排与参数校验；
- **执行不在本文件**：专家对话已并入通用入口 ``/agent/chat/stream``（请求体带
  ``expert_agent_id``），运行时逻辑（提示词加载 / 职责边界 / 相关性预检 / 拒答流）
  统一由 ``services/expert_runtime.py`` 提供，本文件不再有独立执行路由；
- **专家提示词走 ``system_prompt_extra``**：专家人设通过现成通道追加到系统提示词
  末尾（recency 最高，实际效果上主导通用人设），不改 ``agent.py``。
"""

import logging
import time
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
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
from ..services import get_agent_config, invalidate_user_agents
from ..services import public_expert_service
from ..services.expert_runtime import current_user_id as _current_user_id
from ..services.expert_service import (
    build_expert_ids,
    generate_expert_config,
    load_active_prompt_file,
    remove_expert_dir,
    slugify,
    validate_create_params,
    write_expert_version,
)

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
    hard: bool = Field(default=True, description="True=物理删除（含目录）；False=软删除")


# ── 辅助 ──────────────────────────────────────────────────────────────


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


@router.get("/agent/expert/required-skills", summary="查看专家依赖技能的安装状态（排查用）")
def expert_required_skills_status(
    username: Annotated[str, Depends(get_current_username)],
    expert_id: str = Query(..., description="公共专家 ID，如 preset_factor_research"),
):
    """返回该专家声明的依赖技能、解析到的公共技能目录、是否已装到当前用户。

    现场排查「选了这个专家，但模型说扫不到技能」时先看这里：
    ``skipped[].reason`` 会直接给出原因——``source_missing`` 表示**服务端公共技能
    目录里没有这个技能包**（需要运维放包），``disabled`` 表示用户自己停用了。
    只读，不会触发安装以外的副作用（缺技能时仍会尝试装一次，装成功即返回 installed）。

    ``required`` 里是**声明值**（可能是目录名，也可能是 SKILL.md 的中文 name）；
    ``resolved`` 逐条给出 ``declared`` / ``resolved_dir`` / ``matched_by``
    （``dir`` / ``dir_normalized`` / ``name`` / ``name_normalized`` / ``alias``），
    ``installed`` 与 ``skipped[].resolved_dir`` 一律是 canonical 目录名；
    解析失败时 ``skipped[].candidates`` 给出名称相近的技能，便于区分「声明写错」
    还是「技能包没放」。
    """
    from ..services.skill_provision import provision_expert_skills

    report = provision_expert_skills(username, expert_id)
    if not report.get("required"):
        raise HTTPException(status_code=404, detail=f"公共专家 '{expert_id}' 不存在或未声明依赖技能")
    return report


@router.get(
    "/agent/expert-md",
    summary="读取专家提示词 MD（只读预览）",
)
def get_expert_md(
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
    expert_id: str = Query(..., description="专家 ID（我的专家或公共专家）"),
):
    """返回专家提示词 MD 的**文件名与正文**，供详情弹窗与工作区只读预览。

    只读接口：专家提示词由 ``save_expert`` 工具在对话里生成落盘，前端不提供
    编辑写入口。归属校验与 ``/agent/list`` 一致——本人记录或公共预置专家，
    其余一律 404（不泄露存在性）。

    ``mine`` = 本人专家（``export_file_path`` 是版本目录，正文取 active 版本里的
    提示词文件）；``public`` = 公共专家（``export_file_path`` 就是 MD 本身）。
    """
    user_id = _current_user_id(db, username)
    store = ExpertStore(db)

    record = store.get_owned(expert_id, user_id)
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
    db: Annotated[Database, Depends(get_database)],
    username: Annotated[str, Depends(get_current_username)],
    session_id: str = Query(..., description="会话 ID"),
):
    """列出在该会话中创建的专家，用于工作区文件树的「专家设定 MD」条目。

    专家 MD 存在 ``{workspace}/{user}/experts/{id}/v{n}/``，不在会话目录里，
    因此工作区文件树（只扫会话目录）看不到它。前端把这些记录作为**虚拟文件**
    条目并入工作区文件树，点击走 ``/agent/expert-md`` 取正文。
    只读：不提供删除/编辑入口，专家本体仍由专家面板管理。
    """
    user_id = _current_user_id(db, username)
    records = ExpertStore(db).list_by_session(user_id, session_id)
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
