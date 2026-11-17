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

本模块在**工具结果层 + 工具执行入口**做事，不改 backend、不写 state：

1. 空结果尾注（原四件套 ``ls/glob/grep/execute``）：查了但没有时补终止信号；
2. 原地打转提醒（原四件套）：同一工具 + 同一参数连续重复到
   ``REPEAT_WARN_AFTER`` 次时点名次数；
3. MCP 止损（对 MCP 结果按**工具名**（参数无关）算连续空结果、是否取到过
   数据、连续无进展/低价值，到阈值时注入升级式指令）；
4. **工具熔断（2026-11-17，硬止损）**：软提示拦不住执意续调的模型，线上两次
   出现 200+ 步循环。以下三类命中后，下一次调用在 ``wrap_tool_call`` 里直接
   拦截，返回合成的「已熔断」ToolMessage，不再执行真实调用——
   a. 同一查询（工具名 + 参数指纹）连续空结果 ≥ ``fuse_limit``（默认 3）；
   b. 单工具本轮调用总数超过 ``tool_call_budget``（默认 20，换参数也算）
      **且低进展信号成立**（低价值结果占比过半 / 末尾连续无进展）——
      健康分页（每页都是足量新数据）不受预算限制；
   c. 同一工具连续「低价值」结果（空 / 行数过少 / 与此前重复）≥
      ``low_value_limit``（默认 6）。

熔断覆盖探索型四件套与 MCP 查询工具；所有触发点都打带 ``thread_id`` 前缀的
日志（warning/info），便于排查「谁熔断的、为什么熔断」。

为什么熔断放 ``wrap_tool_call`` 而不是 ``before_model`` 的 ``jump_to="end"``：
多任务轮次真实存在（todo 列表里「继续下一项任务」），整轮终止会误伤
「A 查不到但 B/C 还没做」的合法场景；工具级熔断只封死这一口井，模型仍可
给结论、切工具或继续后续任务。``max_steps`` / 调用上限仍是最后防线。

为什么不放 backend：``SkillsMiddleware`` 靠 ``ls`` 遍历一级子目录发现技能，
在 backend 层给空结果插假条目会被当成技能目录，技能列表直接坏掉。
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from typing import Any, NamedTuple

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from easy_agent.bootstrap.config_models import (
    DEFAULT_TOOL_PROGRESS_CONVERGE_LIMIT,
    DEFAULT_TOOL_PROGRESS_EMPTY_HARD_LIMIT,
    DEFAULT_TOOL_PROGRESS_EMPTY_LIMIT,
    DEFAULT_TOOL_PROGRESS_FUSE_LIMIT,
    DEFAULT_TOOL_PROGRESS_LOW_VALUE_LIMIT,
    DEFAULT_TOOL_PROGRESS_LOW_VALUE_ROWS,
    DEFAULT_TOOL_PROGRESS_STOP_LIMIT,
    DEFAULT_TOOL_PROGRESS_TOOL_CALL_BUDGET,
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

#: 往回看多少条消息做历史判定（重复 / 熔断 / 预算共用一个窗口；
#: 只做类型过滤 + 指纹计算，成本可忽略）。
_NO_PROGRESS_WINDOW = 200

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
    f"{NOTE_MARK}本次查询没有结果。若这是同一数据目标的第 1 次尝试，可以换一次参数；"
    "但同一目标连续 {n} 次无结果即视为「数据不可得」，必须停止并给出结论。"
)
#: 注意：``write_todos`` 的 status 只接受 pending / in_progress / completed，
#: 没有 cancelled。让模型标 cancelled 会拿到参数校验错误，反而多一轮无效调用。
_TODO_CLOSE_HINT = "用 write_todos 把对应 todo 标记为 completed，并在内容里注明「数据不可得」"
_MCP_EMPTY_WARN = (
    f"{NOTE_MARK}⚠️ 同一数据目标已连续 {{n}} 次查询无结果（换参数也算同一次尝试）。"
    "**再查一次仍无结果，必须停止尝试**：给出「该数据不可得」结论，"
    f"{_TODO_CLOSE_HINT}，继续下一项任务。"
)
_MCP_EMPTY_STOP = (
    f"{NOTE_MARK}⛔ 已连续 {{n}} 次查询无结果，达到止损上限。"
    "**立即停止对该目标的任何进一步查询**：在回复中明确告知用户「该数据不可得」"
    f"及已尝试的范围，{_TODO_CLOSE_HINT}，继续下一项任务。"
    "不要再发起任何确认性查询。"
)
_MCP_EMPTY_HARD_STOP = (
    f"{NOTE_MARK}⛔ 同一工具已连续 {{n}} 次查询无结果，已超出「分页/分段遍历」的合理范围。"
    "即使本轮此前取到过数据，也不要再换参数试探：判定该数据目标不可得，"
    f"在回复里说明已尝试的范围，{_TODO_CLOSE_HINT}，继续下一项任务。"
)
_MCP_EMPTY_AFTER_DATA = (
    f"{NOTE_MARK}该区间无数据。本轮已成功取到过数据，这属于正常遍历："
    "可继续下一区间；若已取全，直接汇总即可，不必回头补查。"
)
_MCP_CONVERGE = (
    f"{NOTE_MARK}⚠️ 本工具连续 {{n}} 次调用都没有拿到新内容。"
    "除非下一步有明确的新假设，请开始收敛：1–2 次内给出数据质量结论。"
)
_MCP_STOP = (
    f"{NOTE_MARK}⛔ 本工具连续 {{n}} 次调用没有拿到新内容，停止探查："
    "基于已获得的信息输出数据质量报告（已确认事实 / 不可得的数据 / 待补清单），"
    "结束本项工作。"
)

