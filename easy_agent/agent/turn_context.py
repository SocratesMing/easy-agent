"""本轮上下文（DA-03 P0）：本轮临时注入只进模型请求，不写进 thread 的 messages。

知识库片段、页面上下文、上传文件预览、技能指令、工作区前缀只属于本轮：DB 存的是用户原话，
从 DB 重建历史时它们自然消失。会话级 thread（P1）由 thread 保存历史、每轮只输入本轮消息，
这些注入就不能再拼进 ``HumanMessage``，否则会永久留在历史里。做法：

- thread 里的本轮 ``HumanMessage`` 只存原话，``id`` 为本轮 ``message_id``；
- 渲染后的完整内容及服务端本轮说明随运行输入写进 ``turn_context`` 状态通道。它是 LastValue 通道，HITL
  中断后仍在 checkpoint 里，恢复的那次运行照样可用；下一轮输入会覆盖它；
- 主代理的摘要中间件在计 token 之前调用 :func:`render_turn_request`：把本轮消息换成渲染
  内容。之前各轮 ``AIMessage`` 里的推理原样保留、随请求回传（与 ``build_context_messages``
  从 DB 还原思考一致）。只改本次模型请求，不写回 state。

渲染放在摘要中间件里，而不是单独的中间件：deepagents 把新增的自定义中间件排在摘要之后
（更内层），单独的中间件改写请求时，摘要已经按原话计完 token。注入大段内容（回退模式的
文件全文、知识片段）时，请求会超过阈值却不触发摘要。

见 docs/archive/design/2026-10-19-da03-session-thread.md §6.2。
"""

from __future__ import annotations

import re
from typing import Annotated, Any, NotRequired, TypedDict

from deepagents.middleware.summarization import SummarizationMiddleware, SummarizationState
from langchain.agents.middleware.types import OmitFromOutput
from langchain_core.messages import AIMessage, SystemMessage

TURN_CONTEXT_KEY = "turn_context"


class TurnContext(TypedDict):
    """``message_id`` 定位 thread 里的本轮用户消息，``content`` 是发给模型的全文。"""

    message_id: str
    content: str
    # 可信的服务端本轮说明；与用户正文分开，随 turn_context 覆盖，不进入历史消息。
    system_context: NotRequired[str]
    scope_approved: NotRequired[bool]


class TurnContextSummarizationState(SummarizationState):
    """摘要状态 + ``turn_context``（只进输入，不进输出）。"""

    turn_context: Annotated[NotRequired[TurnContext | None], OmitFromOutput]
    context_compactions: Annotated[NotRequired[dict[str, dict]], OmitFromOutput]


def turn_input(
    messages: list,
    *,
    message_id: str,
    rendered: str,
    pinned_skills: list[str] | None = None,
    system_context: str = "",
    scope_approved: bool = False,
) -> dict[str, Any]:
    """一轮对话的图输入：消息链、本轮渲染内容、技能重载标记、本轮 pin 的技能。

    ``skills_metadata: None`` 是官方的技能重载方式（deepagents skills 文档 "Reload skills"）：
    同一 thread 的后续运行也会重新扫描技能目录，本会话新建的技能下一轮即可见。
    ``pinned_skills``（DA-13）是输入框选中的技能名：框架在下一次模型调用前把这些技能的
    ``SKILL.md`` 作为消息加进对话，用完自动清空。
    ``system_context`` 只接受服务端本轮说明，不应填入用户提供的内容。
    """
    payload = {
        "messages": messages,
        TURN_CONTEXT_KEY: {"message_id": message_id, "content": rendered},
        "skills_metadata": None,
    }
    if pinned_skills:
        payload["pinned_skills"] = list(pinned_skills)
    if system_context:
        payload[TURN_CONTEXT_KEY]["system_context"] = system_context
    if scope_approved:
        payload[TURN_CONTEXT_KEY]["scope_approved"] = True
    return payload


def render_turn_messages(messages: list, turn: TurnContext | None) -> list:
    """只把本轮用户消息换成渲染内容，其余消息（含各轮的推理）原样随请求回传给模型。

    没有 ``turn``、或本轮消息已不在列表里（例如被摘要截掉）时原样返回同一个列表。
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
    return rendered


def render_turn_request(request):
    """``ModelRequest`` 版本：只改本次请求的消息，不写回 state。"""
    turn = request.state.get(TURN_CONTEXT_KEY)
    rendered = render_turn_messages(request.messages, turn)
    if rendered is not request.messages:
        request = request.override(messages=rendered)
    # 先计入摘要预算；记忆中间件随后会把同一说明移到固定记忆使用说明之后。
    return render_turn_system_context(request)


def render_turn_system_context(request):
    """本轮服务端说明放在系统提示词末尾；重复渲染只保留一份，保留其它内容块。"""
    turn = request.state.get(TURN_CONTEXT_KEY)
    system_context = (turn or {}).get("system_context")
    if not system_context:
        return request
    note = {"type": "text", "text": "\n\n" + system_context}
    blocks = list(request.system_message.content_blocks) if request.system_message else []
    blocks = [block for block in blocks if block != note]
    messages = request.messages
    if (turn or {}).get("scope_approved"):
        # 仅本轮服务端已准入的普通聊天清理模型视图：历史与原始记忆继续保留，
        # 专家 / 知识库等没有此标记，不会被改写。工具调用与工具结果也始终保留。
        messages = _without_scope_refusals(messages, turn["message_id"])
        blocks = [_without_scope_memory(block) for block in blocks]
    return request.override(
        messages=messages, system_message=SystemMessage(content_blocks=[*blocks, note])
    )


_OBSOLETE_SCOPE = re.compile(
    r"(?:仅|只)(?:能|会)?(?:支持|回答|处理).{0,12}金融"
    r"|(?:服务|业务|职责)范围.{0,12}(?:限于|仅限).{0,8}金融"
    r"|非金融.{0,30}(?:拒答|不支持|拒绝)"
)


def _without_scope_refusals(messages: list, current_message_id: str) -> list:
    kept = []
    for message in messages:
        if (isinstance(message, AIMessage) and not message.tool_calls
                and isinstance(message.content, str) and _OBSOLETE_SCOPE.search(message.content)):
            if (kept and kept[-1].type == "human"
                    and kept[-1].id != current_message_id):
                kept.pop()
            continue
        kept.append(message)
    return kept


def _without_scope_memory(block: dict) -> dict:
    text = block.get("text", "")
    if block.get("type") != "text" or "<agent_memory>" not in text:
        return block
    return {**block, "text": "\n".join(
        line for line in text.splitlines() if not _OBSOLETE_SCOPE.search(line)
    )}


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
