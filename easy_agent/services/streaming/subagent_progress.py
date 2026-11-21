"""子智能体（SubAgent）实时进度提取器。

开启 LangGraph ``subgraphs=True`` 后，子图事件以 ``(namespace, mode, data)``
三元组到达。本模块只从 ``updates`` 帧中蒸馏轻量级结构化进度（调用工具、完成
工具、整理结果），既让前端实时展示子智能体执行轨迹，又彻底隔离其原始流式输出，
避免混入主代理回复正文。
"""

from __future__ import annotations

import inspect
import time
from typing import Any

from easy_agent.services.streaming.sse import SSEEvent

MAX_SUBAGENT_STEPS = 20


def is_subagent_progress_event(event: Any) -> bool:
    """判断事件是否为子智能体进度帧（避免在编排器内直接引用 SSEEvent 常量）。"""
    return isinstance(event, dict) and event.get("type") == SSEEvent.SUBAGENT_PROGRESS


def astream_supports_subgraphs(astream_fn: Any) -> bool:
    """检查 ``agent.astream`` 是否接受 ``subgraphs`` 参数（兼容最小单测替身）。"""
    try:
        sig = inspect.signature(astream_fn)
    except (TypeError, ValueError):
        return False
    for param in sig.parameters.values():
        if param.kind == inspect.Parameter.VAR_KEYWORD or param.name == "subgraphs":
            return True
    return False


def _clip(text: Any, limit: int = 48) -> str:
    first = str(text or "").strip().splitlines()[0] if str(text or "").strip() else ""
    return first[:limit] + "…" if len(first) > limit else first


def _basename(path: Any) -> str:
    parts = [p for p in str(path or "").split("/") if p]
    return parts[-1] if parts else ""


def summarize_subagent_tool(tool_name: str, args: dict[str, Any] | None) -> str:
    """把子智能体内部工具调用格式化为简短中文进度文案。"""
    a = args if isinstance(args, dict) else {}
    name = str(tool_name or "").strip()
    if name == "execute":
        cmd = _clip(a.get("command"), 44)
        return f"执行 {cmd}" if cmd else "执行命令"
    if name in ("read_file", "read_uploaded_file"):
        fp = _basename(a.get("file_path") or a.get("path"))
        return f"读取 {fp}" if fp else "读取文件"
    if name == "write_file":
        fp = _basename(a.get("file_path") or a.get("path"))
        return f"写入 {fp}" if fp else "写入文件"
    if name == "edit_file":
        fp = _basename(a.get("file_path") or a.get("path"))
        return f"编辑 {fp}" if fp else "编辑文件"
    if name == "delete":
        fp = _basename(a.get("file_path") or a.get("path"))
        return f"删除 {fp}" if fp else "删除文件"
    if name in ("ls", "glob", "grep"):
        target = _clip(a.get("pattern") or a.get("path"), 36)
        return f"搜索 {target}" if target else "搜索文件"
    if name == "web_search":
        q = _clip(a.get("query"), 36)
        return f"联网搜索 {q}" if q else "联网搜索"
    if name == "access_knowledge_original":
        return "查阅知识库"
    if name == "write_todos":
        return "更新任务清单"
    return f"调用 {name}" if name else "执行工具"


def _extract_messages(node_output: Any) -> list[Any]:
    """从 LangGraph ``updates`` 节点输出中取出消息列表（兼容 Overwrite 包装）。"""
    if not isinstance(node_output, dict):
        return []
    raw = node_output.get("messages")
    if raw is None:
        return []
    if hasattr(raw, "value"):
        raw = raw.value
    if isinstance(raw, list):
        return raw
    return [raw]


def _message_role_kind(msg: Any) -> str:
    msg_type = getattr(msg, "type", None)
    if isinstance(msg_type, str):
        return msg_type.lower()
    if isinstance(msg, dict):
        return str(msg.get("type") or msg.get("role") or "").lower()
    return type(msg).__name__.lower()


