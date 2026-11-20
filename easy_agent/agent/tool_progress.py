"""工具进度与循环熔断中间件（不改 backend、不写 state）。

覆盖探索型文件工具（ls/glob/grep/execute）、MCP 查询工具与 read_file：
- 软提示：空结果终止尾注、同参数连续重复提醒、MCP 连续低进展升级提示；
- 硬熔断（wrap_tool_call）：同参数连续空结果、低进展超预算、连续低价值、
  同参数累计超限及 read_file 同参数重复读取时直接返回熔断提示，避免阻塞后续任务。
"""

from __future__ import annotations

import hashlib
import json
import logging
import posixpath
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
    DEFAULT_TOOL_PROGRESS_SAME_ARGS_TOTAL_LIMIT,
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

#: 读取型工具：**不进 ``_SCOPE_TOOLS``**——空结果 / 低价值行数 /
#: 预算占比那套语义是为"查询"设计的，套到文件读取上会误伤（连续读多个 1 行小
#: 文件会被判成"连续低价值"）。这里只认一个零风险信号：同一路径 + 完全相同
#: 参数（file_path/offset/limit）的重复读，内容必然一致，正常工作流没有这种用法。
_READ_TOOLS = frozenset({"read_file", "read_uploaded_file"})

#: 第几次相同参数读取开始注入软提示（0 基：第二次读到的结果上就会出现）。
_READ_REPEAT_WARN_AFTER = 1

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

#: 往回看多少条消息做历史判定（重复 / 熔断 / 预算 / MCP 无进展共用一个窗口；
#: 只做类型过滤 + 指纹计算，成本可忽略）。
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

#: 读取型工具（``read_file``）重复读的合成消息：语义与查询熔断不同——不是"查不到"，
#: 是"内容已经拿到了"。所以不能说"改用其它数据来源"，要指向"用已读内容推进"，
#: 并顺带点出最可能的真实原因（技能要的工具不在会话里）。
_READ_FUSE_MESSAGE = (
    f"{NOTE_MARK}⛔ 重复读取已拦截（{{reason}}）。系统未执行本次读取。"
    "该文件内容你已经拿到，再读一次不会有新信息：请直接用已读内容推进；"
    "若需要文件后半段，改用 `offset` 从已读行号继续读；"
    "若技能点名的工具不在你的工具列表里，说明该数据服务未接入——"
    "直接告知用户「数据未就位」并说明缺少哪类服务，不要靠重读文档或换参数重试来验证。"
)

#: 读取型工具重复读的软提示（第二次相同参数读取时注入，第三次起熔断）。
_READ_REPEAT_HINT = (
    f"{NOTE_MARK}该文件已用完全相同的参数读过 {{n}} 次，内容一致。"
    "读取一次即视为已加载：需要后半段用 `offset` 续读，不要从头重读。"
)


def _fuse_text(reason: str, *, read: bool = False) -> str:
    return (_READ_FUSE_MESSAGE if read else _FUSE_MESSAGE).format(reason=reason)


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


#: 需要做路径归一化的参数名。模型在多轮里会换写法（``C:\a\b`` / ``c:/a/b`` /
#: 末尾带斜杠），不归一化就会被当成"不同参数"，绕开同参数重复判定。
_PATH_ARG_KEYS = frozenset({"file_path", "path", "dir_path", "root"})


def _normalize_args(args: Any) -> Any:
    """把路径类参数归一成可比较的形态（分隔符、重复斜杠、大小写盘符、末尾斜杠）。

    用 ``posixpath.normpath`` 而不是手写 replace：它顺带折叠 ``a//b``、
    去掉末尾斜杠、把 ``..`` 解析掉，同时**保留** POSIX 允许的 UNC 前导 ``//``。
    """
    if not isinstance(args, dict):
        return args
    normalized: dict[Any, Any] = {}
    for key, value in args.items():
        if key in _PATH_ARG_KEYS and isinstance(value, str) and value:
            text = posixpath.normpath(value.replace("\\", "/"))
            if text == ".":  # normpath("") / normpath(".") 的退化形态
                text = value
            elif len(text) > 1 and text[1] == ":":  # Windows 盘符不区分大小写
                text = text[0].lower() + text[1:]
            normalized[key] = text
        else:
            normalized[key] = value
    return normalized