# ── 工具熔断（硬止损）────────────────────────────────────────────────

#: 低价值连续计数的升级提示：熔断前最后一次结果上注入（下一次调用会被拦截）。
_LOW_VALUE_STOP = (
    f"{NOTE_MARK}⛔ 本工具已连续 {{n}} 次调用没有取得有效数据"
    "（空 / 数据过少 / 与此前重复）。**立即停止对该工具的任何进一步调用**："
    "基于已获得的信息直接输出结论（已确认事实 / 不可得的数据 / 待补清单），"
    f"{_TODO_CLOSE_HINT}。下一次调用将被系统熔断拦截。"
)

#: 熔断时返回给模型的合成 ToolMessage 正文。必须让模型一眼看懂：
#: 不是工具出错，是系统主动拦截，且要求它收敛给结论而不是换法重试。
_FUSE_MESSAGE = (
    f"{NOTE_MARK}⛔ 工具熔断（{{reason}}）。系统已拦截本次调用，未执行。"
    "请立即停止对同一目标的重复查询：基于已获得的信息直接给出结论"
    "（已确认事实 / 不可得的数据 / 待补清单），或改用其它数据来源、"
    "继续下一项任务。"
)


def _fuse_text(reason: str) -> str:
    return _FUSE_MESSAGE.format(reason=reason)


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
    for message in _state_messages(state)[-_NO_PROGRESS_WINDOW:]:
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


#: 历史版本给四件套追加的尾注（**不带 NOTE_MARK** 的存量格式）：
#: ``_EMPTY_HINT`` / ``_REPEAT_HINT``。剥指纹时必须一并去掉，否则
#: 「被注解过的历史结果」判不出空（正文混入了提示文本），同参数空结果
#: 的连续计数每轮都被自己的提示打断，熔断永远不会触发。
_SCOPE_HINT_MARKERS = ("（此处为空。", "（本工具同样参数的调用已连续")


def strip_progress_notes(text: str) -> str:
    """去掉本模块此前追加的尾注，只留业务数据本体。

    末尾的 ``rstrip`` 不能省：注解拼接形如 ``{原文}\\n{提示}``，剥掉提示后
    会留下一个换行；而本次调用的结果还没注解、没有这个换行。不统一两侧，
    「被提示包过的历史结果」与「本次原始结果」的指纹就会不同，判重直接失效。

    除 NOTE_MARK 外还要剥 ``_SCOPE_HINT_MARKERS`` 两种存量四件套尾注
    （2026-11-17 之前的版本不带标记）。两类标记都是本模块的固定文案，
    出现在业务数据正文里的概率可以忽略。
    """
    index = text.find(NOTE_MARK)
    body = text[:index] if index >= 0 else text
    for marker in _SCOPE_HINT_MARKERS:
        pos = body.find(marker)
        if pos >= 0:
            body = body[:pos]
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


