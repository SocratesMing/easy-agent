"""设置相关 API：记忆读写、系统提示词、Skills 列表、MCP 列表、模型列表"""

import json
import logging
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from langchain_mcp_adapters.client import MultiServerMCPClient

from ..config import Config
from ..middleware import get_current_username
from ..skills import discover_skills
from ..services.mcp import (
    load_mcp_config,
    invalidate_mcp_cache,
    get_mcp_tools,
    validate_mcp_servers,
)
from ..services import get_agent_config, invalidate_user_agents

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

        result.append(
            {
                "name": skill["name"],
                "description": description,
                "description_file": skill["description_file"],
            }
        )

    return {"skills": result}


# ── 模型列表 ──────────────────────────────────────────────────────────


@router.get("/models", summary="获取所有可选模型列表")
async def get_models(
    username: Annotated[str, Depends(get_current_username)],
):
    """返回 config.models 中的模型列表及当前激活模型。

    前端用于填充输入框的模型下拉，值使用模型 key（如 deepseek/glm）。
    """
    _cfg = get_agent_config()
    if not _cfg or not _cfg.get("config"):
        raise HTTPException(status_code=503, detail="Agent 配置未初始化")
    config: Config = _cfg["config"]

    models = []
    for name, prov in config.models.items():
        models.append({
            "name": name,
            "model": prov.model,
            "provider": prov.provider,
            "protocol": prov.protocol,
            "max_input_tokens": prov.max_input_tokens,
            "is_active": name == config.active_model,
        })

    logger.info(
        f"获取模型列表 | 用户: {username} | 可选: {[m['name'] for m in models]} | "
        f"active: {config.active_model}"
    )
    return {"models": models, "active_model": config.active_model}


# ── MCP ───────────────────────────────────────────────────────────────


@router.get("/mcp", summary="获取当前用户的 MCP 服务配置")
async def get_mcp_servers(
    username: Annotated[str, Depends(get_current_username)],
):
    """读取用户专属 mcp.json（不存在则回退全局）。

    不暴露 env 中的敏感值，仅返回 key 列表。
    """
    config = load_mcp_config(username)

    servers = []
    for name, cfg in config.items():
        # 保留完整原始配置供前端编辑（env 值脱敏）
        raw = dict(cfg)
        if "env" in raw and isinstance(raw["env"], dict):
            raw["env"] = {k: "***" for k in raw["env"]}

        server_info = {
            "name": name,
            "transport": cfg.get("transport", cfg.get("type", "unknown")),
            "command": cfg.get("command", ""),
            "args": cfg.get("args", []),
            "_raw": raw,
        }
        # 不暴露 env 中的敏感信息（如密码），仅返回 key 列表
        env_keys = list(cfg.get("env", {}).keys())
        server_info["env_keys"] = env_keys
        servers.append(server_info)

    # 标注来源：用户专属文件是否存在
    user_mcp_path = Config.get_user_mcp_path(username)
    logger.info(
        f"获取 MCP 配置 | 用户: {username} | 来源: "
        f"{'user' if user_mcp_path.exists() else 'global'} | servers: {len(servers)}"
    )
    return {
        "servers": servers,
        "source": "user" if user_mcp_path.exists() else "global",
        "user_mcp_path": str(user_mcp_path),
    }


class UpdateMcpRequest(BaseModel):
    servers: dict[str, dict[str, Any]]


