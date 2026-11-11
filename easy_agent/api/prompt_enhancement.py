"""普通登录用户的草稿增强接口，与管理员提示词管理独立。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, StrictStr, field_validator

from easy_agent.bootstrap.config_models import PromptEnhancementConfig
from easy_agent.bootstrap.runtime_config import get_runtime_agent_config
from easy_agent.services.usage import resolve_usage_identity
from easy_agent.utils.usage_context import usage_scope
from easy_agent.middleware import get_current_username
from easy_agent.services.prompt_enhancement import EnhancedPrompt, EnhancementError, enhance_prompt

router = APIRouter(prefix="/agent/prompts", tags=["Prompts"])


class EnhanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: StrictStr

    @field_validator("prompt")
    @classmethod
    def valid_prompt(cls, value: str) -> str:
        runtime = get_runtime_agent_config() or {}
        config = runtime.get("config")
        settings = config.prompt_enhancement if config else PromptEnhancementConfig()
        if not value.strip() or len(value) > settings.max_input_chars:
            raise ValueError("请输入有效提示词，或缩短输入内容")
        return value


@router.post("/enhance", response_model=EnhancedPrompt, summary="增强输入框提示词")
async def enhance(
    body: EnhanceRequest,
    username: Annotated[str, Depends(get_current_username)],
) -> EnhancedPrompt:
    runtime = get_runtime_agent_config() or {}
    config = runtime.get("config")
    if config is None:
        raise HTTPException(503, "提示词增强暂不可用，请联系管理员检查模型配置")
    try:
        identity = await resolve_usage_identity(username)
        with usage_scope(identity.user_id if identity else None, identity.session_id if identity else None):
            return await enhance_prompt(body.prompt, config)
    except EnhancementError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
