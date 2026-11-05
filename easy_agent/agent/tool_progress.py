"""工具进度中间件：给「查不到」一个终止信号，并抑制探索型循环。

背景分两代问题：

1. ``ls`` / ``glob`` / ``grep`` 查不到东西时返回的是**正常结果**
   （``No files found`` / ``No matches found``），``ToolErrorMiddleware`` 不介入，
   模型拿不到任何「该停」的信号；而工具真失败时回灌的提示又明说「可调整参数、
   改用其它工具」，于是「换个路径再试一次」成了默认动作。叠加真实路径与虚拟路径
   不一致（沙箱里只有 ``/workspace/``），单轮就会跑满 ``max_steps``（默认 100）次
   模型调用，最后以 ``Model call limits exceeded: run limit (100/100)`` 收场。
2. MCP / API 数据查询工具更隐蔽：每次返回 ``ok=true`` 的 JSON（哪怕只有 500 条
   脏数据），参数每轮都换（渠道 × 频率 × 年份 × 时间窗），原有「同参数指纹」
   重复检测永远不命中 → 「换个参数再查一次」成了理性策略，200+ 轮不终止。

本模块只在**工具结果层**做事，不改 backend、不写 state、不中止图：

1. 空结果尾注（原四件套 ``ls/glob/grep/execute``）：查了但没有时补终止信号；
2. 原地打转提醒（原四件套）：同一工具 + 同一参数连续重复到
   ``REPEAT_WARN_AFTER`` 次时点名次数；
3. MCP 止损（新增）：对 MCP 结果按**工具名**（参数无关）算三个量——
   连续空结果、本轮是否曾取到过数据、连续无进展（空结果或结果与历史逐字节相同），
   到阈值时向观测里注入升级式指令，把「停止」从可选项变成被授权、被要求的动作。

为什么不放 backend：``SkillsMiddleware`` 靠 ``ls`` 遍历一级子目录发现技能，
在 backend 层给空结果插假条目会被当成技能目录，技能列表直接坏掉。

为什么不硬中止：合法重复真实存在（同一文件不同 offset、滑窗分页、连续构建、
轮询任务），硬断图一旦误伤，表现是「任务没做完就结束」，比报错更难排查；
``max_steps`` 本身就是硬闸。所以这里只给信号。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from easy_agent.bootstrap.config_models import (
    DEFAULT_TOOL_PROGRESS_CONVERGE_LIMIT,
    DEFAULT_TOOL_PROGRESS_EMPTY_HARD_LIMIT,
    DEFAULT_TOOL_PROGRESS_EMPTY_LIMIT,
    DEFAULT_TOOL_PROGRESS_STOP_LIMIT,
)

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

#: MCP 无进展计数的回溯窗口：一轮 run 可能几十次调用，200 条够覆盖，
#: 且只做类型过滤 + 目标工具的指纹计算，成本可忽略。
_NO_PROGRESS_WINDOW = 200

# ---------------------------------------------------------------------------
# MCP 结果止损（新增）
# ---------------------------------------------------------------------------

#: 证据信封前缀（``services/mcp/evidence.py`` 生成，属于我们自己的稳定契约）。
_MCP_MARKER_PREFIX = "[MCP source:"
_MCP_ERROR_RE = re.compile(r"^\[MCP source:[^\]]*is_error=true[^\]]*\]", re.I)

#: 所有新注入的尾注都带这个标记：一是让模型一眼看出是平台提示而非业务数据，
#: 二是指纹计算时能把它剥掉，避免「同一结果被不同提示包过」导致判重失效。
NOTE_MARK = "〔进度提示〕"
_NOTE_PREFIX = NOTE_MARK

#: JSON 里表示行数的字段（先看计数，再看列表；顺序即优先级）。
_COUNT_KEYS = (
    "row_count",
    "rows_count",
    "total",
    "total_count",
    "count",
    "num_rows",
    "totalCount",
)
#: JSON 里承载数据列表的字段。
_LIST_KEYS = ("data", "items", "rows", "records", "list", "values", "result")

_MCP_EMPTY_FIRST = (
    f"{_NOTE_PREFIX}本次查询没有结果。若这是同一数据目标的第 1 次尝试，可以换一次参数；"
    "但同一目标连续 {n} 次无结果即视为「数据不可得」，必须停止并给出结论。"
)
#: 注意：``write_todos`` 的 status 只接受 pending / in_progress / completed，
#: 没有 cancelled。让模型标 cancelled 会拿到参数校验错误，反而多一轮无效调用。
_TODO_CLOSE_HINT = "用 write_todos 把对应 todo 标记为 completed，并在内容里注明「数据不可得」"
_MCP_EMPTY_WARN = (
    f"{_NOTE_PREFIX}⚠️ 同一数据目标已连续 {{n}} 次查询无结果（换参数也算同一次尝试）。"
    "**再查一次仍无结果，必须停止尝试**：给出「该数据不可得」结论，"
    f"{_TODO_CLOSE_HINT}，继续下一项任务。"
)
_MCP_EMPTY_STOP = (
    f"{_NOTE_PREFIX}⛔ 已连续 {{n}} 次查询无结果，达到止损上限。"
    "**立即停止对该目标的任何进一步查询**：在回复中明确告知用户「该数据不可得」"
    f"及已尝试的范围，{_TODO_CLOSE_HINT}，继续下一项任务。"
    "不要再发起任何确认性查询。"
)
_MCP_EMPTY_HARD_STOP = (
    f"{_NOTE_PREFIX}⛔ 同一工具已连续 {{n}} 次查询无结果，已超出「分页/分段遍历」的合理范围。"
    "即使本轮此前取到过数据，也不要再换参数试探：判定该数据目标不可得，"
    f"在回复里说明已尝试的范围，{_TODO_CLOSE_HINT}，继续下一项任务。"
)
_MCP_EMPTY_AFTER_DATA = (
    f"{_NOTE_PREFIX}该区间无数据。本轮已成功取到过数据，这属于正常遍历："
    "可继续下一区间；若已取全，直接汇总即可，不必回头补查。"
)
_MCP_CONVERGE = (
    f"{_NOTE_PREFIX}⚠️ 本工具连续 {{n}} 次调用都没有拿到新内容。"
    "除非下一步有明确的新假设，请开始收敛：1–2 次内给出数据质量结论。"
)
_MCP_STOP = (
    f"{_NOTE_PREFIX}⛔ 本工具连续 {{n}} 次调用没有拿到新内容，停止探查："
    "基于已获得的信息输出数据质量报告（已确认事实 / 不可得的数据 / 待补清单），"
    "结束本项工作。"
)


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


# ── MCP 结果解析 / 计数（纯函数，便于单测） ──────────────────────────────


def is_mcp_result(text: str) -> bool:
    """是否是证据信封包过的 MCP 结果（``[MCP source: …]`` 开头）。"""
    return text.lstrip().startswith(_MCP_MARKER_PREFIX)


def is_mcp_error(text: str) -> bool:
    """信封里 ``is_error=true``：工具本身失败，交给 ToolErrorMiddleware 处理。"""
    return bool(_MCP_ERROR_RE.match(text.lstrip()))


def strip_mcp_envelope(text: str) -> str:
    """去掉首行 ``[MCP source: …]`` 信封，返回原始结果。

    信封里有 ``fetched_at``（每次调用都不同），不剥掉就无法判断「同一批数据
    被取了两次」——指纹会永远不同。
    """
    stripped = text.lstrip()
    if not stripped.startswith(_MCP_MARKER_PREFIX):
        return text
    end = stripped.find("]")
    if end < 0:
        return text
    return stripped[end + 1 :].lstrip("\n")


def strip_progress_notes(text: str) -> str:
    """去掉本模块此前追加的尾注，只留业务数据本体。

    末尾的 ``rstrip`` 不能省：注解拼接形如 ``{原文}\\n{提示}``，剥掉提示后
    会留下一个换行；而本次调用的结果还没注解、没有这个换行。不统一两侧，
    「被提示包过的历史结果」与「本次原始结果」的指纹就会不同，判重直接失效。
    """
    index = text.find(NOTE_MARK)
    body = text[:index] if index >= 0 else text
    return body.rstrip()


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        raw = value.strip()
        if raw.lstrip("-").isdigit():
            return int(raw)
    return None


def json_is_empty(data: Any) -> bool | None:
    """JSON 结果是否表示「查了但没有」；``None`` 表示判不出（交给无进展计数）。

    宁可判不出，也不要用「全文子串包含 ``[]``」这种写法：K 线类结果里夹一个
    空数组字段就会被误判成整次查询无数据，直接触发止损——比漏检更糟。
    """
    if isinstance(data, list):
        return not data
    if not isinstance(data, dict):
        return None
    if not data:
        return True
    for key in _COUNT_KEYS:
        if key in data:
            number = _as_int(data.get(key))
            if number is not None:
                return number == 0
    for key in _LIST_KEYS:
        if key in data:
            value = data[key]
            if isinstance(value, list):
                return not value
            if isinstance(value, dict):
                nested = json_is_empty(value)
                if nested is not None:
                    return nested
    return None


def is_empty_mcp_body(body: str) -> bool:
    """MCP 结果本体是否为空（剥过信封）。"""
    stripped = body.strip()
    if not stripped:
        return True
    if stripped.lower() in _EMPTY_MARKERS:
        return True
    try:
        data = json.loads(stripped)
    except (TypeError, ValueError):
        return False
    return json_is_empty(data) is True


def body_fingerprint(body: str) -> str:
    """结果指纹：整段正文的 sha1。

    对**整段**做哈希而不是只取头尾——尾 512 字符在「已截断」的结果上恒为平台截断
    提示，长度也只剩 ``omitted`` 的差别；一旦两页内容不同但总长相同、开头又是同一
    段 schema，只取头尾就会撞出同一个指纹，合法分页被误判成「同一批数据反复取」。
    成本可忽略：200 条 × 2 万字符实测 3.5ms，低于一次 JSON 解析。
    """
    return hashlib.sha1(body.encode("utf-8", "ignore")).hexdigest()


def current_turn_messages(messages: list[Any]) -> list[Any]:
    """只保留**本轮**消息（最后一条 ``HumanMessage`` 之后的部分）。

    计数器说的「连续 N 次」必须是本轮内的连续：跨轮累加会让上一轮查到的数据
    永久性地把本轮判成「取到过数据」，止损就再也触发不了。用 ``HumanMessage``
    做轮次切分是不依赖额外状态的最稳做法；找不到用户消息时退回全窗口。
    """
    last_human = -1
    for index, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            last_human = index
    return messages[last_human + 1 :] if last_human >= 0 else messages


def tool_result_entries(state: Any, name: str) -> list[tuple[bool, str]]:
    """指定工具在**本轮**的结果序列 ``[(是否空, 指纹)]``，按时间顺序。

    ``ToolMessage.name`` 可能为空，因此用 ``tool_call_id`` 回查 ``AIMessage.tool_calls``
    拿工具名——这也是把「调用」与「结果」配对的唯一可靠方式。
    """
    messages = current_turn_messages(
        _state_messages(state)[-_NO_PROGRESS_WINDOW:]
    )
    names: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in getattr(message, "tool_calls", None) or []:
            call_id = call.get("id")
            if call_id:
                names[str(call_id)] = str(call.get("name") or "")

    entries: list[tuple[bool, str]] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        tool_name = str(getattr(message, "name", "") or "")
        if not tool_name:
            tool_name = names.get(str(getattr(message, "tool_call_id", "") or ""), "")
        if tool_name != name:
            continue
        body = strip_progress_notes(
            strip_mcp_envelope(_content_text(message))
        )
        entries.append((is_empty_mcp_body(body), body_fingerprint(body)))
    return entries


def trailing_empty_streak(entries: list[tuple[bool, str]]) -> int:
    """末尾连续空结果的次数（含最后一条）。"""
    streak = 0
    for empty, _ in reversed(entries):
        if not empty:
            break
        streak += 1
    return streak


def no_progress_streak(entries: list[tuple[bool, str]]) -> int:
    """末尾连续「无进展」的次数。

    无进展 = 空结果，或结果指纹与**此前**某次相同（同一批数据被原样再取一遍）。
    分页取数每页行集合不同 → 每页都算有进展 → 计数清零。
    """
    seen: set[str] = set()
    flags: list[bool] = []
    for empty, fingerprint in entries:
        flags.append(empty or (not empty and fingerprint in seen))
        seen.add(fingerprint)

    streak = 0
    for flag in reversed(flags):
        if not flag:
            break
        streak += 1
    return streak


class ToolProgressMiddleware(AgentMiddleware):
    """空结果终止信号 + 原地打转提醒 + MCP 探索型循环止损（只读 state）。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        empty_limit: int | None = None,
        converge_limit: int | None = None,
        stop_limit: int | None = None,
        empty_hard_limit: int | None = None,
    ) -> None:
        """阈值全部来自 ``config.yaml``（``AgentConfig.tool_progress_*``）。

        参数留 ``None`` 只为了「不传就用默认值」：默认值来自
        ``bootstrap.config_models`` 的常量，不是一个写在这里的裸数字。
        """
        super().__init__()
        self.enabled = bool(enabled)
        # 阈值兜底：非正整数回默认；hard 不得小于 empty、stop 不得小于 converge。
        self.empty_limit = max(1, int(empty_limit or DEFAULT_TOOL_PROGRESS_EMPTY_LIMIT))
        self.converge_limit = max(
            1, int(converge_limit or DEFAULT_TOOL_PROGRESS_CONVERGE_LIMIT)
        )
        self.stop_limit = max(
            self.converge_limit, int(stop_limit or DEFAULT_TOOL_PROGRESS_STOP_LIMIT)
        )
        self.empty_hard_limit = max(
            self.empty_limit,
            int(empty_hard_limit or DEFAULT_TOOL_PROGRESS_EMPTY_HARD_LIMIT),
        )

    # ── 原四件套（行为未改） ──────────────────────────────────────────

    def _scope_note(self, request: Any, name: str, args: Any, text: str) -> str:
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

        return "".join(notes)

    # ── MCP 止损（新增） ─────────────────────────────────────────────

    def _mcp_note(self, request: Any, name: str, text: str) -> str:
        body = strip_progress_notes(strip_mcp_envelope(text))
        current_empty = is_empty_mcp_body(body)

        history = tool_result_entries(getattr(request, "state", None), name)
        # 「本轮是否取到过数据」必须把本次结果算进来：本次非空即算取到过。
        has_data_once = (not current_empty) or any(not empty for empty, _ in history)

        entries = history + [(current_empty, body_fingerprint(body))]
        empty_streak = trailing_empty_streak(entries)
        no_progress = no_progress_streak(entries)

        notes: list[str] = []
        if current_empty:
            if empty_streak >= self.empty_hard_limit:
                # 硬顶：连续空到这个次数已不是「分页到底/该区间无数据」能解释的，
                # 与 has_data_once 无关（先查到 A 再反复查 B 时它恒为 true）。
                notes.append(_MCP_EMPTY_HARD_STOP.format(n=empty_streak))
                logger.warning(
                    "⛔ MCP 空结果硬顶止损 | 工具: %s | 连续空: %d 次 | 已达硬顶 %d",
                    name,
                    empty_streak,
                    self.empty_hard_limit,
                )
            elif empty_streak >= self.empty_limit and not has_data_once:
                notes.append(_MCP_EMPTY_STOP.format(n=empty_streak))
                logger.warning(
                    "⛔ MCP 空结果止损 | 工具: %s | 连续空: %d 次 | 本轮从未取到数据",
                    name,
                    empty_streak,
                )
            elif not has_data_once and empty_streak >= 2:
                notes.append(_MCP_EMPTY_WARN.format(n=empty_streak))
                logger.info(
                    "⚠️ MCP 连续空结果 | 工具: %s | 连续空: %d 次", name, empty_streak
                )
            elif has_data_once:
                # 取到过数据后的空结果 = 正常遍历（分页到底 / 该区间本就无数据），
                # 只说明情况，不升级、不要求关闭 todo；连到 empty_hard_limit 才硬顶。
                notes.append(_MCP_EMPTY_AFTER_DATA)
            else:
                notes.append(_MCP_EMPTY_FIRST.format(n=self.empty_limit))
        else:
            if no_progress >= self.stop_limit:
                notes.append(_MCP_STOP.format(n=no_progress))
                logger.warning(
                    "⛔ MCP 无进展止损 | 工具: %s | 连续无进展: %d 次 | 参数: %s",
                    name,
                    no_progress,
                    str(getattr(request, "tool_call", {}).get("args"))[:200],
                )
            elif no_progress >= self.converge_limit:
                notes.append(_MCP_CONVERGE.format(n=no_progress))
                logger.info("⚠️ MCP 无进展收敛提示 | 工具: %s | 连续 %d 次", name, no_progress)

        return "".join(notes)

    # ── 入口 ────────────────────────────────────────────────────────

    def annotate(self, request: Any, result: Any) -> Any:
        """给工具结果补尾注；非目标工具 / 非文本 / 错误结果原样返回。

        **必须同时支持 str 与 list 两种 content**：MCP 工具走的是
        ``response_format="content_and_artifact"``，ToolNode 组装出的
        ``ToolMessage.content`` 是 content block 列表（首块是证据信封，其后是正文）。
        只认 str 会让整个 MCP 止损在生产环境一次都不触发——单测用 str 构造发现不了。
        """
        if not self.enabled or not isinstance(result, ToolMessage):
            return result
        if not isinstance(result.content, (str, list)):
            return result

        call = getattr(request, "tool_call", None) or {}
        name = str(call.get("name") or "")
        args = call.get("args") or {}
        text = _content_text(result)
        if looks_like_error(text) or is_mcp_error(text):
            return result

        if is_mcp_result(text):
            note = self._mcp_note(request, name, text)
        elif name in _SCOPE_TOOLS:
            note = self._scope_note(request, name, args, text)
        else:
            return result

        if not note:
            return result
        if isinstance(result.content, str):
            merged = f"{text}\n{note}" if text.strip() else note
            return result.model_copy(update={"content": merged})
        # 分段内容：尾注单独成块。不动已有块，图片/文件等非文本块原样保留。
        return result.model_copy(
            update={"content": [*result.content, {"type": "text", "text": note}]}
        )

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        return self.annotate(request, handler(request))

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        return self.annotate(request, await handler(request))
