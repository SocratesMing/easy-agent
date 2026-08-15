"""Public chat-completion API - 直接透传大模型，供其他业务系统调用。

传入消息数组（system/user/assistant 角色 + 内容），调用大模型后返回完整对话上下文
（含本轮 assistant 回复），便于外部业务以无状态方式集成大模型能力。
"""

import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..model import create_model, extract_reasoning
from ..models.api import ChatCompletionRequest
from ..services.agent_manager import get_agent_config

logger = logging.getLogger("easy_agent.completion")

router = APIRouter(prefix="/api/completion", tags=["Completion"])

_ROLE_TO_MESSAGE = {
    "system": SystemMessage,
    "user": HumanMessage,
    "assistant": AIMessage,
}


def _get_llm(model_name: Optional[str] = None):
    """获取 LLM 实例。指定 model_name 时按 config.models 解析，否则用全局实例。"""
    if model_name:
        agent_cfg = get_agent_config()
        if agent_cfg and agent_cfg.get("config"):
            try:
                return create_model(agent_cfg["config"], model_name)
            except Exception as e:
                logger.warning(
                    "按 model_name=%s 创建模型失败，回退全局实例: %s",
                    model_name,
                    e,
                )
    # _llm_instance 是启动时懒初始化的全局变量，须在调用时读取最新值，
    # 不能用模块顶层 from import 绑定（否则拿到的是初始化前的 None）。
    from ..services.agent_manager import _llm_instance

    llm = _llm_instance
    if llm is None:
        raise HTTPException(
            status_code=503, detail="大模型尚未初始化，请稍后重试"
        )
    return llm


def _to_lc_messages(messages):
    """将 {role, content} 列表转为 LangChain 消息列表。"""
    lc_messages = []
    for m in messages:
        role = (m.role or "").strip().lower()
        msg_cls = _ROLE_TO_MESSAGE.get(role)
        if msg_cls is None:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的消息角色: '{m.role}'，仅支持 system/user/assistant",
            )
        lc_messages.append(msg_cls(content=m.content))
    return lc_messages


@router.post(
    "/chat",
    summary="公共对话补全",
    description=(
        "传入消息数组（如 system 提示词 + user 需求），调用大模型后返回完整对话上下文"
        "（含本轮 assistant 回复）。供其他业务系统直接调用。"
    ),
)
async def chat_completion(request: ChatCompletionRequest):
    if not request.messages:
        raise HTTPException(status_code=400, detail="消息列表不能为空")

    lc_messages = _to_lc_messages(request.messages)
    llm = _get_llm(request.model)

    start = time.time()
    try:
        result = await llm.ainvoke(lc_messages)
    except Exception as e:
        logger.error("对话补全调用失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"大模型调用失败: {e}")

    elapsed = round(time.time() - start, 2)
    assistant_content = (
        result.content if hasattr(result, "content") else str(result)
    )
    thinking = extract_reasoning(getattr(result, "additional_kwargs", None))

    # 组装完整上下文：原始消息 + 本轮 assistant 回复
    context = [{"role": m.role, "content": m.content} for m in request.messages]
    assistant_message = {"role": "assistant", "content": assistant_content}
    if thinking:
        assistant_message["thinking"] = thinking
    context.append(assistant_message)

    logger.info(
        "对话补全完成 | 消息数=%d | 耗时=%ss | model=%s",
        len(context),
        elapsed,
        request.model or "(active)",
    )
    return context
