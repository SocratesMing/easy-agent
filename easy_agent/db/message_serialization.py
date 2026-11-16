"""会话消息的序列化 / sanitize / JSON 修复（T3：从 repositories/sessions.py 纯移动）。

数据库读写仍在 ``db/repositories/sessions.py`` 与 ``db/session_messages.py`` 里；
这里只放与数据库连接无关的纯函数。拆出来是为了打断
``repositories.sessions`` ↔ ``session_messages`` 的模块级循环导入：两个模块都
依赖本模块，import_graph 的 SCC 门禁要求 ``easy_agent`` 内 0 个强连通分量。

函数签名与迁移前一致（``db`` 参数保留但当前实现不使用），A2 的消息 sanitize
语义、长度上限与 JSON 修复策略都没有改动。
"""

from __future__ import annotations

import json


def _sanitize_message_for_storage(db, message: dict) -> dict:
    # 不做内容截断：历史上下文需要完整回灌给模型，落库时截断会导致
    # 下一轮上下文 token 明显缩水（实测 50% → 36%）。
    # 列类型 MySQL 为 MEDIUMTEXT(16MB)、SQLite 为 TEXT，足以容纳完整内容；
    # 上下文体积改由 SummarizationMiddleware 在模型调用前按 token 阈值压缩。
    sanitized = {
        "role": message.get("role", ""),
        "content": message.get("content", "") or "",
        "timestamp": message.get("timestamp", ""),
    }
    if message.get("thinking"):
        sanitized["thinking"] = message["thinking"]
    if message.get("thinking_duration") is not None:
        sanitized["thinking_duration"] = message["thinking_duration"]
    if message.get("usage"):
        sanitized["usage"] = message["usage"]
    if message.get("error"):
        sanitized["error"] = str(message["error"])
    for key in ("knowledge_evidence", "knowledge_warnings"):
        if isinstance(message.get(key), list):
            sanitized[key] = message[key]
    if message.get("tool_calls"):
        # 与 _sanitize_extra_data 对齐：不再截断为前 20 条。
        # HITL 恢复后新增的工具调用位于列表尾部，截断会导致历史会话中
        # HITL 之后的工具调用整体丢失。
        sanitized["tool_calls"] = [
            {
                "tool_name": tc.get("tool_name", "") or tc.get("name", ""),
                "tool_call_id": tc.get("tool_call_id", ""),
                "arguments": tc.get("arguments", {}),
                "result": str(tc.get("result", "")),
                "success": tc.get("success", True),
                "duration": tc.get("duration"),
                "step": tc.get("step", 0),
                "approval_status": tc.get("approval_status"),
            }
            for tc in message["tool_calls"][:1000]
        ]
    if message.get("blocks"):
        # 分离 content blocks 和其他 blocks，确保 content 不被截断
        content_blocks = [
            b for b in message["blocks"] if b.get("type") == "content"
        ]
        other_blocks = [b for b in message["blocks"] if b.get("type") != "content"]

        # 保留全部非 content blocks（原先仅保留前 30 个）：
        # 长会话/HITL 恢复后产生的 thinking/tool_call 块 order 靠后，
        # 截断会导致「切回历史会话时 HITL 之后的工具与思考过程不显示」。
        # sessions.messages 列为 MEDIUMTEXT(16MB)，且各块 result/content 已做长度裁剪。
        sanitized_blocks = []
        for b in other_blocks[:1000] + content_blocks:
            block_type = b.get("type", "")
            s = {"type": block_type, "order": b.get("order", 0)}
            if block_type == "thinking":
                s["content"] = b.get("content", "") or ""
                s["duration"] = b.get("duration")
                s["step"] = b.get("step", 0)
            elif block_type == "tool_call":
                s["tool_name"] = b.get("tool_name", "")
                s["tool_call_id"] = b.get("tool_call_id", "")
                s["arguments"] = b.get("arguments", {})
                s["result"] = str(b.get("result", ""))
                s["success"] = b.get("success", True)
                s["duration"] = b.get("duration")
                s["step"] = b.get("step", 0)
                # 保留 HITL 审批标记：历史会话显示「已批准/已拒绝/待审批」，
                # 且恢复流依赖 approval_status == "pending" 定位待审批工具。
                if b.get("approval_status"):
                    s["approval_status"] = b["approval_status"]
                if b.get("pending_approval"):
                    s["pending_approval"] = True
                if b.get("file_paths"):
                    s["file_paths"] = b["file_paths"]
            elif block_type == "content":
                s["content"] = b.get("content", "") or ""
                s["step"] = b.get("step", 0)
            else:
                s["content"] = b.get("content", "") or ""
            sanitized_blocks.append(s)
        # 按 order 排序恢复原始顺序
        sanitized_blocks.sort(key=lambda x: x.get("order", 0))
        sanitized["blocks"] = sanitized_blocks
    if message.get("pending_approval"):
        # HITL 待审批信息（thread_id + 操作详情），供历史会话恢复审批 UI 与继续执行
        sanitized["pending_approval"] = message["pending_approval"]
    return sanitized