class SubagentProgressTracker:
    """将子图 ``updates`` 蒸馏为 ``subagent_progress`` 事件并挂到父 ``task`` block 上。"""

    def __init__(self) -> None:
        # call_id -> {subagent_type, started_at, step, steps}
        self._active: dict[str, dict[str, Any]] = {}
        # namespace_root -> call_id
        self._ns_to_call: dict[str, str] = {}

    def register_task(self, call_id: str, subagent_type: str = "general-purpose") -> None:
        if not call_id:
            return
        self._active[call_id] = {
            "subagent_type": subagent_type or "general-purpose",
            "started_at": time.monotonic(),
            "step": 0,
            "steps": [],
        }

    def complete_task(self, call_id: str) -> tuple[str, float] | None:
        info = self._active.pop(call_id, None)
        if info is None:
            return None
        for ns, mapped in list(self._ns_to_call.items()):
            if mapped == call_id:
                self._ns_to_call.pop(ns, None)
        return str(info["subagent_type"]), time.monotonic() - float(info["started_at"])

    def _resolve_call_id(self, namespace: tuple[Any, ...]) -> str | None:
        if not self._active:
            return None
        root = str(namespace[0]) if namespace else ""
        if root and root in self._ns_to_call:
            mapped = self._ns_to_call[root]
            if mapped in self._active:
                return mapped
            self._ns_to_call.pop(root, None)
        # 优先绑定到尚未映射 namespace 的最早活跃 task（支持并行子智能体）
        assigned = set(self._ns_to_call.values())
        for cid in self._active:
            if cid not in assigned:
                if root:
                    self._ns_to_call[root] = cid
                return cid
        # 兜底：绑定到最近活跃 task
        cid = next(reversed(self._active))
        if root:
            self._ns_to_call[root] = cid
        return cid

    def handle_subgraph_event(
        self,
        namespace: tuple[Any, ...],
        mode: str,
        data: Any,
        proc: Any = None,
    ) -> list[dict[str, Any]]:
        """消费一条子图事件，返回要推给前端的 ``subagent_progress`` 事件列表。"""
        if mode != "updates" or not isinstance(data, dict):
            return []
        call_id = self._resolve_call_id(namespace)
        if not call_id:
            return []
        info = self._active[call_id]
        sub_type = info["subagent_type"]
        events: list[dict[str, Any]] = []

        for node_output in data.values():
            for msg in _extract_messages(node_output):
                kind = _message_role_kind(msg)
                if kind in ("ai", "aimessage", "assistant"):
                    tool_calls = (
                        getattr(msg, "tool_calls", None)
                        if not isinstance(msg, dict)
                        else msg.get("tool_calls")
                    ) or []
                    if tool_calls:
                        for tc in tool_calls:
                            tname = (
                                tc.get("name", "")
                                if isinstance(tc, dict)
                                else getattr(tc, "name", "")
                            )
                            targs = (
                                tc.get("args")
                                if isinstance(tc, dict)
                                else getattr(tc, "args", None)
                            )
                            if not tname:
                                continue
                            info["step"] += 1
                            summary = summarize_subagent_tool(tname, targs)
                            ev = {
                                "type": SSEEvent.SUBAGENT_PROGRESS,
                                "tool_call_id": call_id,
                                "subagent_type": sub_type,
                                "stage": "tool_call",
                                "tool_name": str(tname),
                                "summary": summary,
                                "step": info["step"],
                            }
                            events.append(ev)
                            self._record_step(info, ev)
                    else:
                        content = (
                            getattr(msg, "content", "")
                            if not isinstance(msg, dict)
                            else msg.get("content", "")
                        )
                        if content and (
                            not info["steps"]
                            or info["steps"][-1].get("stage") != "summary"
                        ):
                            info["step"] += 1
                            ev = {
                                "type": SSEEvent.SUBAGENT_PROGRESS,
                                "tool_call_id": call_id,
                                "subagent_type": sub_type,
                                "stage": "summary",
                                "tool_name": "",
                                "summary": "整理子任务结果",
                                "success": True,
                                "step": info["step"],
                            }
                            events.append(ev)
                            self._record_step(info, ev)
                elif kind in ("tool", "toolmessage"):
                    tname = (
                        getattr(msg, "name", "")
                        if not isinstance(msg, dict)
                        else msg.get("name", "")
                    )
                    status = (
                        getattr(msg, "status", None)
                        if not isinstance(msg, dict)
                        else msg.get("status")
                    )
                    success = status != "error"
                    ev = {
                        "type": SSEEvent.SUBAGENT_PROGRESS,
                        "tool_call_id": call_id,
                        "subagent_type": sub_type,
                        "stage": "tool_result",
                        "tool_name": str(tname or ""),
                        "summary": summarize_subagent_tool(str(tname or ""), None),
                        "success": success,
                        "step": info["step"],
                    }
                    events.append(ev)
                    self._record_step(info, ev)

        if events and proc is not None:
            self._sync_to_parent_block(proc, call_id, info["steps"])
        return events

    def _record_step(self, info: dict[str, Any], ev: dict[str, Any]) -> None:
        steps: list[dict[str, Any]] = info["steps"]
        stage = ev["stage"]
        if stage == "tool_result":
            # 回填最近一条同名且仍在运行中的 tool_call，避免同一工具占两行
            for item in reversed(steps):
                if (
                    item.get("stage") == "tool_call"
                    and item.get("success") is None
                    and (not ev["tool_name"] or item.get("tool_name") == ev["tool_name"])
                ):
                    item["success"] = ev.get("success", True)
                    item["stage"] = "tool_result"
                    return
        steps.append(
            {
                "stage": stage,
                "tool_name": ev.get("tool_name", ""),
                "summary": ev.get("summary", ""),
                "success": ev.get("success") if stage != "tool_call" else None,
                "step": ev.get("step", 0),
            }
        )
        if len(steps) > MAX_SUBAGENT_STEPS:
            del steps[: len(steps) - MAX_SUBAGENT_STEPS]

    @staticmethod
    def _sync_to_parent_block(
        proc: Any, call_id: str, steps: list[dict[str, Any]]
    ) -> None:
        blocks = getattr(proc, "blocks", None)
        if not isinstance(blocks, list):
            return
        for block in reversed(blocks):
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_call"
                and (block.get("tool_call_id") == call_id or block.get("id") == call_id)
            ):
                block["subagent_steps"] = [dict(s) for s in steps]
                return
