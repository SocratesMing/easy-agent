"""交给记忆整理模型的本轮摘要：本轮做了哪些操作（工具调用、写了哪些文件、审批结果），以及
回复的开头和结尾。

改造前整理模型只看到用户原话前 800 字和回复前 2000 字：工具调用、生成的文件、用户批准还是
拒绝都看不到，长回复末尾的「下一步」也被截掉；可会话记忆要记的「决策与理由」，理由常常就在
这些操作里（试了哪个方案失败、用户拒绝了哪一步）。

这里只做压缩后的描述：每次调用一行（工具名 + 关键参数 + 成败 / 审批），不带工具结果原文；
提示词里写明「执行过程本身不要写进记忆」，操作记录只用来理解决策与依据。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

#: 最多列多少条操作；更多时保留开头几条和最后几条，中间写一句省略
MAX_ACTIONS = 12
_HEAD_ACTIONS = 4
#: 关键参数、失败原因的展示长度
_ARG_CHARS = 80
_ERROR_CHARS = 60

#: 文件类工具：关键参数是路径
_PATH_KEYS = ("file_path", "path", "pattern")
#: 写入 / 删除类工具：单独汇总成「本轮改动的文件」
_WRITE_TOOLS = frozenset({"write_file", "edit_file", "delete"})
#: 规划类工具：只是待办列表，不列入操作
_SKIP_TOOLS = frozenset({"write_todos"})


def clip_middle(text: str | None, limit: int, tail: int = 600) -> str:
    """超过 ``limit`` 字时保留开头和结尾 ``tail`` 字，中间写明省略了多少字。

    回复的结论在开头，「下一步 / 待确认」常在结尾，只截开头会丢后者。
    """
    text = text or ""
    if len(text) <= limit:
        return text
    tail = max(0, min(tail, limit // 2))
    marker = "\n…（中间省略 {n} 字）…\n"
    head_len = max(0, limit - tail - len(marker.format(n=len(text))))
    omitted = len(text) - head_len - tail
    return text[:head_len] + marker.format(n=omitted) + (text[-tail:] if tail else "")


def _short(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _arguments(record: Mapping[str, Any]) -> dict:
    raw = record.get("arguments")
    if isinstance(raw, Mapping):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {"_raw": raw}
        return dict(parsed) if isinstance(parsed, Mapping) else {"_raw": raw}
    return {}


def _key_argument(name: str, args: dict) -> str:
    for key in _PATH_KEYS:
        if args.get(key):
            return _short(args[key], _ARG_CHARS)
    if name == "execute" and args.get("command"):
        return f"`{_short(args['command'], _ARG_CHARS)}`"
    if name == "task":
        kind = args.get("subagent_type") or ""
        return _short(f"{kind}：{args.get('description') or ''}".strip("："), _ARG_CHARS)
    if args.get("query"):
        return _short(args["query"], _ARG_CHARS)
    return _short(args, _ARG_CHARS) if args else ""


def _outcome(record: Mapping[str, Any]) -> str:
    status = record.get("approval_status")
    if status == "rejected":
        return "✗ 用户拒绝"
    if status == "pending":
        return "… 等待审批"
    approved = "（用户批准）" if status == "approved" else ""
    if record.get("success") is False:
        error = _short(record.get("result") or "", _ERROR_CHARS)
        return f"✗ 失败{approved}" + (f"：{error}" if error else "")
    return f"✓{approved}"


def describe_turn_actions(records: Iterable[Mapping[str, Any]] | None) -> str:
    """把本轮工具调用写成几行摘要；没有可列的操作时返回空串。

    ``records`` 是 ``services/streaming/messages.tool_call_records_to_dicts`` 的输出
    （tool_name / arguments / result / success / approval_status）。
    """
    lines: list[str] = []
    changed: list[str] = []
    for record in records or ():
        name = str(record.get("tool_name") or "").strip()
        if not name or name in _SKIP_TOOLS:
            continue
        args = _arguments(record)
        key = _key_argument(name, args)
        line = f"- {name}" + (f" {key}" if key else "") + f" {_outcome(record)}"
        if lines and lines[-1].split(" ×")[0] == line:
            # 同一操作连续重复：合并成一行并计数
            base, _, count = lines[-1].partition(" ×")
            lines[-1] = f"{base} ×{int(count or 1) + 1}"
        else:
            lines.append(line)
        if name in _WRITE_TOOLS and record.get("success") is not False and record.get(
            "approval_status"
        ) not in ("rejected", "pending"):
            path = args.get("file_path") or args.get("path")
            if path and path not in changed:
                changed.append(str(path))
    if not lines:
        return ""
    if len(lines) > MAX_ACTIONS:
        tail = MAX_ACTIONS - _HEAD_ACTIONS
        omitted = len(lines) - MAX_ACTIONS
        lines = [*lines[:_HEAD_ACTIONS], f"- …（中间省略 {omitted} 条）", *lines[-tail:]]
    if changed:
        lines.append("本轮改动的文件：" + "、".join(changed[:8]) + ("等" if len(changed) > 8 else ""))
    return "\n".join(lines)