@router.put("/mcp", summary="更新当前用户的 MCP 服务配置")
async def update_mcp_servers(
    request: UpdateMcpRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """写入用户专属 mcp.json，并失效该用户的 MCP 缓存与缓存 Agent。

    下次聊天请求会重建 Agent 并加载新的 MCP 工具，实现动态加载。
    """
    _cfg = get_agent_config()
    config: Config = _cfg["config"] if _cfg and _cfg.get("config") else None
    user_mcp_path = Config.get_user_mcp_path(username, config)
    user_mcp_path.parent.mkdir(parents=True, exist_ok=True)

    payload = {"servers": request.servers}
    user_mcp_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    logger.info(
        f"写入用户 MCP 配置 | 用户: {username} | 路径: {user_mcp_path} | "
        f"servers: {list(request.servers.keys())}"
    )

    # 失效 MCP 工具缓存（按用户路径）
    invalidate_mcp_cache(username)

    # 失效该用户已缓存的 Agent，使下次请求重建并加载新 MCP
    evicted = invalidate_user_agents(username)
    logger.info(
        f"用户 MCP 更新完成 | 用户: {username} | 失效 Agent: {evicted} 个"
    )

    # 逐个校验 server 状态：异常的 server 前端会自动关闭开关并提示
    server_status = []
    try:
        server_status = await validate_mcp_servers(username)
        ok_count = sum(1 for s in server_status if s["status"] == "ok")
        err_count = len(server_status) - ok_count
        logger.info(
            f"用户 MCP 校验 | 用户: {username} | "
            f"成功: {ok_count} | 失败: {err_count} | 详情: {server_status}"
        )
    except Exception as e:
        logger.warning(f"用户 MCP 校验失败 | 用户: {username} | {e}")

    return {
        "status": "ok",
        "path": str(user_mcp_path),
        "servers": list(request.servers.keys()),
        "agents_invalidated": evicted,
        "server_status": server_status,
    }


# ── MCP 单条添加 / 删除（即时同步后端） ────────────────────────────────


def _read_user_mcp_raw(username: str, config: Config | None) -> dict[str, Any]:
    """读取用户专属 mcp.json 原始内容，返回 {"servers": {name: cfg}}。

    文件不存在或格式异常时返回空 servers。
    """
    user_mcp_path = Config.get_user_mcp_path(username, config)
    if not user_mcp_path.exists():
        return {"servers": {}}
    try:
        data = json.loads(user_mcp_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("servers"), dict):
            return data
    except Exception as e:
        logger.warning(f"读取用户 mcp.json 失败 | 用户: {username} | {e}")
    return {"servers": {}}


def _write_user_mcp_raw(username: str, config: Config | None, payload: dict[str, Any]) -> Path:
    """写入用户专属 mcp.json，返回文件路径。"""
    user_mcp_path = Config.get_user_mcp_path(username, config)
    user_mcp_path.parent.mkdir(parents=True, exist_ok=True)
    user_mcp_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return user_mcp_path


def _extract_servers(user_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """从用户粘贴的 JSON 中提取 {name: cfg} 映射。

    支持三种格式（以 mcp.json 的 servers 结构为基准）：
      1. {"servers": {"mysql": {...}}}          - 完整 mcp.json 片段
      2. {"mysql": {...}}                        - 对象格式
      3. {"name": "mysql", "transport": ...}     - 单条 server
    """
    # 格式1: {"servers": {"mysql": {...}}}
    servers = user_config.get("servers")
    if isinstance(servers, dict):
        return {k: v for k, v in servers.items() if isinstance(v, dict)}
    # 格式3: {"name": "mysql", ...}
    if "name" in user_config and isinstance(user_config["name"], str):
        name = user_config["name"]
        cfg = {k: v for k, v in user_config.items() if k != "name"}
        return {name: cfg}
    # 格式2: {"mysql": {...}}
    return {k: v for k, v in user_config.items() if isinstance(v, dict)}


class AddMcpServerRequest(BaseModel):
    config: dict[str, Any]


@router.post("/mcp/server", summary="添加 MCP 服务（按 servers 下的名称判重，即时生效）")
async def add_mcp_server(
    request: AddMcpServerRequest,
    username: Annotated[str, Depends(get_current_username)],
):
    """添加一个或多个 MCP server。

    用户粘贴的 JSON 会被解析，以 servers 下的 key 作为 MCP 名称进行判重：
    若名称已存在则返回 409（不写入）。全部新增成功后立即失效缓存与 Agent，
    并逐个校验新添加的 server，异常的在前端提示。
    """
    _cfg = get_agent_config()
    config: Config | None = _cfg["config"] if _cfg and _cfg.get("config") else None

    new_servers = _extract_servers(request.config)
    if not new_servers:
        raise HTTPException(status_code=400, detail="未找到有效的 server 配置，请检查 JSON 格式")

    raw = _read_user_mcp_raw(username, config)
    existing: dict[str, Any] = raw.get("servers", {})

    # 判重：以 servers 下的名称识别
    duplicates = [n for n in new_servers if n in existing]
    if duplicates:
        raise HTTPException(
            status_code=409,
            detail=f"MCP 服务已存在: {', '.join(duplicates)}（请先删除或改名）",
        )

    # 合并写入
    existing.update(new_servers)
    raw["servers"] = existing
    user_mcp_path = _write_user_mcp_raw(username, config, raw)
    logger.info(
        f"添加 MCP | 用户: {username} | 新增: {list(new_servers.keys())} | "
        f"总数: {len(existing)} | 路径: {user_mcp_path}"
    )

    # 失效 MCP 工具缓存 + 该用户已缓存 Agent，使下次请求重建并加载新 MCP
    invalidate_mcp_cache(username)
    evicted = invalidate_user_agents(username)

    # 仅校验新添加的 server
    server_status = []
    try:
        all_config = load_mcp_config(username)
        for name in new_servers:
            cfg = all_config.get(name)
            if not cfg:
                continue
            single = {name: cfg}
            try:
                client = MultiServerMCPClient(single)
                tools = await client.get_tools()
                server_status.append({"name": name, "status": "ok", "tools_count": len(tools), "error": ""})
                logger.info(f"MCP 校验 | {name} ✅ 成功 ({len(tools)} 工具)")
            except Exception as e:
                server_status.append({"name": name, "status": "error", "tools_count": 0, "error": str(e)})
                logger.warning(f"MCP 校验 | {name} ❌ 失败: {e}")
    except Exception as e:
        logger.warning(f"MCP 校验失败 | 用户: {username} | {e}")

    return {
        "status": "ok",
        "added": list(new_servers.keys()),
        "servers": list(existing.keys()),
        "path": str(user_mcp_path),
        "agents_invalidated": evicted,
        "server_status": server_status,
    }


@router.delete("/mcp/server/{name}", summary="删除指定 MCP 服务（即时同步后端）")
async def delete_mcp_server(
    name: str,
    username: Annotated[str, Depends(get_current_username)],
):
    """按 servers 下的名称删除单个 MCP server。

    删除后立即失效缓存与 Agent，下次请求不会再加载该 MCP。
    """
    _cfg = get_agent_config()
    config: Config | None = _cfg["config"] if _cfg and _cfg.get("config") else None

    raw = _read_user_mcp_raw(username, config)
    existing: dict[str, Any] = raw.get("servers", {})

    if name not in existing:
        raise HTTPException(status_code=404, detail=f"MCP 服务不存在: {name}")

    existing.pop(name, None)
    raw["servers"] = existing
    user_mcp_path = _write_user_mcp_raw(username, config, raw)
    logger.info(
        f"删除 MCP | 用户: {username} | 删除: {name} | 剩余: {list(existing.keys())} | "
        f"路径: {user_mcp_path}"
    )

    # 失效 MCP 工具缓存 + 该用户已缓存 Agent，使下次请求不再加载已删除的 MCP
    invalidate_mcp_cache(username)
    evicted = invalidate_user_agents(username)

    return {
        "status": "ok",
        "deleted": name,
        "servers": list(existing.keys()),
        "path": str(user_mcp_path),
        "agents_invalidated": evicted,
    }
