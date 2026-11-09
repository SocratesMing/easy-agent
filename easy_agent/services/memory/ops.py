"""记忆的增量更新：模型只给出修改指令（新增 / 修改 / 删除哪几条），由程序按指令改写。

改造前每次整理都让模型把整份记忆重写一遍：没变的条目也要原样抄一遍，抄着抄着就走样、
丢条，改了什么也查不出来；模型认为「没什么要改」时也会重写，记忆文件每轮都在变。

现在把当前记忆按条编号交给模型（``- [3] 数据库统一用 MySQL —— …``），模型只输出一个 JSON：

    {"add": [{"section": "偏好与约定", "text": "…"}], "update": [{"id": 3, "text": "…"}], "delete": [5]}

没动到的条目一字不改；什么都不用改时（三个列表都为空）记忆文件保持不变。

兼容：模型没按 JSON 回答、而是给了整份 Markdown 时（外部提示词目录里的旧模板就是这样要求的），
按旧逻辑当成整份重写，只把编号去掉；以 ``{`` 开头却解析不出来的回答按「无法应用」处理，
不改记忆，免得把一段坏 JSON 当成记忆写进去。
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from easy_agent.services.memory.entries import DEFAULT_CATEGORY, extract_title, parse_markdown

_FENCE_RE = re.compile(r"^```[A-Za-z]*\s*|\s*```\s*$")
_ID_PREFIX_RE = re.compile(r"^\s*\[\s*(\d+)\s*\]\s*")
_BULLET_ID_RE = re.compile(r"^(\s*(?:[-*+•]|\d+[.)、])\s+)\[\s*\d+\s*\]\s*", re.MULTILINE)
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*+•]|\d+[.)、])\s+")


@dataclass
class MemoryItem:
    section: str
    text: str


@dataclass
class MemoryOps:
    add: list[tuple[str, str]] = field(default_factory=list)
    update: list[tuple[int, str]] = field(default_factory=list)
    delete: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class MemoryChange:
    """一次整理的结果：``markdown`` 为 ``None`` 表示记忆不变（不写文件）。"""

    markdown: str | None
    #: ``"ops"`` 增量修改 / ``"rewrite"`` 整份重写（旧格式回答）/ ``"invalid"`` 无法应用
    mode: str
    summary: str = ""


def parse_memory(markdown: str) -> list[MemoryItem]:
    """把记忆 Markdown 拆成按顺序排列的条目（小节 + 正文），沿用结构化记忆的解析规则。"""
    return [
        MemoryItem(section=entry.category or DEFAULT_CATEGORY, text=entry.value)
        for entry in parse_markdown(markdown or "")
    ]


def annotate_for_prompt(markdown: str) -> str:
    """当前记忆按小节列出、每条带编号，供模型引用；没有条目时返回空串。"""
    lines: list[str] = []
    section = None
    for index, item in enumerate(parse_memory(markdown), 1):
        if item.section != section:
            if lines:
                lines.append("")
            lines.append(f"## {item.section}")
            section = item.section
        lines.append(f"- [{index}] {item.text}")
    return "\n".join(lines)


def strip_entry_ids(markdown: str) -> str:
    """整份重写的回答里去掉模型照抄的条目编号（``- [3] …`` → ``- …``）。"""
    return _BULLET_ID_RE.sub(r"\1", markdown or "")


def _clean_text(text) -> str:
    value = " ".join(str(text or "").split())
    value = _BULLET_PREFIX_RE.sub("", value)
    return _ID_PREFIX_RE.sub("", value).strip()


def _as_id(value) -> int | None:
    match = re.fullmatch(r"\s*\[?\s*(\d+)\s*\]?\s*", str(value))
    return int(match.group(1)) if match else None


def parse_ops(text: str) -> MemoryOps | None:
    """解析模型输出的修改指令；不是指令 JSON 时返回 ``None``。"""
    raw = _FENCE_RE.sub("", (text or "").strip()).strip()
    if not raw.startswith("{"):
        return None
    try:
        data = json.loads(raw[: raw.rfind("}") + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or not set(data) & {"add", "update", "delete"}:
        return None

    ops = MemoryOps()
    for item in data.get("add") or []:
        if isinstance(item, dict):
            ops.add.append((str(item.get("section") or "").strip(" #"), _clean_text(item.get("text"))))
        elif isinstance(item, str):
            ops.add.append(("", _clean_text(item)))
    for item in data.get("update") or []:
        if isinstance(item, dict) and _as_id(item.get("id")) is not None:
            ops.update.append((_as_id(item.get("id")), _clean_text(item.get("text"))))
    for item in data.get("delete") or []:
        if _as_id(item) is not None:
            ops.delete.append(_as_id(item))
    return ops


def apply_ops(
    items: Sequence[MemoryItem], ops: MemoryOps, *, sections: Sequence[str]
) -> tuple[list[MemoryItem], dict[str, int]]:
    """按指令改写条目；返回 (新条目, 实际生效的 新增 / 修改 / 删除 条数)。

    编号从 1 开始、与 :func:`annotate_for_prompt` 一致；越界编号忽略。新增条目的小节不在
    ``sections`` 里、也不是现有小节时，归入 ``sections[0]``；与已有条目完全相同的新增跳过。
    """
    work: list[MemoryItem | None] = [MemoryItem(item.section, item.text) for item in items]
    counts = {"added": 0, "updated": 0, "deleted": 0}

    for item_id, text in ops.update:
        if 1 <= item_id <= len(work) and work[item_id - 1] is not None:
            if not text:
                work[item_id - 1] = None
                counts["deleted"] += 1
            elif text != work[item_id - 1].text:
                work[item_id - 1].text = text
                counts["updated"] += 1
    for item_id in ops.delete:
        if 1 <= item_id <= len(work) and work[item_id - 1] is not None:
            work[item_id - 1] = None
            counts["deleted"] += 1

    result = [item for item in work if item is not None]
    known = {item.section for item in result} | set(sections)
    existing = {item.text.casefold() for item in result}
    for section, text in ops.add:
        if not text or text.casefold() in existing:
            continue
        result.append(MemoryItem(section if section in known else sections[0], text))
        existing.add(text.casefold())
        counts["added"] += 1
    return result, counts


def render_memory(items: Iterable[MemoryItem], *, title: str, sections: Sequence[str]) -> str:
    """渲染回 Markdown：规定的小节按规定顺序在前，其余小节（旧格式遗留）按出现顺序在后。"""
    grouped: dict[str, list[str]] = {}
    for item in items:
        grouped.setdefault(item.section, []).append(item.text)
    order = [name for name in sections if name in grouped] + [
        name for name in grouped if name not in sections
    ]
    lines = [f"# {title}", ""] if title else []
    for name in order:
        lines.append(f"## {name}")
        lines.extend(f"- {text}" for text in grouped[name])
        lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + "\n" if lines else ""


def fit_to_budget(
    markdown: str, *, title: str, sections: Sequence[str], max_chars: int
) -> tuple[str, int]:
    """超出上限时按条整条丢弃，直到放得下；返回 (Markdown, 丢弃条数)。

    先丢最长的条目：模型精简过一轮仍超限时，剩下的长条目多是实现细节（参数、默认值、
    具体写法），用户的偏好、约束和关键决策通常一句话就写完了。不截半句、不加省略说明，
    小节标题不变。
    """
    items = parse_memory(markdown)
    title = extract_title(markdown, title) or title
    dropped = 0
    rendered = render_memory(items, title=title, sections=sections)
    while items and len(rendered) > max_chars:
        longest = max(range(len(items)), key=lambda i: (len(items[i].text), i))
        items.pop(longest)
        dropped += 1
        rendered = render_memory(items, title=title, sections=sections)
    return rendered, dropped


def apply_compaction(reply: str, markdown: str, *, title: str, sections: Sequence[str]) -> MemoryChange:
    """应用「精简」回答：只认修改 / 删除指令，忽略新增；不是指令时不改（不接受整份重写）。"""
    ops = parse_ops(reply)
    if ops is None:
        return MemoryChange(None, "invalid", "精简回答不是修改指令")
    ops.add = []
    items, counts = apply_ops(parse_memory(markdown), ops, sections=sections)
    summary = f"修改 {counts['updated']} 条，删除 {counts['deleted']} 条"
    if not counts["updated"] and not counts["deleted"]:
        return MemoryChange(None, "ops", summary)
    title = extract_title(markdown, title) or title
    return MemoryChange(render_memory(items, title=title, sections=sections), "ops", summary)


def apply_memory_response(
    response: str, current_markdown: str, *, title: str, sections: Sequence[str]
) -> MemoryChange:
    """把模型的回答变成新的记忆 Markdown（``None`` = 不改）。"""
    ops = parse_ops(response)
    if ops is None:
        raw = _FENCE_RE.sub("", (response or "").strip()).strip()
        if raw.startswith("{"):
            return MemoryChange(None, "invalid", "回答像 JSON 但解析不出修改指令")
        return MemoryChange(strip_entry_ids(response), "rewrite", "整份重写（回答不是修改指令）")

    items, counts = apply_ops(parse_memory(current_markdown), ops, sections=sections)
    summary = f"新增 {counts['added']} 条，修改 {counts['updated']} 条，删除 {counts['deleted']} 条"
    if not any(counts.values()):
        return MemoryChange(None, "ops", summary)
    markdown = render_memory(
        items, title=extract_title(current_markdown, title) or title, sections=sections
    )
    return MemoryChange(markdown, "ops", summary)
