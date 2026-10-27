"""专家 Agent 的 HTTP 层辅助逻辑：用户/记录转换、配置读取、生成与落库。

职责边界（2026-10 迁移后）：

- **本模块**：只服务专家 CRUD 接口（``api/expert.py``）——记录 →对外结构转换、
  配置与标签读取、重名校验、LLM 生成后落库；
- **``services/expert_runtime.py``**：专家对话运行时（提示词加载、职责边界护栏、
  相关性预检、短路拒答、消息落库）。聊天主链路与专家接口都要用，故下沉到 services，
  避免 ``api/chat.py`` 与 ``api/expert.py`` 互相 import。

依赖方向：本模块**不 import ``api.expert``**（避免循环），旧内部名字经
``__all__`` re-export 继续可用（``expert_api._to_public`` 等）；运行时函数从
``services.expert_runtime`` 原样转发，历史调用点（``_load_expert_prompt`` 等）不破。
"""

import logging
from typing import Optional

from fastapi import HTTPException

from easy_agent.db.expert_store import (
    STATUS_ACTIVE,
    STATUS_FAILED,
)
from easy_agent.db.repositories.expert_repository import ExpertRepository
from easy_agent.agent.model import create_model
from easy_agent.services import (
    get_agent_config,
)
from easy_agent.services.expert_runtime import (  # noqa: F401 - re-export 历史名字
    _as_str_list,
    _current_user_id,
    _expert_flags,
    _expert_meta_for_message,
    _expert_profile,
    _expert_tag_name,
    _load_expert_prompt,
    _persist_user_turn,
    _precheck_disable_thinking,
    _precheck_llm,
    _recent_context,
    _refusal_event_stream,
    _relevance_gate,
    _session_logger_for,
    _with_guardrail,
)
from easy_agent.services.expert_service import (
    generate_expert_config,
    slugify,
    write_expert_version,
)

logger = logging.getLogger(__name__)


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
        # 专家绑定的技能包目录名（对话时按它做技能作用域硬隔离）
        "export_required_skills": record.get("export_required_skills", []),
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
    store: ExpertRepository,
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
    repo: ExpertRepository,
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
    store = repo
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

