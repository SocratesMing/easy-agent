"""设置相关 API：记忆读写、系统提示词、Skills 列表、模型列表、联网搜索状态

MCP（连接器）相关接口已迁到 ``easy_agent/api/connectors.py``。
"""

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..config import Config
from ..middleware import get_current_username
from ..skills import discover_skills
from ..services import get_agent_config
from ..services.prompt_loader import load_system_prompt
from ..tools.web_search import TIME_RANGE_VALUES, is_web_search_available, resolve_provider

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agent/settings", tags=["Settings"])


# ── 记忆 ──────────────────────────────────────────────────────────────


@router.get("/memory", summary="获取当前用户的记忆文件内容")
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


MEMORY_MAX_CHARS = 2000


@router.put("/memory", summary="更新当前用户的记忆文件内容")
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

    memory_file.write_text(content, encoding="utf-8")
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


# ── 系统提示词 ────────────────────────────────────────────────────────


@router.get("/system-prompt", summary="获取系统提示词（只读）")
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


@router.get("/skills", summary="获取所有可用的 Skills 列表")
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


@router.get("/models", summary="获取所有可选模型列表（公共接口，无需登录）")
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


@router.get("/web-search", summary="获取联网搜索配置状态（公共接口，无需登录）")
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


@router.get("/upload-limits", summary="获取上传限额（公共接口，无需登录）")
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

