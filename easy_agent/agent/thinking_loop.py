"""思考循环守卫：本轮末尾连续相同的思考 → 强制收口（软着陆）。

背景（2026-11-18 线上两起循环的补丁之三）：``ToolProgressMiddleware`` 的
熔断只覆盖查询型工具（``_SCOPE_TOOLS`` + MCP），``read_file`` 等读取型工具
完全裸奔——模型反复「先加载技能」并以相同参数重读同一 ``SKILL.md``，思考
逐字重复（连「注意：第 4 个问题…」都一模一样），一直跑到 max_steps。
用户方案：像计数工具调用一样计数思考，相同思考连续出现 ≥3 次就结束。

实现与 ``tool_progress`` 同一套架构约定：

- **无状态推导**：中间件实例进共享编译图，不能有实例计数器；每轮从 state
  消息里取「本轮」（最后一条 ``HumanMessage`` 之后）的 AIMessage，对思考
  文本归一化后做末尾连续计数（与 ``no_progress_streak`` 同款逻辑）。
- **只在本轮计数**：跨轮不累加——上一轮问过类似问题、本轮重新思考出同样
  的文本是合法行为，用 ``HumanMessage`` 做轮次切分（与 ``tool_progress`` 一致）。
- **最小长度阈值**：todo 驱动的长任务里模型可能连续多步用一模一样的短思考
  （「继续执行下一项」）+ 每次调不同工具，属于正常工作；短思考不参与判定。

**为什么软着陆而不是 ``jump_to="end"`` 硬退**：硬退时图结束时没有最终
AIMessage，前端收不到回复；软着陆摘掉全部工具（框架对空工具列表走
``model.bind(...)`` 分支，模型物理上无法再发起调用）并注入终态指令，
模型输出纯文本 → 无 tool_calls → 图按正常路径自然结束，streaming / 落库 /
resume 全走现有通路，只多花一次模型调用，用户拿到的是真实回答。

思考文本的读取复用 ``agent/model.py::extract_reasoning``（各厂商字段名的
统一口径：``reasoning_content`` / ``reasoning`` / ``reason_content``），
另兼容 content 块里的 ``thinking`` 段（Anthropic 风格）。
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage

from easy_agent.bootstrap.config_models import (
    DEFAULT_THINKING_LOOP_LIMIT,
    DEFAULT_THINKING_LOOP_MIN_CHARS,
)
from easy_agent.agent.model import extract_reasoning

logger = logging.getLogger("easy_agent.agent")

#: 与 ``tool_progress._NO_PROGRESS_WINDOW`` 同款回溯窗口：只做类型过滤与
#: 哈希，成本可忽略，200 条覆盖一轮 run 的全部消息。
_WINDOW = 200

#: 归一化：去首尾空白 + 压缩连续空白。截图里的循环是严格逐字重复，
#: 这个强度足够；不做模糊比对，避免把「思考相似但不同」的合法推进误杀。
_WHITESPACE_RE = re.compile(r"\s+")

#: 强制收口时注入的终态指令（只进本次模型请求，不写回 state）。
#: 语义必须无歧义：不是工具出错，是系统判定循环并已摘除工具，
#: 要求直接给最终答复而不是任何形式的重试。
LOOP_BREAK_NOTE = (
    "〔循环终止〕检测到你连续多次输出完全相同的思考，没有取得任何新进展。"
    "系统已判定本轮陷入循环，并已移除全部工具：现在任何工具都不可用，"
    "也不要再尝试调用。请立即基于已获得的信息输出面向用户的最终答复："
    "说明已确认的事实、已执行的操作，未能完成的部分如实说明原因。"
)


def thinking_text(message: Any) -> str:
    """AIMessage 的思考纯文本（additional_kwargs 统一口径 + thinking 块兜底）。"""
    text = extract_reasoning(getattr(message, "additional_kwargs", None) or {})
    if text:
        return text
    content = getattr(message, "content", "")
    if isinstance(content, list):
        parts = [
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "thinking"
        ]
        return "".join(parts)
    return ""


def normalize_thinking(text: str) -> str:
    """思考归一化：去首尾空白 + 压缩连续空白。"""
    return _WHITESPACE_RE.sub(" ", (text or "").strip())


def thinking_loop_streak(messages: list, *, min_chars: int) -> int:
    """本轮末尾连续「同思考」的 AIMessage 条数（含最后一条）。

    - 只看最后一条 ``HumanMessage`` 之后（本轮）；找不到用户消息时退回全窗口。
    - 无思考 / 思考长度 < ``min_chars`` 的消息**打断**连续序列：既不计数，
      也中断前面的 streak——保守取向，宁漏不误杀。
    """
    from easy_agent.agent.tool_progress import current_turn_messages

    fingerprints: list[str] = []
    for message in current_turn_messages(list(messages or [])[-_WINDOW:]):
        if not isinstance(message, AIMessage):
            continue
        normalized = normalize_thinking(thinking_text(message))
        if len(normalized) < min_chars:
            fingerprints.append("")
            continue
        fingerprints.append(
            hashlib.sha1(normalized.encode("utf-8", "ignore")).hexdigest()
        )

    last = fingerprints[-1] if fingerprints else ""
    if not last:
        return 0
    streak = 0
    for item in reversed(fingerprints):
        if item != last:
            break
        streak += 1
    return streak


def _thread_id(request: Any) -> str:
    """尽力从运行配置取 thread_id 作日志前缀（与 ``tool_progress`` 同一取法）。"""
    from easy_agent.agent.tool_progress import _thread_id as _tool_progress_thread_id

    return _tool_progress_thread_id(request)


class ThinkingLoopMiddleware(AgentMiddleware):
    """思考循环守卫：连续相同思考到阈值时，摘掉工具强制模型收口（无实例态）。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        limit: int | None = None,
        min_chars: int | None = None,
    ) -> None:
        """阈值来自 ``config.yaml``（``AgentConfig.thinking_loop_*``）。"""
        super().__init__()
        self.enabled = bool(enabled)
        self.limit = max(2, int(limit or DEFAULT_THINKING_LOOP_LIMIT))
        self.min_chars = max(1, int(min_chars or DEFAULT_THINKING_LOOP_MIN_CHARS))

    def _should_break(self, request: Any) -> int:
        """本次模型调用前是否触发收口；返回连续次数，0 表示放行。"""
        state = getattr(request, "state", None)
        messages = state.get("messages") if isinstance(state, dict) else getattr(state, "messages", None)
        streak = thinking_loop_streak(list(messages or []), min_chars=self.min_chars)
        return streak if streak >= self.limit else 0

    def _break_request(self, request: Any, streak: int) -> Any:
        """软着陆：摘掉全部工具 + 注入终态指令，只改本次请求，不写回 state。

        框架对空工具列表走 ``model.bind(...)`` 分支（factory.py 标准路径），
        模型物理上无法再发起工具调用；输出纯文本后图自然结束。
        """
        logger.warning(
            "%s🧠 思考循环，强制收口 | 连续相同思考: %d 次 | 已移除全部工具",
            f"[{_thread_id(request)}] " if _thread_id(request) else "",
            streak,
        )
        messages = list(request.messages) + [HumanMessage(content=LOOP_BREAK_NOTE)]
        return request.override(messages=messages, tools=[], tool_choice=None)

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        if not self.enabled:
            return handler(request)
        try:
            streak = self._should_break(request)
        except Exception as exc:  # noqa: BLE001 - 守卫失败绝不阻断正常调用
            logger.debug("思考循环检查跳过（%s: %s）", type(exc).__name__, exc)
            return handler(request)
        if streak:
            return handler(self._break_request(request, streak))
        return handler(request)

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        if not self.enabled:
            return await handler(request)
        try:
            streak = self._should_break(request)
        except Exception as exc:  # noqa: BLE001 - 守卫失败绝不阻断正常调用
            logger.debug("思考循环检查跳过（%s: %s）", type(exc).__name__, exc)
            return await handler(request)
        if streak:
            return await handler(self._break_request(request, streak))
        return await handler(request)
