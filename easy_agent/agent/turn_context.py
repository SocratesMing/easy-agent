"""本轮上下文（DA-03 P0）：本轮临时注入只进模型请求，不写进 thread 的 messages。

知识库片段、页面上下文、上传文件预览、技能指令、工作区前缀只属于本轮：DB 存的是用户原话，
从 DB 重建历史时它们自然消失。会话级 thread（P1）由 thread 保存历史、每轮只输入本轮消息，
这些注入就不能再拼进 ``HumanMessage``，否则会永久留在历史里。做法：

- thread 里的本轮 ``HumanMessage`` 只存原话，``id`` 为本轮 ``message_id``；
- 渲染后的完整内容随运行输入写进 ``turn_context`` 状态通道。它是 LastValue 通道，HITL
  中断后仍在 checkpoint 里，恢复的那次运行照样可用；下一轮输入会覆盖它；
- 主代理的摘要中间件在计 token 之前调用 :func:`render_turn_request`：把本轮消息换成渲染
  内容，并剥离之前各轮 ``AIMessage`` 里的推理内容（与 ``build_context_messages`` 不回灌
  历史思考一致）。只改本次模型请求，不写回 state。

渲染放在摘要中间件里，而不是单独的中间件：deepagents 把新增的自定义中间件排在摘要之后
（更内层），单独的中间件改写请求时，摘要已经按原话计完 token。注入大段内容（回退模式的
文件全文、知识片段）时，请求会超过阈值却不触发摘要。

见 docs/archive/design/2026-10-19-da03-session-thread.md §6.2。
"""

from __future__ import annotations

from typing import Annotated, Any, NotRequired, TypedDict

from deepagents.middleware.summarization import SummarizationMiddleware, SummarizationState
from langchain.agents.middleware.types import OmitFromOutput
from langchain_core.messages import AIMessage

TURN_CONTEXT_KEY = "turn_context"

#: 历史 AIMessage 里要剥离的推理内容块（Anthropic thinking、LangChain 标准 reasoning）。
_REASONING_BLOCK_TYPES = frozenset({"thinking", "redacted_thinking", "reasoning"})


class TurnContext(TypedDict):
    """``message_id`` 定位 thread 里的本轮用户消息，``content`` 是发给模型的全文。"""

    message_id: str
    content: str


class TurnContextSummarizationState(SummarizationState):
    """摘要状态 + ``turn_context``（只进输入，不进输出）。"""

    turn_context: Annotated[NotRequired[TurnContext | None], OmitFromOutput]


def turn_input(
    messages: list,
    *,
    message_id: str,
    rendered: str,
    pinned_skills: list[str] | None = None,
) -> dict[str, Any]:
    """一轮对话的图输入：消息链、本轮渲染内容、技能重载标记、本轮 pin 的技能。

    ``skills_metadata: None`` 是官方的技能重载方式（deepagents skills 文档 "Reload skills"）：
    同一 thread 的后续运行也会重新扫描技能目录，本会话新建的技能下一轮即可见。
    ``pinned_skills``（DA-13）是输入框选中的技能名：框架在下一次模型调用前把这些技能的
    ``SKILL.md`` 作为消息加进对话，用完自动清空。
    """
    payload = {
        "messages": messages,
        TURN_CONTEXT_KEY: {"message_id": message_id, "content": rendered},
        "skills_metadata": None,
    }
    if pinned_skills:
        payload["pinned_skills"] = list(pinned_skills)
    return payload


def _is_reasoning_block(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") in _REASONING_BLOCK_TYPES


def _strip_reasoning(message: AIMessage) -> AIMessage:
    """剥离历史 AIMessage 的推理内容（只针对本轮之前的轮次）。

    上游只在「工具调用 → 工具结果」续跑时要求 ``reasoning_content``，本轮工具
    循环里的消息不在剥离范围内，故历史轮次剥掉不影响请求合法性。
    """
    update: dict[str, Any] = {}
    if isinstance(message.content, list):
        kept = [block for block in message.content if not _is_reasoning_block(block)]
        if len(kept) != len(message.content):
            update["content"] = kept or ""
    if "reasoning_content" in message.additional_kwargs:
        update["additional_kwargs"] = {
            k: v for k, v in message.additional_kwargs.items() if k != "reasoning_content"
        }
    return message.model_copy(update=update) if update else message


def render_turn_messages(messages: list, turn: TurnContext | None) -> list:
    """把本轮用户消息换成渲染内容，并剥离之前各轮 AIMessage 的推理内容。

    没有 ``turn``、或本轮消息已不在列表里（例如被摘要截掉）时原样返回同一个列表。
    本轮之后的消息（本轮工具循环）保持原样：Anthropic 在工具循环里需要本轮的 thinking。
    """
    target = (turn or {}).get("message_id")
    content = (turn or {}).get("content")
    if not target or content is None:
        return messages
    index = next(
        (
            i
            for i, message in enumerate(messages)
            if getattr(message, "type", None) == "human" and getattr(message, "id", None) == target
        ),
        None,
    )
    if index is None:
        return messages
    rendered = list(messages)
    rendered[index] = messages[index].model_copy(update={"content": content})
    for i in range(index):
        if isinstance(rendered[i], AIMessage):
            rendered[i] = _strip_reasoning(rendered[i])
    return rendered


def render_turn_request(request):
    """``ModelRequest`` 版本：只改本次请求的消息，不写回 state。"""
    rendered = render_turn_messages(request.messages, request.state.get(TURN_CONTEXT_KEY))
    return request if rendered is request.messages else request.override(messages=rendered)


class TurnAwareSummarizationMiddleware(SummarizationMiddleware):
    """主代理的摘要中间件：先渲染本轮上下文，再按渲染后的请求计 token、决定是否摘要。"""

    state_schema = TurnContextSummarizationState

    @property
    def name(self) -> str:
        # 同名才会原位替换框架默认项；deepagents 的子类默认回落到类名。
        return "SummarizationMiddleware"

    def wrap_model_call(self, request, handler):
        return super().wrap_model_call(render_turn_request(request), handler)

    async def awrap_model_call(self, request, handler):
        return await super().awrap_model_call(render_turn_request(request), handler)
