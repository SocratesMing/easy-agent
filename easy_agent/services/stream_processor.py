"""Shared stream processor for DeepAgents hybrid streaming.

Consumes (mode, data) events from agent.astream(stream_mode=['messages','updates'])
and emits SSE event dicts. Unit-testable with synthetic messages (no agent loop).

- ``updates`` mode: authoritative tool_call / tool_result / token_usage from
  complete node outputs (model node AIMessage, tools node ToolMessage).
- ``messages`` mode: token-level thinking / content (typewriter UX).

Step semantics: one step per model turn. ``_step_advanced_this_turn`` is set when
a turn's thinking/AIMessage first advances the step, and reset when the tools
node's ToolMessage arrives (the turn boundary), so a turn that both thinks and
calls a tool counts as a single step. Each turn gets its own thinking card
(step-incrementing); reasoning that arrives in segments within one turn reopens
that turn's card instead of creating a duplicate (no within-step split).
"""
import json
import logging
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from ..model import extract_reasoning


def _truncate(text, limit: int) -> str:
    text = str(text or "")
    if len(text) > limit:
        return text[:limit] + "..."
    return text


def _fmt_tool_args(args) -> str:
    if not args:
        return "{}"
    try:
        return json.dumps(args, ensure_ascii=False)
    except Exception:
        return str(args)





def _is_error_result(text: str) -> bool:
    """Check if a tool result string indicates an error (DeepAgents plain-text errors)."""
    if not text:
        return False
    stripped = text.strip()
    if stripped.startswith("Error:") or stripped.startswith("Error "):
        return True
    if stripped.startswith("Cannot write to"):
        return True
    if "[Command failed with exit code" in stripped:
        return True
    stderr_match = re.search(r"\[stderr\]\s*(.+)", text, re.DOTALL)
    if stderr_match:
        stderr_lower = stderr_match.group(1).strip().lower()
        for pattern in (
            "permission denied", "cannot create directory", "can't cd to",
            "cannot cd to", "no such file", "not found", "command not found",
            "is not a directory", "is not a file", "access denied",
            "operation not permitted", "read-only",
        ):
            if pattern in stderr_lower:
                return True
    return False


def _is_reemitted_reasoning(rc: str, accumulated: str) -> bool:
    """Detect whether a reasoning chunk ``rc`` is a re-emission of reasoning
    already accumulated for the current turn (``accumulated``).

    Provider/LangGraph streams sometimes re-emit reasoning: either the full
    aggregate (``rc == accumulated``) or a large contiguous block already
    present earlier (prefix/suffix/middle). Appending such a chunk would
    duplicate the step's thinking on the frontend ("分两次渲染").
    A >50 char guard avoids false-positives on short legitimate repetition.
    """
    if not rc or not accumulated:
        return False
    if rc == accumulated:
        return True
    if len(rc) > 50 and rc in accumulated:
        return True
    return False