#: 读取型工具里不参与指纹的参数。``limit`` 只决定从同一 offset 起读多少行，
#: 内容前缀是同一段：把 limit 从 1000 调成 500 再调成 2000 属于"同一份文件读了
#: 三遍"，若不剔除就能以"参数不同"绕过重复读熔断（offset 仍然参与，分页照常区分）。
_READ_IGNORED_ARGS = frozenset({"limit"})


def _fingerprint(name: str, args: Any) -> str:
    """工具调用指纹：工具名 + 规范化参数（键序无关，不可序列化时回退 str）。

    读取型工具额外剔除 ``limit``（见 ``_READ_IGNORED_ARGS``）；历史条目与本次
    调用共用本函数，剔除规则必须只有这一处，否则两侧口径不同会永远数不中。
    """
    normalized = _normalize_args(args)
    if name in _READ_TOOLS and isinstance(normalized, dict):
        normalized = {
            key: value for key, value in normalized.items() if key not in _READ_IGNORED_ARGS
        }
    try:
        serialized = json.dumps(
            normalized or {}, ensure_ascii=False, sort_keys=True, default=str
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


#: 本模块「整条替换」工具结果的合成消息标识。这类结果**没有真实执行**，
#: 必须完全排除在所有统计之外——否则每续调一次熔断，就有一条「非空、非重复」
#: 的合成结果被当成「有进展」，把无进展计数清零，熔断等于给自己发免死金牌
#: （线上曾因这个原因熔断后模型又续调上百次）。
#: 只认本模块自己的固定文案，且限定在最前面若干字符：真实业务数据里几乎不可能
#: 出现，同时避免大结果体里偶然命中。
_SYNTHETIC_MARKERS = ("⛔ 工具熔断", "⛔ 重复读取已拦截")
_SYNTHETIC_SCAN_CHARS = 200


def is_synthetic_result(text: str) -> bool:
    """工具结果是否为平台合成（熔断拦截），而非真实执行产物。

    注意区分三件事，统计口径完全不同：

    - **平台合成**（本条）：调用根本没执行，应视为「不存在」，不进任何统计；
    - **工具真实报错**（``looks_like_error`` / ``is_mcp_error``）：执行了但失败，
      是「无进展」，应照常计入；
    - **真实空结果**：执行了、确实没有数据，也是「无进展」。

    把前两者混为一谈，会出现「报错越多越容易触发」或「熔断越多越不触发」
    两种畸形，所以这里必须只认本模块的熔断文案。
    """
    if not text:
        return False
    head = text.lstrip()[: _SYNTHETIC_SCAN_CHARS]
    return any(marker in head for marker in _SYNTHETIC_MARKERS)


def strip_progress_notes(text: str) -> str:
    """去掉本模块此前追加的尾注，只留业务数据本体。

    末尾的 ``rstrip`` 不能省：注解拼接形如 ``{原文}\\n{提示}``，剥掉提示后
    会留下一个换行；而本次调用的结果还没注解、没有这个换行。不统一两侧，
    「被提示包过的历史结果」与「本次原始结果」的指纹就会不同，判重直接失效。

    除 NOTE_MARK 外还要剥 ``_SCOPE_HINT_MARKERS`` 两种存量四件套尾注。
    两类标记都是本模块的固定文案，出现在业务数据正文里的概率可以忽略。
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
    #: 是否为平台合成的熔断消息（调用未执行）。这类条目**保留在序列里**（范围
    #: 判定 ``is_mcp`` 还要用它），但所有「有进展 / 低价值 / 连续计数」都必须
    #: 跳过它——合成消息既不构成进展，也不构成执行。
    is_synthetic: bool = False


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
                is_synthetic=is_synthetic_result(raw_text),
            )
        )
    return entries


def trailing_empty_streak(entries: list[ToolResultEntry]) -> int:
    """末尾连续空结果的次数（含最后一条）。合成熔断消息不算一次执行。"""
    entries = [entry for entry in entries if not entry.is_synthetic]
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

    合成熔断消息先剔除：它不代表一次执行，否则续调时会被算成「又空了一次」，
    看似加速触发，实际会让「已拦截」与「已执行」在日志里无法区分。
    """
    entries = [entry for entry in entries if not entry.is_synthetic]
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
    """低价值条目总数（预算熔断的「低价值占比」用）。合成消息不计。"""
    entries = [entry for entry in entries if not entry.is_synthetic]
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

    合成熔断消息必须剔除：它正文非空、指纹唯一（原因文本不同），若不剔除会被
    判成「有进展」把 streak 清零——熔断消息等于给熔断机制自己发免死金牌。
    """
    entries = [entry for entry in entries if not entry.is_synthetic]
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
        same_args_total_limit: int | None = None,
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
        # 情况 1b：同参数累计执行上限（不论结果内容）。
        self.same_args_total_limit = max(
            1,
            int(
                same_args_total_limit
                if same_args_total_limit is not None
                else DEFAULT_TOOL_PROGRESS_SAME_ARGS_TOTAL_LIMIT
            ),
        )

    # ── 原四件套（行为未改） ──────────────────────────────────────────

    def _read_note(self, request: Any, name: str, args: Any) -> str:
        """读取型工具的重复读软提示（第二次同参数读取就出现，第 4 次起熔断）。

        与 ``_scope_note`` 分开实现：读取没有"空结果 / 行数过少"的语义，
        唯一有意义的信息就是"这份文件你已经读过几遍、内容没变"。
        """
        args_fp = _fingerprint(name, args)
        real = [
            entry
            for entry in tool_result_entries(getattr(request, "state", None), name)
            if not entry.is_synthetic
        ]
        same = sum(1 for entry in real if entry.args_fp == args_fp)
        if same < _READ_REPEAT_WARN_AFTER:
            return ""
        logger.warning(
            "%s🔁 重复读取同一文件 | 工具: %s | 已读: %d 次 | 参数: %s",
            _log_prefix(request),
            name,
            same,
            str(args)[:200],
        )
        return _READ_REPEAT_HINT.format(n=same)

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

    def _mcp_note(
        self,
        request: Any,
        name: str,
        text: str,
        *,
        history: list[ToolResultEntry] | None = None,
    ) -> str:
        body = strip_progress_notes(strip_mcp_envelope(text))
        current_empty = is_empty_mcp_body(body)

        state = getattr(request, "state", None)
        prefix = _log_prefix(request)
        if history is None:
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

    # ── 工具熔断 ────────────────────────────────────────────────────

    def _fuse_reason(
        self,
        request: Any,
        name: str,
        args: Any,
        *,
        entries: list[ToolResultEntry] | None = None,
    ) -> str | None:
        """无状态判定本次调用是否应熔断；返回原因文本，``None`` 表示放行。"""
        state = getattr(request, "state", None)
        if entries is None:
            entries = tool_result_entries(state, name)

        # 合成熔断消息只参与范围判定，不计入真实执行次数与低价值占比。
        real_entries = [entry for entry in entries if not entry.is_synthetic]
        has_synthetic = len(real_entries) != len(entries)

        if name in _READ_TOOLS:
            if entries and not real_entries:
                return "本轮对该文件的读取持续被系统拦截，历史结果已超出判定窗口"
            args_fp = _fingerprint(name, args)
            same_reads = sum(1 for entry in real_entries if entry.args_fp == args_fp)
            if same_reads >= self.fuse_limit:
                return f"同一文件已用完全相同的参数读取 {same_reads} 次，内容没有变化"
            return None

        if (
            name not in _SCOPE_TOOLS
            and not any(e.is_mcp for e in entries)
            and not has_synthetic
        ):
            return None

        args_fp = _fingerprint(name, args)

        if entries and not real_entries:
            return "本轮对该工具的调用持续被系统拦截，历史执行结果已超出判定窗口"

        empty_streak = trailing_args_empty_streak(real_entries, args_fp)
        if empty_streak >= self.fuse_limit:
            return f"同一查询已连续 {empty_streak} 次返回空结果"

        same_args_total = sum(1 for entry in real_entries if entry.args_fp == args_fp)
        if same_args_total >= self.same_args_total_limit:
            return (
                f"同一查询（工具名+参数完全相同）本轮已累计执行 {same_args_total} 次"
                f"（上限 {self.same_args_total_limit}），结果无进展"
            )

        # 超预算仅在叠加低进展信号时熔断，避免误拦健康分页。
        call_count = tool_call_count_in_turn(state, name)
        if call_count > self.tool_call_budget:
            ratio = (
                low_value_count(real_entries, low_value_rows=self.low_value_rows)
                / len(real_entries)
                if real_entries
                else 0.0
            )
            trailing_lv = no_progress_streak(
                real_entries, low_value_rows=self.low_value_rows
            )
            if trailing_lv >= 2 or ratio >= 0.5:
                return (
                    f"本工具本轮已调用 {call_count} 次（超出预算 {self.tool_call_budget}），"
                    f"且低价值结果占比 {ratio:.0%}"
                )

        low_value = no_progress_streak(real_entries, low_value_rows=self.low_value_rows)
        if low_value >= self.low_value_limit:
            return f"本工具已连续 {low_value} 次查询未取得有效数据（空/过少/重复）"

        return None

    def _fused_tool_message(self, request: Any, name: str, reason: str) -> ToolMessage:
        """返回与当前 tool_call 配对的合成熔断 ToolMessage。"""
        call = getattr(request, "tool_call", None) or {}
        call_id = str(call.get("id") or "")
        return ToolMessage(
            content=_fuse_text(reason, read=name in _READ_TOOLS),
            tool_call_id=call_id,
            name=name,
        )

    def _maybe_fuse(
        self, request: Any, *, entries: list[ToolResultEntry] | None = None
    ) -> ToolMessage | None:
        """工具执行前的熔断检查；命中返回合成结果，未命中返回 ``None`` 放行。"""
        if not self.enabled:
            return None
        try:
            call = getattr(request, "tool_call", None) or {}
            name = str(call.get("name") or "")
            if not name:
                return None
            args = call.get("args") or {}
            reason = self._fuse_reason(request, name, args, entries=entries)
            if reason is None:
                return None
            logger.warning(
                "%s⛔ %s，本次调用已拦截 | 工具: %s | 原因: %s | 参数: %s",
                _log_prefix(request),
                "重复读取" if name in _READ_TOOLS else "工具熔断",
                name,
                reason,
                str(args)[:200],
            )
            return self._fused_tool_message(request, name, reason)
        except Exception as exc:  # noqa: BLE001 - 熔断检查失败不阻断工具执行
            logger.debug("熔断检查跳过（%s: %s）", type(exc).__name__, exc)
            return None

    # ── 入口 ────────────────────────────────────────────────────────

    def _prefetched_entries(self, request: Any) -> list[ToolResultEntry] | None:
        """单次工具调用周期内预取一次历史条目，避免 _maybe_fuse / annotate / _mcp_note 重复扫描。"""
        if not self.enabled:
            return None
        call = getattr(request, "tool_call", None) or {}
        name = str(call.get("name") or "")
        if not name:
            return None
        try:
            return tool_result_entries(getattr(request, "state", None), name)
        except Exception:  # noqa: BLE001
            return None

    def annotate(
        self,
        request: Any,
        result: Any,
        *,
        entries: list[ToolResultEntry] | None = None,
    ) -> Any:
        """给工具结果补尾注（兼容 str 与 list content）；非目标/错误结果原样返回。"""
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

        history = (
            entries
            if entries is not None
            else tool_result_entries(getattr(request, "state", None), name)
        )
        if is_mcp_result(text) or any(e.is_mcp for e in history):
            note = self._mcp_note(request, name, text, history=history)
        elif name in _SCOPE_TOOLS:
            note = self._scope_note(request, name, args, text)
        elif name in _READ_TOOLS:
            note = self._read_note(request, name, args)
        else:
            return result

        if not note:
            return result
        if isinstance(result.content, str):
            merged = f"{text}\n{note}" if text.strip() else note
            return result.model_copy(update={"content": merged})
        return result.model_copy(
            update={"content": [*result.content, {"type": "text", "text": note}]}
        )

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        entries = self._prefetched_entries(request)
        fused = self._maybe_fuse(request, entries=entries)
        if fused is not None:
            return fused
        return self.annotate(request, handler(request), entries=entries)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        entries = self._prefetched_entries(request)
        fused = self._maybe_fuse(request, entries=entries)
        if fused is not None:
            return fused
        return self.annotate(request, await handler(request), entries=entries)