def _sanitize_blocks(db, blocks: list) -> list:
    """规范化 blocks：分离 content 与其他块、按类型挑选字段、按 order 还原顺序。

    content 块不计入数量上限，避免正文被截断。
    """
    # 分离 content blocks 和其他 blocks，确保 content 不被截断
    content_blocks = []
    other_blocks = []
    for b in blocks:
        if b.get("type") == "content":
            content_blocks.append(b)
        else:
            other_blocks.append(b)

    # 保留全部非 content blocks（不再限制数量）：
    # 长会话/多次 HITL 会产生数十至上百个 tool_call/thinking 块，
    # 限制数量会导致后期（最晚出现的）块在历史记录中整体丢失。
    # 列类型为 MEDIUMTEXT(16MB)，配合下方 max_len 渐进式裁剪控制体积。
    max_other = 1000
    truncated_other = other_blocks[:max_other]

    sanitized_blocks = []
    for b in truncated_other + content_blocks:
        block_type = b.get("type", "")
        sanitized = {"type": block_type, "order": b.get("order", 0)}
        if block_type == "thinking":
            sanitized["content"] = b.get("content", "") or ""
            sanitized["duration"] = b.get("duration")
            sanitized["step"] = b.get("step", 0)
        elif block_type == "tool_call":
            sanitized["tool_name"] = b.get("tool_name", "")
            sanitized["tool_call_id"] = b.get("tool_call_id", "")
            sanitized["arguments"] = b.get("arguments", {})
            sanitized["result"] = str(b.get("result", ""))
            sanitized["success"] = b.get("success", True)
            sanitized["duration"] = b.get("duration")
            sanitized["step"] = b.get("step", 0)
            # 保留 HITL 审批标记，使历史会话也能显示「已批准/已拒绝/待审批」
            if b.get("approval_status"):
                sanitized["approval_status"] = b["approval_status"]
            if b.get("pending_approval"):
                sanitized["pending_approval"] = True
            if b.get("file_paths"):
                sanitized["file_paths"] = b["file_paths"]
        elif block_type == "content":
            sanitized["content"] = b.get("content", "") or ""
        else:
            sanitized["content"] = b.get("content", "") or ""
        sanitized_blocks.append(sanitized)
    # 按 order 排序恢复原始顺序
    sanitized_blocks.sort(key=lambda x: x.get("order", 0))
    return sanitized_blocks


def _sanitize_tool_calls(db, tool_calls: list) -> list:
    """规范化 tool_calls 字段（最多保留 1000 条）。"""
    return [
        {
            "tool_name": tc.get("tool_name", "") or tc.get("name", ""),
            "tool_call_id": tc.get("tool_call_id", ""),
            "arguments": tc.get("arguments", {}),
            "result": str(tc.get("result", "")),
            "success": tc.get("success", True),
            "duration": tc.get("duration"),
            "step": tc.get("step", 0),
            "approval_status": tc.get("approval_status"),
        }
        for tc in tool_calls[:1000]
    ]


_MAX_EXTRA_DATA_CHARS = 5_000_000


_TRIM_LIMITS = {"tool_call": ("result", 200), "thinking": ("content", 200), "content": ("content", 500)}


_STRIP_LIMITS = {"tool_call": ("result", 100), "thinking": ("content", 100), "content": ("content", 200)}


