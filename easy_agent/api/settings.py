"""设置相关 API：记忆读写、系统提示词、Skills 列表、模型列表、联网搜索状态

MCP（连接器）相关接口已迁到 ``easy_agent/api/connectors.py``。
"""

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from easy_agent.bootstrap.config import Config
from easy_agent.middleware import get_current_username
from easy_agent.models.api import (
    ContentResponse,
    MemoryContentResponse,
    KnowledgeStatusResponse,
    MemoryUpdateResponse,
    ModelsResponse,
    SkillsResponse,
    UploadLimitsResponse,
    WebSearchStatusResponse,
)
from easy_agent.agent.skills import discover_skills
from easy_agent.agent.hitl import APPROVAL_MODES, auto_approved_tool_names
from easy_agent.services.agents.manager import get_agent_config
from easy_agent.services.approval_settings import (
    read_approval_settings,
    write_approval_settings,
)
from easy_agent.services.agents.prompts import load_system_prompt
from easy_agent.services.memory.manager import MAX_LONG_TERM_MEMORY_CHARS, write_memory
from easy_agent.utils.blocking import run_blocking
from easy_agent.tools.web_search import TIME_RANGE_VALUES, is_web_search_available, resolve_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent/settings", tags=["Settings"])


# ── 记忆 ──────────────────────────────────────────────────────────────


@router.get("/memory", summary="获取当前用户的记忆文件内容", response_model=MemoryContentResponse)
async def get_memory(username: Annotated[str, Depends(get_current_username)]):
    safe_name = Config.sanitize_username(username)
    _cfg = get_agent_config()
    if _cfg and _cfg.get("config"):
        memories_dir = Path(_cfg["config"].agent.memories_dir) / safe_name
    else:
        memories_dir = Path("./memories") / safe_name
    memory_file = memories_dir / "AGENTS.md"

    if not memory_file.exists():
        return {"content": f"# {username} 的长期记忆\n\n", "path": str(memory_file)}

    content = memory_file.read_text(encoding="utf-8")
    return {"content": content, "path": str(memory_file)}


class UpdateMemoryRequest(BaseModel):
    content: str


#: 与后台长期记忆更新的上限一致：这里放得更宽，下一轮更新就会把用户刚写的内容压缩掉
MEMORY_MAX_CHARS = MAX_LONG_TERM_MEMORY_CHARS


