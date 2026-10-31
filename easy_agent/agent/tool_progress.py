"""工具进度中间件：给「找不到」一个终止信号，并抑制原地打转。

背景：``ls`` / ``glob`` / ``grep`` 查不到东西时返回的是**正常结果**
（``No files found`` / ``No matches found``），``ToolErrorMiddleware`` 不介入，
模型拿不到任何「该停」的信号；而工具真失败时回灌的提示又明说「可调整参数、
改用其它工具」，于是「换个路径再试一次」成了默认动作。叠加真实路径与虚拟路径
不一致（沙箱里只有 ``/workspace/``），单轮就会跑满 ``max_steps``（默认 100）次
模型调用，最后以 ``Model call limits exceeded: run limit (100/100)`` 收场。

本模块只在**工具结果层**做两件事，不改 backend：

1. 空结果尾注：对本轮确实「查了但没有」的探索型工具追加一句终止信号；
2. 原地打转提醒：同一工具 + 同一参数连续重复到 ``REPEAT_WARN_AFTER`` 次时，
   在尾注里点名次数，让模型自己收口。

为什么不放 backend：``SkillsMiddleware`` 靠 ``ls`` 遍历一级子目录发现技能，
在 backend 层给空结果插假条目会被当成技能目录，技能列表直接坏掉。

为什么不硬中止：合法重复真实存在（同一文件不同 offset、连续构建、轮询任务），
硬断图一旦误伤，表现是「任务没做完就结束」，比报错更难排查；而 ``max_steps``
本身就是硬闸，不需要第二道。所以这里只给信号。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

logger = logging.getLogger("easy_agent.agent")

#: 框架「查了但没有」的原样返回
#: （deepagents ``backends/utils.py`` 的 ``No matches found`` 与
#: ``middleware/filesystem.py`` 的 ``No files found``）。
_EMPTY_MARKERS = frozenset({"no files found", "no matches found", "[]", "{}"})

#: 只对这些探索型工具加提示：读文件、写文件、todo、task 一律不动。
_SCOPE_TOOLS = frozenset({"ls", "glob", "grep", "execute"})

#: ``execute`` 只有查找类命令才判空，避免 ``mkdir`` 这类静默成功被误判成空。
_LOOKUP_COMMAND_RE = re.compile(
    r"^\s*(?:find|grep|rg|ls|cat|head|tail|stat|test|tree|du|wc)\b"
)

#: 视为「工具已经失败」的前缀：这类结果自带 ToolErrorMiddleware 的提示，
#: 再叠一句「别重试」会互相打架，所以跳过。
_ERROR_PREFIXES = ("error:", "错误：", "cannot write to", "[command failed")

_EMPTY_HINT = (
    "（此处为空。继续换同级路径不会有结果，请改用已知正确路径，"
    "或直接说明数据缺失。）"
)
_REPEAT_HINT = "（本工具同样参数的调用已连续 {n} 次，结果未变，请停止重试。）"

#: 同一工具 + 同一参数连续重复到几次开始点名。
REPEAT_WARN_AFTER = 5

#: 往回看多少条消息做重复判定（只看末尾连续，中间插入别的调用即中断）。
_MAX_HISTORY_MESSAGES = 40


def _content_text(message: Any) -> str:
    """``ToolMessage.content`` 的纯文本形态（兼容 str / 分段列表）。"""
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return "" if content is None else str(content)


def _fingerprint(name: str, args: Any) -> str:
    """工具调用指纹：工具名 + 规范化参数（键序无关，不可序列化时回退 str）。"""
    try:
        serialized = json.dumps(
            args or {}, ensure_ascii=False, sort_keys=True, default=str
        )
    except (TypeError, ValueError):
        serialized = str(args)
    return f"{name}:{serialized}"


def _state_messages(state: Any) -> list[Any]:
    if state is None:
        return []
    if isinstance(state, dict):
        messages = state.get("messages")
    else:
        messages = getattr(state, "messages", None)
    return list(messages or [])


def consecutive_repeats(state: Any, fingerprint: str) -> int:
    """末尾连续相同指纹的次数，**含本次**（当前 tool_call 已在最后一条 AIMessage 里）。"""
    history: list[str] = []
    for message in _state_messages(state)[-_MAX_HISTORY_MESSAGES:]:
        if not isinstance(message, AIMessage):
            continue
        for call in getattr(message, "tool_calls", None) or []:
            history.append(
                _fingerprint(str(call.get("name") or ""), call.get("args"))
            )
    count = 0
    for item in reversed(history):
        if item != fingerprint:
            break
        count += 1
    return count


def is_empty_result(name: str, args: Any, text: str) -> bool:
    """这个工具结果是否表示「查了，但确实没有」。"""
    stripped = text.strip()
    if name == "execute":
        command = str((args or {}).get("command") or "")
        if not _LOOKUP_COMMAND_RE.match(command):
            return False
        return not stripped
    return stripped.lower() in _EMPTY_MARKERS or not stripped


def looks_like_error(text: str) -> bool:
    head = text.lstrip().lower()
    if any(head.startswith(prefix) for prefix in _ERROR_PREFIXES):
        return True
    return "[stderr]" in head and "no such file" in head


class ToolProgressMiddleware(AgentMiddleware):
    """空结果终止信号 + 原地打转提醒（只读 state，不写入、不中止）。"""

    def __init__(self, *, enabled: bool = True) -> None:
        super().__init__()
        self.enabled = bool(enabled)

    def annotate(self, request: Any, result: Any) -> Any:
        """给工具结果补尾注；非目标工具 / 非文本 / 错误结果原样返回。"""
        if not self.enabled or not isinstance(result, ToolMessage):
            return result
        if not isinstance(result.content, str):
            return result

        call = getattr(request, "tool_call", None) or {}
        name = str(call.get("name") or "")
        if name not in _SCOPE_TOOLS:
            return result

        args = call.get("args") or {}
        text = _content_text(result)
        if looks_like_error(text):
            return result

        notes: list[str] = []
        empty = is_empty_result(name, args, text)
        if empty:
            notes.append(_EMPTY_HINT)

        repeats = consecutive_repeats(
            getattr(request, "state", None), _fingerprint(name, args)
        )
        if repeats >= REPEAT_WARN_AFTER:
            notes.append(_REPEAT_HINT.format(n=repeats))
            logger.warning(
                "🔁 工具原地打转 | 工具: %s | 连续: %d 次 | 参数: %s",
                name,
                repeats,
                str(args)[:200],
            )
        elif empty:
            logger.debug("🔍 工具空结果已提示 | 工具: %s | 参数: %s", name, str(args)[:200])

        if not notes:
            return result
        suffix = "".join(notes)
        merged = f"{text}\n{suffix}" if text.strip() else suffix
        return result.model_copy(update={"content": merged})

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        return self.annotate(request, handler(request))

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        return self.annotate(request, await handler(request))