def _trim_long_fields(extra: dict) -> bool:
    """把块、工具调用结果和 thinking 的长字段截到固定长度，返回是否截断过。"""
    trimmed = False
    blocks = extra.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            rule = _TRIM_LIMITS.get(block.get("type"))
            if rule is None:
                continue
            field, limit = rule
            value = str(block.get(field, ""))
            if len(value) > limit:
                block[field] = value[:limit]
                trimmed = True
    tool_calls = extra.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            value = str(tc.get("result", ""))
            if len(value) > 200:
                tc["result"] = value[:200]
                trimmed = True
    if "thinking" in extra and len(str(extra["thinking"])) > 200:
        extra["thinking"] = str(extra["thinking"])[:200]
        trimmed = True
    return trimmed


def _head(value, limit: int) -> str:
    return str(value)[:limit] + "..." if value else ""


def _strip_to_metadata(extra: dict) -> None:
    """最终兜底：清空参数、长字段只留开头，保留 blocks / tool_calls 的结构
    （tool_name、success、duration 等元信息）。"""
    blocks = extra.get("blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if block.get("type") == "tool_call":
                block["arguments"] = {}
            rule = _STRIP_LIMITS.get(block.get("type"))
            if rule is not None:
                field, limit = rule
                block[field] = _head(block.get(field), limit)
    tool_calls = extra.get("tool_calls")
    if isinstance(tool_calls, list):
        for tc in tool_calls:
            tc["arguments"] = {}
            tc["result"] = _head(tc.get("result"), 100)


def _sanitize_extra_data(db, message: dict) -> str | None:
    extra_keys = {
        k: v
        for k, v in message.items()
        if k not in ("role", "content", "timestamp")
    }
    if not extra_keys:
        return None

    if isinstance(extra_keys.get("blocks"), list):
        extra_keys["blocks"] = _sanitize_blocks(db, extra_keys["blocks"])
    if isinstance(extra_keys.get("tool_calls"), list):
        extra_keys["tool_calls"] = _sanitize_tool_calls(db, extra_keys["tool_calls"])

    # thinking 不截断：完整思考用于下一轮上下文；超大时由下方的裁剪兜底
    result = json.dumps(extra_keys, ensure_ascii=False)
    if len(result) <= _MAX_EXTRA_DATA_CHARS:
        return result

    # 先截断长字段（不直接删除 blocks / tool_calls）；截断一次后不会再有可截的内容
    if _trim_long_fields(extra_keys):
        result = json.dumps(extra_keys, ensure_ascii=False)
        if len(result) <= _MAX_EXTRA_DATA_CHARS:
            return result

    _strip_to_metadata(extra_keys)
    return json.dumps(extra_keys, ensure_ascii=False)


def try_repair_json(raw: str) -> dict | None:
    if not raw:
        return None

    def _scan_structural_ends(text: str) -> list[int]:
        ends = []
        in_str = False
        esc = False
        for i, ch in enumerate(text):
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch in ("}", "]"):
                ends.append(i)
        return ends

    def _close_brackets(candidate: str) -> dict | None:
        in_str = False
        esc = False
        stack = []
        for ch in candidate:
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch in ("{", "["):
                stack.append(ch)
            elif ch in ("}", "]"):
                if stack:
                    stack.pop()
        for b in reversed(stack):
            candidate += "}" if b == "{" else "]"
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            return None

    blocks_start = raw.find('"blocks": [')
    if blocks_start > 0:
        search_area = raw[: len(raw) - 200]
        last_boundary = search_area.rfind('}, {"type"')
        if last_boundary > blocks_start:
            truncated = raw[: last_boundary + 1]
            result = _close_brackets(truncated)
            if result is not None:
                return result

    structural_ends = _scan_structural_ends(raw)
    for pos in reversed(structural_ends):
        candidate = raw[: pos + 1]
        result = _close_brackets(candidate)
        if result is not None:
            return result

    return None


__all__ = [
    "_MAX_EXTRA_DATA_CHARS",
    "_STRIP_LIMITS",
    "_TRIM_LIMITS",
    "_head",
    "_sanitize_blocks",
    "_sanitize_extra_data",
    "_sanitize_message_for_storage",
    "_sanitize_tool_calls",
    "_strip_to_metadata",
    "_trim_long_fields",
    "try_repair_json",
]
