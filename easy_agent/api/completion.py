"""Public chat-completion API - 直接透传大模型，供其他业务系统调用。

传入消息数组（system/user/assistant 角色 + 内容），调用大模型后返回完整对话上下文
（含本轮 assistant 回复），便于外部业务以无状态方式集成大模型能力。

SEC-09：接口必须登录后调用，并由限流中间件限制频率（见
``middleware/rate_limit._DEFAULT_RULES``）；入口处限制消息条数与内容总长度，
避免被匿名或单账号当作免费 LLM 代理刷额度。
"""

import logging
import time
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from easy_agent.agent.model import create_model, extract_reasoning
from easy_agent.services.usage import resolve_usage_identity
from easy_agent.utils.usage_context import usage_scope
from easy_agent.middleware import get_current_username
from easy_agent.models.api import ChatCompletionRequest
from easy_agent.services.agents.manager import get_agent_config

logger = logging.getLogger("easy_agent.completion")

router = APIRouter(prefix="/agent/completion", tags=["Completion"])

# 输入上限（SEC-09）：消息条数 / 单条长度在 ChatCompletionRequest 上由 Pydantic
# 限制（50 条、单条 100000 字符），这里再兜住所有消息 content 的总长度。
MAX_TOTAL_CONTENT_CHARS = 100_000

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
    from easy_agent.services.agents.manager import _llm_instance  # 延迟导入：测试 monkeypatch 语义：按模块属性替换，函数内导入才能取到替换后的对象

    llm = _llm_instance
    if llm is None:
        raise HTTPException(
            status_code=503, detail="大模型尚未初始化，请稍后重试"
        )
    return llm


def _to_lc_messages(messages):
    """将 {role, content[, thinking]} 列表转为 LangChain 消息列表。

    assistant 消息带的 ``thinking`` 放进 ``additional_kwargs["reasoning_content"]``，
    由模型层随请求回传（见 ``agent/model.py`` 的 ``inject_reasoning_content``）。
    """
    lc_messages = []
    for m in messages:
        role = (m.role or "").strip().lower()
        msg_cls = _ROLE_TO_MESSAGE.get(role)
        if msg_cls is None:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的消息角色: '{m.role}'，仅支持 system/user/assistant",
            )
        if msg_cls is AIMessage and m.thinking:
            lc_messages.append(
                AIMessage(content=m.content, additional_kwargs={"reasoning_content": m.thinking})
            )
        else:
            lc_messages.append(msg_cls(content=m.content))
    return lc_messages


def _context_item(message) -> dict:
    """返回给调用方的上下文条目：带思考的 assistant 消息保留 thinking，下次原样传回即可。"""
    item = {"role": message.role, "content": message.content}
    if message.thinking and (message.role or "").strip().lower() == "assistant":
        item["thinking"] = message.thinking
    return item


@router.post(
    "/chat",
    summary="公共对话补全",
    description=(
        "传入消息数组（如 system 提示词 + user 需求），调用大模型后返回完整对话上下文"
        "（含本轮 assistant 回复）。供其他业务系统直接调用。需要登录（Bearer token），"
        "并受 30 次/分钟限流；消息最多 50 条、内容（含 thinking）总长最多 100000 字符。"
        "assistant 消息可带 thinking（返回结果里的思考内容），会随请求回传给模型。"
    ),
)
async def chat_completion(
    request: ChatCompletionRequest,
    username: Annotated[str, Depends(get_current_username)],
) -> list:
    if not request.messages:
        raise HTTPException(status_code=400, detail="消息列表不能为空")

    total_chars = sum(len(m.content or "") + len(m.thinking or "") for m in request.messages)
    if total_chars > MAX_TOTAL_CONTENT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"消息内容总长度不能超过 {MAX_TOTAL_CONTENT_CHARS} 字符",
        )

    lc_messages = _to_lc_messages(request.messages)
    llm = _get_llm(request.model)

    start = time.time()
    try:
        identity = await resolve_usage_identity(username)
        with usage_scope(identity.user_id if identity else None, identity.session_id if identity else None):
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
    context = [_context_item(m) for m in request.messages]
    assistant_message = {"role": "assistant", "content": assistant_content}
    if thinking:
        assistant_message["thinking"] = thinking
    context.append(assistant_message)

    logger.info(
        "对话补全完成 | 用户=%s | 消息数=%d | 耗时=%ss | model=%s",
        username,
        len(context),
        elapsed,
        request.model or "(active)",
    )
    return context