def json_row_count(data: Any) -> int | None:
    """JSON 结果的数据行数；判不出返回 ``None``（与 ``json_is_empty`` 同风格）。

    计数字段优先（``row_count`` 等），其次列表字段长度；两者都没有时判不出，
    宁可判不出也不猜——低价值误判会把合法的长文本结果拦下来。
    """
    if isinstance(data, list):
        return len(data)
    if not isinstance(data, dict):
        return None
    for key in _COUNT_KEYS:
        if key in data:
            number = _as_int(data.get(key))
            if number is not None:
                return number
    for key in _LIST_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def body_row_count(body: str) -> int | None:
    """MCP 结果本体（剥过信封）的数据行数；空串/非 JSON 结构判不出。"""
    stripped = body.strip()
    if not stripped or stripped.lower() in _EMPTY_MARKERS:
        return None  # 空结果由 is_empty 判定，行数不参与
    try:
        data = json.loads(stripped)
    except (TypeError, ValueError):
        return None
    return json_row_count(data)


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


class ToolResultEntry(NamedTuple):
    """单次工具调用的结果摘要（只保留熔断/止损判定需要的六个量）。"""

    #: 是否「查了但没有」
    is_empty: bool
    #: 结果正文指纹（同一批数据被原样再取一遍时相同）
    body_fp: str
    #: 调用参数指纹（工具名 + 规范化参数；调用 ID 变化不影响识别）
    args_fp: str
    #: JSON 结果的行数（判不出时为 ``None``；用于低价值判定）
    rows: int | None
    #: 是否 MCP 证据信封结果（熔断范围判定：MCP 查询工具也纳入熔断）
    is_mcp: bool = False
    #: 正文是否为空串。空串的「重复」不能计入无进展：``mkdir`` 这类静默成功
    #: 的输出本来就是空，多次 mkdir 的空输出互为 dup 会把合法建目录任务熔断。
    is_blank: bool = False


def tool_result_entries(state: Any, name: str) -> list[ToolResultEntry]:
    """指定工具在**本轮**的结果序列，按时间顺序。

    ``ToolMessage.name`` 可能为空，因此用 ``tool_call_id`` 回查 ``AIMessage.tool_calls``
    拿工具名、参数与参数指纹——这也是把「调用」与「结果」配对的唯一可靠方式。

    判空必须分工具：四件套用参数感知的 :func:`is_empty_result`
    （``execute`` 只有查找类命令才判空，``mkdir`` 的静默成功不算），
    MCP 结果用 JSON 结构感知的 :func:`is_empty_mcp_body`。
    """
    messages = current_turn_messages(
        _state_messages(state)[-_NO_PROGRESS_WINDOW:]
    )
    names: dict[str, str] = {}
    args_by_id: dict[str, Any] = {}
    arg_fps: dict[str, str] = {}
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in getattr(message, "tool_calls", None) or []:
            call_id = str(call.get("id") or "")
            if call_id:
                call_name = str(call.get("name") or "")
                call_args = call.get("args") or {}
                names[call_id] = call_name
                args_by_id[call_id] = call_args
                arg_fps[call_id] = _fingerprint(call_name, call_args)

    entries: list[ToolResultEntry] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        tool_name = str(getattr(message, "name", "") or "")
        call_id = str(getattr(message, "tool_call_id", "") or "")
        if not tool_name:
            tool_name = names.get(call_id, "")
        if tool_name != name:
            continue
        raw_text = _content_text(message)
        body = strip_progress_notes(strip_mcp_envelope(raw_text))
        call_args = args_by_id.get(call_id, {})
        entries.append(
            ToolResultEntry(
                is_empty=(
                    is_empty_result(tool_name, call_args, body)
                    if tool_name in _SCOPE_TOOLS
                    else is_empty_mcp_body(body)
                ),
                body_fp=body_fingerprint(body),
                args_fp=arg_fps.get(call_id, ""),
                rows=body_row_count(body),
                is_mcp=is_mcp_result(raw_text),
                is_blank=not body.strip(),
            )
        )
    return entries