class StreamProcessor:
    def __init__(self, *, sid: str = "", current_step: int = 0,
                 blocks: list | None = None, db=None, session_id: str | None = None,
                 message_id: str = "", session_logger=None,
                 max_input_tokens: int | None = None,
                 auto_compress_tokens: int | None = None,
                 pre_session_tokens: int = 0, start_time: float | None = None,
                 result_log_truncate: int = 5000,
                 total_usage: dict | None = None,
                 last_context_tokens: int = 0,
                 accumulated_response: str = "",
                 accumulated_thinking: str | None = None):
        self.sid = sid
        self.current_step = current_step
        self.blocks = blocks if blocks is not None else []
        self.db = db
        self.session_id = session_id
        self.message_id = message_id
        self.session_logger = session_logger
        self.max_input_tokens = max_input_tokens
        self.auto_compress_tokens = auto_compress_tokens
        self.pre_session_tokens = pre_session_tokens
        self.start_time = start_time if start_time is not None else time.time()
        self.result_log_truncate = result_log_truncate

        self.is_in_thinking = False
        self.thinking_start_time = None
        self.thinking_step_start_len = len(self._thinking_text())
        self.accumulated_thinking = (
            accumulated_thinking if accumulated_thinking is not None
            else self._thinking_text()
        )
        self.accumulated_response = accumulated_response
        self.tool_call_start_times: dict[str, float] = {}
        self.total_usage = total_usage if total_usage is not None else {
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.last_context_tokens = last_context_tokens
        self._step_advanced_this_turn = False
        self._todo_emitted_for: set[str] = set()
        self._last_token_usage_time = 0.0
        # 每步统计：在 step 边界（_reset_step_stats）清零，用于「stepN 结束」汇总行。
        self._step_thinking_len = 0
        self._step_content_len = 0
        self._step_tool_count = 0
        self._step_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self._step_end_logged: set = set()

        # 累计思考耗时：同一 turn 内思考被分段（如 思考->工具调用->继续思考）时，
        # 保存上一段已结束的 duration，使该 turn 最终"用时"为各段之和。
        self._thinking_duration_acc = 0.0

    def _thinking_text(self) -> str:
        blk = next((b for b in self.blocks if b.get("type") == "thinking"), None)
        return blk.get("content", "") if blk else ""

    def _response_text(self) -> str:
        return ""

    def handle(self, mode: str, data: Any) -> list[dict]:
        if mode == "updates":
            return self._handle_updates(data)
        if mode == "messages":
            return self._handle_messages(data)
        return []

    def _handle_updates(self, data: dict) -> list[dict]:
        events = []
        for node, delta in (data.items() if isinstance(data, dict) else []):
            if not isinstance(delta, dict):
                continue
            for m in delta.get("messages", []):
                if isinstance(m, AIMessage):
                    events.extend(self._on_ai_message(m))
                elif isinstance(m, ToolMessage):
                    events.extend(self._on_tool_message(m))
        return events

    def _advance_step_for_turn(self) -> None:
        if not self._step_advanced_this_turn:
            self.current_step += 1
            self._step_advanced_this_turn = True

    def _end_thinking(self) -> list[dict]:
        """End the current thinking phase.

        Sets the duration on the *current* (last) thinking block, resets
        ``is_in_thinking`` and emits a ``thinking_end`` event. The block is
        located by type (not ``blocks[-1]``) so this stays correct even when a
        ``tool_call`` block was appended after the thinking block -- which is
        exactly what happens when the model thinks and then immediately calls a
        tool without emitting text content in between.

        When a single turn's thinking is split into several segments (e.g. the
        model emits reasoning, then a tool call, then more reasoning for the
        same turn), ``_thinking_duration_acc`` holds the previously ended
        segment's duration so the final value is the cumulative thinking time
        for that turn (see the reopen logic in ``_handle_messages``).

        Returns an empty list when not currently thinking (idempotent).
        """
        if not self.is_in_thinking:
            return []
        dur = (round(time.time() - self.thinking_start_time, 2) if self.thinking_start_time else 0) + self._thinking_duration_acc
        target = None
        for blk in reversed(self.blocks):
            if blk.get("type") == "thinking":
                target = blk
                target["duration"] = dur
                break
        self.is_in_thinking = False
        self._thinking_duration_acc = 0.0
        content = target.get("content", "") if target is not None else ""
        _content_len = len(content)
        self._step_thinking_len = _content_len
        # 合并「推理过程开始/结束」为一行：打印用时、长度与推理内容前 100 字预览。
        logger.info(
            "[%s] step%d 🧠 | 用时=%.2fs | 长度=%d字符 | 内容: %s",
            self.sid, self.current_step, dur, _content_len,
            _truncate(content, 100),
        )
        return [{"type": "thinking_end", "duration": dur, "step": self.current_step}]

    def _on_ai_message(self, m: AIMessage) -> list[dict]:
        events = []
        # Each model turn = one step (unless thinking already advanced it).
        # NOTE: do NOT reset _step_advanced_this_turn here -- the turn boundary
        # is marked by the subsequent ToolMessage (see _on_tool_message). Resetting
        # here would make late reasoning chunks (same turn, arriving after this
        # AIMessage) increment the step and create a duplicate thinking card
        # (within-step split).
        _prev_step = self.current_step
        self._advance_step_for_turn()
        if self.current_step != _prev_step:
            # 上一个 step（工具/思考）在下一个 model turn 开始时结束：先打印汇总再重置本步统计。
            self._log_step_end(_prev_step)
            self._reset_step_stats()
        # If the model was thinking and now emits tool calls (no text content in
        # between), close the thinking phase now. Otherwise is_in_thinking stays
        # True: subsequent turns' thinking gets appended to accumulated_thinking
        # but is lost from blocks, and the thinking block keeps duration=None
        # (frontend shows a perpetual "思考中" spinner and history loses records).
        events.extend(self._end_thinking())

        for tc in (m.tool_calls or []):
            tid = tc.get("id", "")
            name = tc.get("name", "")
            args = tc.get("args", {}) or {}
            self._step_tool_count += 1
            self.tool_call_start_times[tid] = time.time()
            self.blocks.append({
                "type": "tool_call", "tool_name": name, "tool_call_id": tid,
                "arguments": args, "result": "", "success": True,
                "duration": None, "step": self.current_step,
                "order": len(self.blocks),
            })
            events.append({"type": "tool_call", "tool_name": name,
                           "tool_call_id": tid, "arguments": args,
                           "step": self.current_step})
            events.extend(self._maybe_todo_from_args(name, tid, args))
        self.consume_usage_metadata(m)
        events.extend(self._emit_token_usage())
        return events

    def _on_tool_message(self, m: ToolMessage) -> list[dict]:
        # A tool result marks the end of a model turn: close any thinking phase
        # still open (e.g. late reasoning that reopened the card after the
        # AIMessage) and mark the turn done so the next reasoning starts a new
        # step. In the normal flow thinking was already ended by _on_ai_message,
        # so this is a no-op there.
        events = list(self._end_thinking())
        tid = m.tool_call_id
        name = m.name or "tool"
        result = str(m.content) if m.content else ""
        is_error = bool(getattr(m, "additional_kwargs", {}).get("is_error", False))
        if not is_error and result:
            is_error = _is_error_result(result)
        start = self.tool_call_start_times.pop(tid, None)
        duration = round(time.time() - start, 2) if start else 0
        args = {}
        for blk in reversed(self.blocks):
            if blk.get("type") == "tool_call" and blk.get("tool_call_id") == tid:
                blk["result"] = result
                blk["success"] = not is_error
                blk["duration"] = duration
                args = blk.get("arguments", {})
                break
        ev = {"type": "tool_result", "tool_name": name, "tool_call_id": tid,
              "arguments": args, "result": result, "success": not is_error,
              "duration": duration, "step": self.current_step}
        events.append(ev)
        events.extend(self._maybe_todo_from_result(name, tid, result))
        mark = "✅" if not is_error else "❌"
        logger.info(
            "[%s] step%d 🔧 %s %s | 参数: %s | 结果: %s | 耗时: %.2fs",
            self.sid, self.current_step, name, mark,
            _fmt_tool_args(args), _truncate(result, self.result_log_truncate), duration,
        )
        # 工具轮次不在此打印「step 结束」汇总：step 要等该 step 的「全部工具执行」之后，
        # 在下一个思考/正文开始、或 finalize 时才真正结束。否则「结束」会排在工具之前/中间。
        self._step_advanced_this_turn = False
        return events

    def _handle_messages(self, data) -> list[dict]:
        chunk, _metadata = data
        events = []
        if not isinstance(chunk, AIMessageChunk):
            return events
        rc = extract_reasoning(getattr(chunk, "additional_kwargs", {}))
        content = chunk.content or ""
        if rc:
            # 去重：当前 turn 的思考块已存在（_step_advanced_this_turn=True，即继续
            # 思考或重开同一 step）时，部分 provider/LangGraph 会在流末尾重放整段
            # reasoning（聚合块），其内容等于本 turn 已累积的思考。直接追加会使同一
            # step 的思考内容重复（前端表现为"分两次渲染"）。检测到整段重复时跳过。
            # 新 turn 的首段思考（_step_advanced_this_turn=False）不在此列，不会被误跳过。
            if self._step_advanced_this_turn:
                turn_thinking = self.accumulated_thinking[self.thinking_step_start_len:]
                # 命中条件：整段重复（聚合块等于本 turn 已累积思考），或大段重复块
                #（>50 字符，前缀/后缀/中段均算，避免短增量误伤）。均为
                # provider/LangGraph 流末尾重放，直接追加会让同一 step 的思考内容
                # 重复渲染。
                if _is_reemitted_reasoning(rc, turn_thinking):
                    logger.info(
                        "[%s] step%d 跳过重复 reasoning (rc_len=%d turn_len=%d)",
                        self.sid, self.current_step, len(rc), len(turn_thinking),
                    )
                    rc = ""
            if rc:
                reused = None
                if not self.is_in_thinking:
                    self.is_in_thinking = True
                    self.thinking_start_time = time.time()
                    _prev_step = self.current_step
                    self._advance_step_for_turn()
                    # 每个 model turn（step）一张思考卡片，按 step 递增存储。仅当当前
                    # step 已存在被结束的 thinking 块时复用它（同一 turn 内思考被分段：
                    # 如 思考->工具调用->继续思考，或 messages 模式的 reasoning 晚于
                    # updates 模式的 AIMessage 到达），累计耗时并继续追加内容--避免一个
                    # step 的思考被渲染成多张卡片。仅匹配当前 step，跨 turn 不合并。
                    reused = None
                    for blk in reversed(self.blocks):
                        if blk.get("type") == "thinking" and blk.get("step") == self.current_step:
                            reused = blk
                            break
                    if reused is not None:
                        self._thinking_duration_acc = reused.get("duration") or 0
                        # 不重置 duration=None：避免卡片在"思考过程"与"正在思考"间闪烁、
                        # 也避免同一步骤思考被视觉上分成两段。最终 _end_thinking 写入累计耗时。
                        # thinking_step_start_len 保留该块创建时的起点，使累积内容正确
                    else:
                        self._thinking_duration_acc = 0.0
                        self.thinking_step_start_len = len(self.accumulated_thinking)
                        # 进入新 step：先打印上一步汇总，再清空本步统计（供「stepN 结束」
                        # 汇总行按步重新累计）。(step 结束也会在工具边界/_on_tool_message
                        # 与 finalize 处打印，_step_end_logged 去重保证只打印一次)
                        if _prev_step != self.current_step:
                            self._log_step_end(_prev_step)
                        self._reset_step_stats()
                        self.blocks.append({"type": "thinking", "order": len(self.blocks),
                                            "content": "", "step": self.current_step, "duration": None})
                    # 仅在本轮思考「真正开始」时发送一次 thinking_start 事件（前端用），
                    # 日志改为在 _end_thinking 合并为一行（含推理内容预览），不在此单独打印。
                    events.append({"type": "thinking_start", "step": self.current_step})
            self.accumulated_thinking += rc
            step_content = ""
            for blk in reversed(self.blocks):
                if blk.get("type") == "thinking":
                    blk["content"] = self.accumulated_thinking[self.thinking_step_start_len:]
                    step_content = blk["content"]
                    break
            # 同时发送增量(content)与完整内容(full_content)：新前端用 full_content
            # 采用 SET 语义覆盖（幂等，重复/聚合增量也不会重复渲染）；旧前端仍 append
            # content 增量（依赖后端去重保证正确）。双字段兼容新旧前端缓存，避免
            # "后端发完整内容 + 旧前端 append" 导致的思考内容重复渲染。
            events.append({"type": "thinking", "content": rc,
                           "full_content": step_content, "step": self.current_step})
        if content and not getattr(chunk, "tool_call_chunks", None):
            events.extend(self._end_thinking())
            # 正文也按 step 计入 blocks（与思考/工具一致），持久化后历史会话能按
            # order 还原"思考->正文->工具"的真实顺序，而非把所有正文合并到最后。
            _prev_step = self.current_step
            self._advance_step_for_turn()
            if self.current_step != _prev_step:
                # 上一个 step（思考/工具）在本轮正文开始时结束：先打印其汇总，再清空本步统计。
                self._log_step_end(_prev_step)
                self._reset_step_stats()
            self.accumulated_response += content
            cblk = None
            for b in reversed(self.blocks):
                if b.get("type") == "content" and b.get("step") == self.current_step:
                    cblk = b
                    break
            if cblk is None:
                cblk = {"type": "content", "order": len(self.blocks),
                        "content": "", "step": self.current_step}
                self.blocks.append(cblk)
            cblk["content"] += content
            self._step_content_len += len(content)
            events.append({"type": "content", "content": content, "step": self.current_step})
        return events

    def _emit_token_usage(self) -> list[dict]:
        now = time.time()
        if now - self._last_token_usage_time < 0.3:
            return []
        self._last_token_usage_time = now
        inp = self.total_usage["input_tokens"]
        out = self.total_usage["output_tokens"]
        return [{
            "type": "token_usage",
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": inp + out,
            "session_estimate": self.pre_session_tokens + inp + out,
            "context_tokens": self.last_context_tokens if self.last_context_tokens > 0 else inp,
            "max_input_tokens": self.max_input_tokens,
            "auto_compress_tokens": self.auto_compress_tokens,
            "elapsed_time": round(time.time() - self.start_time, 2),
            "step_count": self.current_step,
        }]

    def _maybe_todo_from_args(self, name: str, tid: str, args) -> list[dict]:
        if name != "write_todos" or tid in self._todo_emitted_for:
            return []
        todos = args.get("todos", []) if isinstance(args, dict) else []
        if not todos:
            return []
        self._todo_emitted_for.add(tid)
        return [{"type": "todo_list", "todos": todos, "step": self.current_step}]

    def _maybe_todo_from_result(self, name: str, tid: str, result: str) -> list[dict]:
        if name != "write_todos" or tid in self._todo_emitted_for:
            return []
        todos = []
        if result:
            match = re.search(r"\[.*?\]", result, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                    if isinstance(parsed, list):
                        todos = parsed
                except (json.JSONDecodeError, ValueError):
                    pass
        if not todos:
            return []
        self._todo_emitted_for.add(tid)
        return [{"type": "todo_list", "todos": todos, "step": self.current_step}]

    def consume_usage_metadata(self, m: AIMessage) -> None:
        um = getattr(m, "usage_metadata", None) or {}
        if not um:
            return
        self.total_usage["input_tokens"] += um.get("input_tokens", 0)
        self.total_usage["output_tokens"] += um.get("output_tokens", 0)
        self.total_usage["total_tokens"] += um.get("total_tokens", 0)
        self._step_usage["input_tokens"] += um.get("input_tokens", 0)
        self._step_usage["output_tokens"] += um.get("output_tokens", 0)
        self._step_usage["total_tokens"] += um.get("total_tokens", 0)
        if um.get("input_tokens", 0) > 0:
            self.last_context_tokens = um["input_tokens"]

    def _reset_step_stats(self) -> None:
        """清空本步累计统计，供下一个 step 重新开始累计。"""
        self._step_thinking_len = 0
        self._step_content_len = 0
        self._step_tool_count = 0
        self._step_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    def _log_step_end(self, step: int) -> None:
        """打印某一步的汇总日志（思考/正文/工具/token），每行以 step 开头。

        通过 _step_end_logged 去重，确保只打印一次。触发点均为「step 真正结束之后」：
        下一个思考开始（_handle_messages 的 else 分支）、本轮正文开始（content 分支）、
        下一个 model turn 开始（_on_ai_message）、以及 finalize（最终步）。
        这样「结束」汇总一定排在该 step 的全部工具执行之后。
        """
        if step <= 0 or step in self._step_end_logged:
            return
        self._step_end_logged.add(step)
        # 正文预览：该 step 全部 content 块拼接后的前 100 字。放在 step 结束时打印，
        # 此时内容已完整（首 chunk 可能只有一个字，中途截不出 100 字预览）。
        if self._step_content_len > 0:
            _preview = ""
            for b in self.blocks:
                if b.get("type") == "content" and b.get("step") == step:
                    _preview += b.get("content", "") or ""
            logger.info(
                "[%s] step%d 📝 | 正文: %s",
                self.sid, step, _truncate(_preview, 100),
            )
        inp = self._step_usage["input_tokens"]
        out = self._step_usage["output_tokens"]
        tot = self._step_usage.get("total_tokens", 0) or (inp + out)
        ctx = self.last_context_tokens or inp
        # 上下文占用率：本步上下文 token / 模型上下文窗口长度（max_input_tokens）。
        if self.max_input_tokens:
            _ctx_pct = ctx / self.max_input_tokens * 100
            _ctx_str = f"{ctx}/{self.max_input_tokens:,} ({_ctx_pct:.1f}%)"
        else:
            _ctx_str = f"{ctx}"
        logger.info(
            "[%s] step%d 结束 | 思考:%d字符 | 正文:%d字符 | 工具:%d | "
            "Token(in/out/total):%d/%d/%d | 上下文:%s",
            self.sid, step, self._step_thinking_len, self._step_content_len,
            self._step_tool_count, inp, out, tot, _ctx_str,
        )

    def _sse_blocks(self) -> list[dict]:
        out = []
        for b in self.blocks:
            bt = b.get("type", "")
            item = {"type": bt, "order": b.get("order", 0), "step": b.get("step", 0)}
            if bt == "thinking":
                item["content"] = (b.get("content", "") or "")[:3000]
                item["duration"] = b.get("duration")
            elif bt == "tool_call":
                item["tool_name"] = b.get("tool_name", "")
                item["tool_call_id"] = b.get("tool_call_id", "")
                item["id"] = b.get("tool_call_id", "") or b.get("id", "")
                item["arguments"] = b.get("arguments", {})
                item["result"] = str(b.get("result", ""))[:3000]
                item["success"] = b.get("success", True)
                item["duration"] = b.get("duration")
                if b.get("approval_status"):
                    item["approval_status"] = b["approval_status"]
            elif bt == "content":
                item["content"] = (b.get("content", "") or "")[:8000]
            out.append(item)
        return out

    def start(self) -> list[dict]:
        return [{"type": "start", "session_id": self.session_id}]

    def finalize(self, *, session_id, elapsed_time, session_total_tokens=None) -> list[dict]:
        # 确保最后一步（如纯正文收尾、无工具边界）的汇总日志被打印。
        self._log_step_end(self.current_step)
        session_est = (
            session_total_tokens
            if session_total_tokens is not None
            else self.pre_session_tokens + self.total_usage["input_tokens"] + self.total_usage["output_tokens"]
        )
        inp = self.total_usage["input_tokens"]
        out = self.total_usage["output_tokens"]
        usage = {
            "input_tokens": inp,
            "output_tokens": out,
            "total_tokens": self.total_usage.get("total_tokens", inp + out),
            "max_input_tokens": self.max_input_tokens,
            "auto_compress_tokens": self.auto_compress_tokens,
            "session_estimate": session_est,
            "context_tokens": self.last_context_tokens if self.last_context_tokens > 0 else inp,
            "elapsed_time": round(elapsed_time, 2),
            "step_count": self.current_step,
        }
        return [{
            "type": "done", "session_id": session_id,
            "elapsed_time": round(elapsed_time, 2),
            "usage": usage,
            "blocks": self._sse_blocks(),
        }]