@router.put("/memory", summary="更新当前用户的记忆文件内容", response_model=MemoryUpdateResponse)
async def update_memory(
    request: UpdateMemoryRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    safe_name = Config.sanitize_username(username)
    _cfg = get_agent_config()
    if _cfg and _cfg.get("config"):
        memories_dir = Path(_cfg["config"].agent.memories_dir) / safe_name
    else:
        memories_dir = Path("./memories") / safe_name
    memories_dir.mkdir(parents=True, exist_ok=True)
    memory_file = memories_dir / "AGENTS.md"

    content = request.content
    if len(content) > MEMORY_MAX_CHARS:
        content = _truncate_memory(content, MEMORY_MAX_CHARS)
        logger.warning(
            f"记忆文件超出限制已截断 | 用户: {username} | 原始: {len(request.content)} | 截断后: {len(content)}"
        )

    # 与后台记忆更新走同一条写入路径：同一把锁 + 原子写 + 同步结构化 sidecar。
    # 后台更新若正在进行，写回前会发现文件已变，基于这份内容重做，不会把它覆盖掉。
    await run_blocking(write_memory, memory_file, content, raise_on_error=True)
    logger.info(f"记忆文件已更新 | 用户: {username} | 字符数: {len(content)}")
    return {"status": "ok", "chars": len(content)}


def _truncate_memory(content: str, max_chars: int) -> str:
    """截断记忆文件内容，保留标题和靠前的条目。

    策略：按行分割，从前往后保留尽可能多的完整行，直到接近 max_chars。
    这样优先保留最早写入的、通常更重要的长期偏好。
    """
    lines = content.split("\n")
    result_lines = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        if current_len + line_len > max_chars:
            break
        result_lines.append(line)
        current_len += line_len

    truncated = "\n".join(result_lines)
    if len(truncated) > max_chars:
        truncated = truncated[:max_chars]

    return truncated


# ── 权限设置（审批模式） ──────────────────────────────────────────────


class ApprovalSettingsResponse(BaseModel):
    """用户级审批模式。

    ``available=False`` 表示部署方在 config.yaml 里关了 ``hitl.enabled``：此时没有审批可免，
    前端应把两个选项置灰并展示 ``locked_reason``，而不是让用户白点一次保存。
    ``auto_approved_tools`` 由服务端下发（与 ``agent/hitl.py`` 的放行名单同源），
    避免前端文案与后端实际行为漂移。
    """

    mode: str
    available: bool = True
    locked_reason: str = ""
    auto_approved_tools: list[str] = []
    revision: int = 0
    updated_at: str = ""
    path: str = ""


class UpdateApprovalRequest(BaseModel):
    mode: str


def _current_agent_config():
    """运行期 Config；未初始化时返回 None（调用方按默认值处理，不抛 500）。"""
    _cfg = get_agent_config()
    return _cfg["config"] if _cfg and _cfg.get("config") else None


def _approval_availability(config) -> tuple[bool, str]:
    """部署方是否允许用户自选审批模式（``hitl.enabled=false`` 时用户选什么都没有意义）。"""
    if config is None:
        return True, ""
    enabled = bool(getattr(getattr(config, "hitl", None), "enabled", True))
    if enabled:
        return True, ""
    return False, "服务端已关闭人工审批（config.yaml 的 hitl.enabled=false），当前所有操作均不审批"


def _approval_payload(settings: dict, config) -> dict:
    available, reason = _approval_availability(config)
    hitl_tools = getattr(getattr(config, "hitl", None), "tools", None)
    return {
        "mode": settings.get("mode", "manual"),
        "available": available,
        "locked_reason": reason,
        "auto_approved_tools": auto_approved_tool_names(hitl_tools),
        "revision": settings.get("revision", 0),
        "updated_at": settings.get("updated_at", ""),
        "path": settings.get("path", ""),
    }


@router.get(
    "/approval",
    summary="获取当前用户的审批模式（设置 → 权限设置）",
    response_model=ApprovalSettingsResponse,
)
async def get_approval_settings(username: Annotated[str, Depends(get_current_username)]):
    """读 ``{workspace_dir}/.settings/{user}.json``；文件不存在/损坏时返回 ``manual``。

    每次打开设置页都直接读文件，前端不做本地缓存——换台设备改过设置这边也能立刻看到。
    """
    config = _current_agent_config()
    settings = await run_blocking(read_approval_settings, username, config)
    return _approval_payload(settings, config)


@router.put(
    "/approval",
    summary="更新当前用户的审批模式（设置 → 权限设置）",
    response_model=ApprovalSettingsResponse,
)
async def update_approval_settings(
    request: UpdateApprovalRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """原子写审批模式。**故意不驱逐本进程的 Agent 缓存。**

    生效靠两条既有机制，不靠这次驱逐：

    1. 每轮装配 Agent 前都会读一次文件（``services/agents/manager.py``），下一个正常
       提问就会带着新模式走到 ``_try_reuse_cached_agent`` 的 ``approval_changed`` 分支，
       自动驱逐并重建；
    2. 跨副本一致同样靠这次读文件，而不是广播。

    反过来，如果在这里调 ``invalidate_user_agents``，会把这个用户**所有会话**的 Agent
    与编译图一起清掉——包括「审批卡片正挂着」的会话。那种会话恢复时必须沿用原来的
    ``interrupt_on``（``prepare_resume`` 传 ``approval_mode=None`` 就是为了这个），
    缓存被清掉后会按新设置重建图，checkpointer 里的中断态对不上，点「继续」直接 409。
    """
    mode = str(request.mode or "").strip().lower()
    if mode not in APPROVAL_MODES:
        raise HTTPException(
            status_code=400,
            detail=f"非法审批模式: {request.mode}（可选 {' / '.join(APPROVAL_MODES)}）",
        )
    config = _current_agent_config()
    try:
        settings = await run_blocking(write_approval_settings, username, mode, config)
    except OSError as exc:
        logger.error(f"审批设置保存失败 | 用户: {username} | mode: {mode} | {exc}")
        raise HTTPException(status_code=500, detail=f"保存失败：{exc}") from exc

    logger.info(
        f"审批设置已更新 | 用户: {username} | mode: {mode} | "
        f"revision: {settings.get('revision')} | 下一条消息装配时生效 | "
        f"{settings.get('path')}"
    )
    return _approval_payload(settings, config)


# ── 系统提示词 ────────────────────────────────────────────────────────


@router.get("/system-prompt", summary="获取系统提示词（只读）", response_model=ContentResponse)
async def get_system_prompt(
    username: Annotated[str, Depends(get_current_username)],
):
    _cfg = get_agent_config()
    if _cfg and _cfg.get("system_prompt"):
        return {"content": _cfg["system_prompt"]}

    # fallback: 由 prompt_loader 从 prompt_path 目录读取（system.md + fragments）
    config_path = Config.resolve_config_path()
    content = load_system_prompt(config_dir=config_path.parent)
    return {"content": content}


# ── Skills ────────────────────────────────────────────────────────────


@router.get("/skills", summary="获取所有可用的 Skills 列表", response_model=SkillsResponse)
async def get_skills(
    username: Annotated[str, Depends(get_current_username)],
):
    _cfg = get_agent_config()
    skills_root = _cfg.get("tools", {}).get("skills_dir", "./skills") if _cfg else "./skills"
    skills = discover_skills(skills_root or None)

    result = []
    for skill in skills:
        skill_dir = Path(skill["path"])
        desc_file = skill_dir / skill["description_file"]
        description = ""
        if desc_file.exists():
            description = desc_file.read_text(encoding="utf-8")
            # 截断过长的描述
            if len(description) > 2000:
                description = description[:2000] + "..."

        result.append(
            {
                "name": skill["name"],
                "description": description,
                "description_file": skill["description_file"],
            }
        )

    return {"skills": result}


# ── 模型列表 ──────────────────────────────────────────────────────────


@router.get("/models", summary="获取所有可选模型列表（公共接口，无需登录）", response_model=ModelsResponse)
async def get_models():
    """返回 config.models 中的模型列表及当前激活模型。

    前端用于填充输入框的模型下拉，值使用模型 key（如 deepseek/glm）。
    与用户隔离无关，为公共接口。

    只返回**真正可用**的模型：配置里的 api_key 常写成 ``${XXX_API_KEY}``，
    若对应环境变量缺失，解析结果会是空字符串，这类模型选中后必然请求失败，
    因此直接从可选列表中过滤掉（激活模型一定有 key，不会被过滤）。
    """
    _cfg = get_agent_config()
    if not _cfg or not _cfg.get("config"):
        raise HTTPException(status_code=503, detail="Agent 配置未初始化")
    config: Config = _cfg["config"]

    models = []
    for name, prov in config.models.items():
        if not str(getattr(prov, "api_key", "") or "").strip():
            logger.info(f"模型 {name} 未配置 API Key，已从可选列表中隐藏")
            continue
        models.append({
            "name": name,
            "model": prov.model,
            "provider": prov.provider,
            "protocol": prov.protocol,
            "context_length": prov.context_length,
            "is_active": name == config.active_model,
        })

    logger.info(
        f"获取模型列表 | 可选: {[m['name'] for m in models]} | "
        f"active: {config.active_model}"
    )
    return {"models": models, "active_model": config.active_model}


# ── 联网搜索 ──────────────────────────────────────────────────────────


@router.get("/web-search", summary="获取联网搜索配置状态（公共接口，无需登录）", response_model=WebSearchStatusResponse)
async def get_web_search_status():
    """返回联网搜索是否可用，供前端决定输入框地球按钮是否可点击。

    与 /models 一致，只暴露「是否可用」与时间范围选项，**不返回** api_url / api_key。
    api_key 为空或功能关闭时 configured=false，前端按钮置灰并提示未配置。
    """
    _cfg = get_agent_config()
    if not _cfg or not _cfg.get("config"):
        raise HTTPException(status_code=503, detail="Agent 配置未初始化")
    config: Config = _cfg["config"]

    ws = getattr(config, "web_search", None)
    enabled = bool(getattr(ws, "enabled", False))
    configured = is_web_search_available(config)
    if configured:
        reason = ""
    elif not enabled:
        reason = "disabled"
    else:
        reason = "not_configured"

    logger.info(
        f"获取联网搜索状态 | enabled={enabled} | configured={configured} | reason={reason or '-'}"
    )
    return {
        "enabled": enabled,
        "configured": configured,
        "reason": reason,
        "provider": resolve_provider(config),
        "default_time_range": str(
            getattr(ws, "default_time_range", "NoLimit") or "NoLimit"
        ),
        "time_ranges": list(TIME_RANGE_VALUES),
    }


# ── 上传限额 ──────────────────────────────────────────────────────────


@router.get("/upload-limits", summary="获取上传限额（公共接口，无需登录）", response_model=UploadLimitsResponse)
async def get_upload_limits():
    """返回对话附件上传限额，供前端在选文件时即时拦截并展示已用额度。

    约束对象是「单个会话、单次对话」的附件（输入框待发送列表），发送后重置。
    与 /models、/web-search 一致为公共接口：这些阈值不敏感，且 SDK 内嵌场景
    需要一个不依赖登录态的取数通道。前端拉取失败时会回落到内置默认值
    （10MB / 5 个 / 20MB），不会因接口异常而放行。
    """
    _cfg = get_agent_config()
    if not _cfg or not _cfg.get("config"):
        raise HTTPException(status_code=503, detail="Agent 配置未初始化")
    config: Config = _cfg["config"]

    limits = getattr(config, "upload_limits", None)
    payload = {
        "enabled": bool(getattr(limits, "enabled", True)),
        "max_file_size_mb": int(getattr(limits, "max_file_size_mb", 10)),
        "max_files_per_message": int(getattr(limits, "max_files_per_message", 5)),
        "max_total_size_mb": int(getattr(limits, "max_total_size_mb", 20)),
    }
    logger.info(
        f"获取上传限额 | enabled={payload['enabled']} | "
        f"单文件 {payload['max_file_size_mb']}MB | "
        f"数量 {payload['max_files_per_message']} | "
        f"总量 {payload['max_total_size_mb']}MB"
    )
    return payload


# ── 知识库开关 ────────────────────────────────────────────────────────


@router.get(
    "/knowledge",
    summary="获取知识库是否启用（公共接口，无需登录）",
    response_model=KnowledgeStatusResponse,
)
async def get_knowledge_status(request: Request):
    """只返回 ``knowledge.enabled``，不暴露 Ragflow 地址和密钥。

    侧边栏据此决定是否显示「知识库」菜单。配置未注入时按未启用处理。
    """
    config = getattr(request.app.state, "knowledge_config", None)
    enabled = bool(getattr(config, "enabled", False))
    return {"enabled": enabled}