def trailing_empty_streak(entries: list[ToolResultEntry]) -> int:
    """末尾连续空结果的次数（含最后一条）。"""
    streak = 0
    for entry in reversed(entries):
        if not entry.is_empty:
            break
        streak += 1
    return streak


def trailing_args_empty_streak(entries: list[ToolResultEntry], args_fp: str) -> int:
    """末尾连续「同参数指纹 + 空结果」的次数（只看该工具自身的调用序列）。

    这是情况 1 的判定：同一查询反复空结果。调用 ID 每次都变，但参数指纹相同
    就算同一次尝试；中间插入该工具的其它调用、或某次查到了东西，都算中断。
    （其它工具的调用不进入该工具的 entries，不影响这里的计数——
    与 MCP 按「工具名」统计的口径一致。）
    """
    streak = 0
    for entry in reversed(entries):
        if entry.args_fp != args_fp or not entry.is_empty:
            break
        streak += 1
    return streak


def _entry_is_low_value(
    entry: ToolResultEntry, seen: set[str], low_value_rows: int | None
) -> bool:
    """单条结果是否「低价值」：空 / 与此前重复（空正文豁免）/ 行数过少。

    ``no_progress_streak``（末尾连续计数）与 ``low_value_count``（总数，
    供预算的占比判定用）共用这一定义——判定口径只有一份，改这里就够。
    """
    if entry.is_empty:
        return True
    # 空正文的「重复」不算低价值：mkdir 这类静默成功的输出本就是空，
    # 多次 mkdir 的空输出互为 dup 会把合法建目录任务判成原地打转。
    # （真正的空查询结果由 is_empty 单独计入。）
    if entry.body_fp in seen and not entry.is_blank:
        return True
    return (
        low_value_rows is not None
        and entry.rows is not None
        and entry.rows <= low_value_rows
    )


def low_value_count(
    entries: list[ToolResultEntry], *, low_value_rows: int | None = None
) -> int:
    """低价值条目总数（预算熔断的「低价值占比」用）。"""
    seen: set[str] = set()
    count = 0
    for entry in entries:
        if _entry_is_low_value(entry, seen, low_value_rows):
            count += 1
        seen.add(entry.body_fp)
    return count


def no_progress_streak(
    entries: list[ToolResultEntry], *, low_value_rows: int | None = None
) -> int:
    """末尾连续「无进展 / 低价值」的次数。

    无进展 = 空结果，或结果指纹与**此前**某次相同（同一批数据被原样再取一遍）；
    传入 ``low_value_rows`` 后，行数 <= 该值的结果也算（探测性取回的少量脏数据
    不再被当成「有进展」）。分页取数每页行集合不同 → 每页都算有进展 → 计数清零。
    """
    seen: set[str] = set()
    flags: list[bool] = []
    for entry in entries:
        flags.append(_entry_is_low_value(entry, seen, low_value_rows))
        seen.add(entry.body_fp)

    streak = 0
    for flag in reversed(flags):
        if not flag:
            break
        streak += 1
    return streak


def tool_call_count_in_turn(state: Any, name: str) -> int:
    """该工具在**本轮**被模型发起的调用总次数（含待执行的当前调用）。

    熔断在 ``wrap_tool_call`` 里执行时，当前 ``AIMessage``（带本次 tool_call）
    已经进了 state，所以计数天然包含本次调用：``count > budget`` 即第
    ``budget + 1`` 次起熔断。熔断后模型若执意续调，计数继续增长、恒超预算。
    """
    messages = current_turn_messages(
        _state_messages(state)[-_NO_PROGRESS_WINDOW:]
    )
    count = 0
    for message in messages:
        if not isinstance(message, AIMessage):
            continue
        for call in getattr(message, "tool_calls", None) or []:
            if str(call.get("name") or "") == name:
                count += 1
    return count


def _thread_id(request: Any) -> str:
    """尽力从运行配置取 thread_id 作日志前缀；取不到返回空串（省略前缀）。

    中间件进共享编译图、不绑定会话，所以只能从本次运行的 config 里读
    （与 ``approval_audit`` 同一取法）。
    """
    config = getattr(getattr(request, "runtime", None), "config", None)
    if isinstance(config, Mapping):
        configurable = config.get("configurable")
    else:
        getter = getattr(config, "get", None)
        configurable = getter("configurable") if callable(getter) else None
    if not isinstance(configurable, Mapping):
        return ""
    return str(configurable.get("thread_id") or "")


