"""设置相关 API：记忆读写、系统提示词、Skills 列表、MCP 列表"""

import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..config import Config
from ..middleware import get_current_username
from ..skills import discover_skills
from ..services.mcp import load_mcp_config
from ..services import get_agent_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["Settings"])


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

    memory_file.write_text(request.content, encoding="utf-8")
    logger.info(f"记忆文件已更新 | 用户: {username} | 字符数: {len(request.content)}")
    return {"status": "ok"}


# ── 系统提示词 ────────────────────────────────────────────────────────

@router.get("/system-prompt", summary="获取系统提示词（只读）")
async def get_system_prompt(
    username: Annotated[str, Depends(get_current_username)],
):
    _cfg = get_agent_config()
    if _cfg and _cfg.get("system_prompt"):
        return {"content": _cfg["system_prompt"]}

    # fallback: 从文件读取
    config_path = Config.find_config_file("config.yaml")
    if config_path:
        config_dir = Path(config_path).parent
        sp_path = config_dir / "system_prompt.md"
    else:
        sp_path = Path(__file__).parent.parent / "config" / "system_prompt.md"

    if sp_path.exists():
        content = sp_path.read_text(encoding="utf-8")
        return {"content": content}

    return {"content": "你是一个有帮助的 AI 助手。"}


# ── Skills ────────────────────────────────────────────────────────────

@router.get("/skills", summary="获取所有可用的 Skills 列表")
async def get_skills(
    username: Annotated[str, Depends(get_current_username)],
):
    _cfg = get_agent_config()
    skills_root = _cfg.get("skills_root", "") if _cfg else ""
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

        result.append({
            "name": skill["name"],
            "description": description,
            "description_file": skill["description_file"],
        })

    return {"skills": result}


# ── MCP ───────────────────────────────────────────────────────────────

@router.get("/mcp", summary="获取所有 MCP 服务配置")
async def get_mcp_servers(
    username: Annotated[str, Depends(get_current_username)],
):
    config = load_mcp_config()

    servers = []
    for name, cfg in config.items():
        server_info = {
            "name": name,
            "transport": cfg.get("transport", cfg.get("type", "unknown")),
            "command": cfg.get("command", ""),
            "args": cfg.get("args", []),
        }
        # 不暴露 env 中的敏感信息（如密码）
        env_keys = list(cfg.get("env", {}).keys())
        server_info["env_keys"] = env_keys
        servers.append(server_info)

    return {"servers": servers}