def _log_prefix(request: Any) -> str:
    thread = _thread_id(request)
    return f"[{thread}] " if thread else ""


class ToolProgressMiddleware(AgentMiddleware):
    """空结果终止信号 + 原地打转提醒 + MCP 止损 + 工具熔断（只读 state，无实例态）。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        empty_limit: int | None = None,
        converge_limit: int | None = None,
        stop_limit: int | None = None,
        empty_hard_limit: int | None = None,
        fuse_limit: int | None = None,
        tool_call_budget: int | None = None,
        low_value_rows: int | None = None,
        low_value_limit: int | None = None,
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
        # 熔断三参数（情况 1/2 的硬止损）。
        self.fuse_limit = max(1, int(fuse_limit or DEFAULT_TOOL_PROGRESS_FUSE_LIMIT))
        self.tool_call_budget = max(
            1, int(tool_call_budget or DEFAULT_TOOL_PROGRESS_TOOL_CALL_BUDGET)
        )
        self.low_value_rows = max(
            0,
            int(
                low_value_rows
                if low_value_rows is not None
                else DEFAULT_TOOL_PROGRESS_LOW_VALUE_ROWS
            ),
        )
        self.low_value_limit = max(
            1, int(low_value_limit or DEFAULT_TOOL_PROGRESS_LOW_VALUE_LIMIT)
        )

    # ── 原四件套（行为未改） ──────────────────────────────────────────

    def _scope_note(self, request: Any, name: str, args: Any, text: str) -> str:
        notes: list[str] = []
        empty = is_empty_result(name, args, text)
        if empty:
            notes.append(_EMPTY_HINT)

        prefix = _log_prefix(request)
        repeats = consecutive_repeats(
            getattr(request, "state", None), _fingerprint(name, args)
        )
        if repeats >= REPEAT_WARN_AFTER:
            notes.append(_REPEAT_HINT.format(n=repeats))
            logger.warning(
                "%s🔁 工具原地打转 | 工具: %s | 连续: %d 次 | 参数: %s",
                prefix,
                name,
                repeats,
                str(args)[:200],
            )

        # 低价值/空结果连续到熔断线：先给最后一次升级提示，下一次调用熔断。
        lv = no_progress_streak(
            tool_result_entries(getattr(request, "state", None), name),
            low_value_rows=self.low_value_rows,
        )
        if lv >= self.low_value_limit:
            notes.append(_LOW_VALUE_STOP.format(n=lv))
            logger.warning(
                "%s⛔ 工具连续无有效数据，下次调用将熔断 | 工具: %s | 连续: %d 次",
                prefix,
                name,
                lv,
            )
        elif empty:
            logger.debug("🔍 工具空结果已提示 | 工具: %s | 参数: %s", name, str(args)[:200])

        return "".join(notes)

    # ── MCP 止损（新增） ─────────────────────────────────────────────

    def _mcp_note(self, request: Any, name: str, text: str) -> str:
        body = strip_progress_notes(strip_mcp_envelope(text))
        current_empty = is_empty_mcp_body(body)

        state = getattr(request, "state", None)
        prefix = _log_prefix(request)
        history = tool_result_entries(state, name)
        # 「本轮是否取到过数据」必须把本次结果算进来：本次非空即算取到过。
        has_data_once = (not current_empty) or any(not e.is_empty for e in history)

        current_args_fp = _fingerprint(
            name, (getattr(request, "tool_call", None) or {}).get("args")
        )
        entries = history + [
            ToolResultEntry(
                is_empty=current_empty,
                body_fp=body_fingerprint(body),
                args_fp=current_args_fp,
                rows=body_row_count(body),
                is_mcp=is_mcp_result(text),
                is_blank=not body.strip(),
            )
        ]
        empty_streak = trailing_empty_streak(entries)
        no_progress = no_progress_streak(entries)
        low_value = no_progress_streak(entries, low_value_rows=self.low_value_rows)

        # 连续低价值到熔断线：先给最后一次升级提示，下一次调用熔断。
        # 放在所有分支最前，与 _fuse_reason 的熔断判定同一口径：换参数空查 /
        # 1 行脏数据 / 重复结果都收口到这里。必须排在「从未取到数据的止损」
        # 之前，否则换参数空查被该分支遮住，永远到不了熔断线。
        if low_value >= self.low_value_limit:
            logger.warning(
                "%s⛔ 工具连续无有效数据，下次调用将熔断 | 工具: %s | 连续: %d 次",
                prefix,
                name,
                low_value,
            )
            return _LOW_VALUE_STOP.format(n=low_value)

        if current_empty:
            if empty_streak >= self.empty_hard_limit:
                # 硬顶：连空到这个次数已不是「分页到底/该区间无数据」能解释的。
                # 默认配置下不可达（连空到 low_value_limit 就被上面的熔断线
                # 收口了），保留给「low_value_limit 调大」的配置作软止损兜底。
                logger.warning(
                    "%s⛔ MCP 空结果硬顶止损 | 工具: %s | 连续空: %d 次 | 已达硬顶 %d",
                    prefix,
                    name,
                    empty_streak,
                    self.empty_hard_limit,
                )
                return _MCP_EMPTY_HARD_STOP.format(n=empty_streak)
            if empty_streak >= self.empty_limit and not has_data_once:
                # 重话只在首次达阈值时注入，之后静默（下一个里程碑是熔断前
                # 警告），避免同一句连续重复注入浪费 token。
                if empty_streak == self.empty_limit:
                    logger.warning(
                        "%s⛔ MCP 空结果止损 | 工具: %s | 连续空: %d 次 | 本轮从未取到数据",
                        prefix,
                        name,
                        empty_streak,
                    )
                    return _MCP_EMPTY_STOP.format(n=empty_streak)
                return ""
            if not has_data_once and empty_streak >= 2:
                logger.info(
                    "%s⚠️ MCP 连续空结果 | 工具: %s | 连续空: %d 次", prefix, name, empty_streak
                )
                return _MCP_EMPTY_WARN.format(n=empty_streak)
            if has_data_once:
                # 取到过数据后的空结果 = 正常遍历（分页到底 / 该区间本就无数据），
                # 只说明情况，不升级、不要求关闭 todo；连到熔断线才收口。
                return _MCP_EMPTY_AFTER_DATA
            return _MCP_EMPTY_FIRST.format(n=self.empty_limit)

        if no_progress >= self.stop_limit:
            logger.warning(
                "%s⛔ MCP 无进展止损 | 工具: %s | 连续无进展: %d 次 | 参数: %s",
                prefix,
                name,
                no_progress,
                str((getattr(request, "tool_call", None) or {}).get("args"))[:200],
            )
            return _MCP_STOP.format(n=no_progress)
        if no_progress >= self.converge_limit:
            logger.info(
                "%s⚠️ MCP 无进展收敛提示 | 工具: %s | 连续 %d 次", prefix, name, no_progress
            )
            return _MCP_CONVERGE.format(n=no_progress)
        return ""

    # ── 工具熔断（硬止损，2026-11-17）───────────────────────────────

    def _fuse_reason(self, request: Any, name: str, args: Any) -> str | None:
        """本次调用是否应被熔断；返回原因文本，``None`` 表示放行。

        三个判定全部从消息历史**无状态推导**（本轮范围，跨轮不累加），
        不写 state、不加实例计数——中间件实例进共享编译图，必须无实例态。
        """
        state = getattr(request, "state", None)
        entries = tool_result_entries(state, name)

        # 熔断只覆盖查询型工具：探索型四件套 + MCP 查询工具（历史结果里出现过
        # 证据信封即认定）。写类工具（write_file/edit_file/write_todos 等）在
        # 长任务里合法调用几十次是常态，绝不能被预算误熔断。
        if name not in _SCOPE_TOOLS and not any(e.is_mcp for e in entries):
            return None

        # 情况 1：同一查询（工具名 + 参数指纹）连续空结果——3 次执行后，
        # 第 4 次起熔断。entries 不含本次（结果还没产生），streak 即已执行次数。
        args_fp = _fingerprint(name, args)
        empty_streak = trailing_args_empty_streak(entries, args_fp)
        if empty_streak >= self.fuse_limit:
            return f"同一查询已连续 {empty_streak} 次返回空结果"

        # 情况 2a：单工具本轮调用预算。计数含本次（当前 AIMessage 已进 state），
        # count > budget 即第 budget+1 次起熔断。
        # 预算必须叠加「低进展信号」才熔断：健康分页（每页都是足量新数据）
        # 超过 20 页是合法工作，绝不能拦；只拦「参数组合爆炸式试探」——
        # 其特征是低价值结果占比过半，或末尾已连续无进展。
        call_count = tool_call_count_in_turn(state, name)
        if call_count > self.tool_call_budget:
            # 预算必须叠加「低进展信号」才熔断：健康分页（每页都是足量新数据）
            # 超过 20 页是合法工作，绝不能拦；只拦「参数组合爆炸式试探」——
            # 其特征是低价值结果占比过半，或末尾已连续无进展。
            ratio = (
                low_value_count(entries, low_value_rows=self.low_value_rows)
                / len(entries)
                if entries
                else 0.0
            )
            trailing_lv = no_progress_streak(entries, low_value_rows=self.low_value_rows)
            if trailing_lv >= 2 or ratio >= 0.5:
                return (
                    f"本工具本轮已调用 {call_count} 次（超出预算 {self.tool_call_budget}），"
                    f"且低价值结果占比 {ratio:.0%}"
                )

        # 情况 2b：连续低价值结果（空 / 行数过少 / 与此前重复）——
        # 每次「都换参数、都只回一点脏数据」的探索型循环，
        # 空结果与逐字节重复判定都不命中，只有这条能兜住。
        low_value = no_progress_streak(entries, low_value_rows=self.low_value_rows)
        if low_value >= self.low_value_limit:
            return f"本工具已连续 {low_value} 次查询未取得有效数据（空/过少/重复）"

        return None

    def _fused_tool_message(self, request: Any, name: str, reason: str) -> ToolMessage:
        """熔断时返回的合成 ToolMessage。

        必须真的返回 ToolMessage（而不是抛异常/返回 None）：当前 AIMessage 已带
        tool_call，缺一条配对的 ToolMessage 会让下一轮模型调用直接报 API 错误。
        """
        call = getattr(request, "tool_call", None) or {}
        call_id = str(call.get("id") or "")
        return ToolMessage(content=_fuse_text(reason), tool_call_id=call_id, name=name)

    def _maybe_fuse(self, request: Any) -> ToolMessage | None:
        """工具执行前的熔断检查；命中返回合成结果，未命中返回 ``None`` 放行。

        熔断检查绝不能影响正常工具调用：任何取值/判定异常都吞成 debug。
        """
        if not self.enabled:
            return None
        try:
            call = getattr(request, "tool_call", None) or {}
            name = str(call.get("name") or "")
            if not name:
                return None
            args = call.get("args") or {}
            reason = self._fuse_reason(request, name, args)
            if reason is None:
                return None
            logger.warning(
                "%s⛔ 工具熔断，本次调用已拦截 | 工具: %s | 原因: %s | 参数: %s",
                _log_prefix(request),
                name,
                reason,
                str(args)[:200],
            )
            return self._fused_tool_message(request, name, reason)
        except Exception as exc:  # noqa: BLE001 - 熔断检查失败不该阻断工具执行
            logger.debug("熔断检查跳过（%s: %s）", type(exc).__name__, exc)
            return None

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

        # MCP 工具识别口径必须与 _fuse_reason 一致：本次是信封结果，**或**
        # 历史里出现过信封。只看本次文本会让「历史有信封、本次返回裸结果」
        # 的场景熔断在计数、提示却不给——模型在收不到任何警告的情况下被拦。
        if is_mcp_result(text) or any(
            e.is_mcp
            for e in tool_result_entries(getattr(request, "state", None), name)
        ):
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
        fused = self._maybe_fuse(request)
        if fused is not None:
            return fused
        return self.annotate(request, handler(request))

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        fused = self._maybe_fuse(request)
        if fused is not None:
            return fused
        return self.annotate(request, await handler(request))
