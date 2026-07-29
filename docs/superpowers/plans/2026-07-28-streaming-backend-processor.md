# 流式后端 StreamProcessor 重构（C2-1）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用共享 `StreamProcessor` 取代 `streaming.py` 两个重复生成器的核心逻辑，基于 `stream_mode=['messages','updates']`：updates 驱动 tool_call/tool_result/token_usage（完整消息，免重组/免 id 匹配 hack），messages 驱动 thinking/content token 流式。修复子项目 A 两个 bug，去重两生成器。

**Architecture:** `StreamProcessor.handle(mode, data) -> list[event dict]` 为纯处理单元，单测喂合成消息（免 agent 循环/免网络）。两个生成器变薄封装：驱动 `agent.astream(stream_mode=['messages','updates'])`，逐事件喂 `handle()`，yield 产出事件。

**Tech Stack:** Python 3.11+, deepagents 0.6.8, langgraph, langchain-core (AIMessage/ToolMessage/AIMessageChunk), pytest

**对应 spec:** `docs/superpowers/specs/2026-07-28-streaming-native-refactor-design.md`（第 1、2 段；前端第 3 段属 C2-2 另出计划）

**迁移说明：** thinking/content 的 token 流式逻辑（reasoning 提取、thinking block 生命周期、content 封存）从 `streaming.py` 现有实现迁移为 `StreamProcessor` 方法，本计划给出方法签名+关键适配+源码行号，不逐行重贴（属迁移非新写）。

---

## File Structure

- **Create:** `easy_agent/services/stream_processor.py` - `StreamProcessor`（handle/start/finalize + 内部 _on_ai_message/_on_tool_message/_handle_messages）。
- **Modify:** `easy_agent/services/streaming.py` - `chat_stream_generator`/`resume_stream_generator` 重写为薄封装，调用 `StreamProcessor`；删除 chunk 重组与 id 匹配 hack。
- **Create:** `tests/test_stream_processor.py` - 合成消息单测（免网络）。

---

## Task 1: StreamProcessor 骨架 + 合成事件测试夹具

**Files:**
- Create: `easy_agent/services/stream_processor.py`
- Create: `tests/test_stream_processor.py`

- [ ] **Step 1: 写失败测试**

`tests/test_stream_processor.py`：

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from easy_agent.services.stream_processor import StreamProcessor


def test_handle_unknown_mode_returns_empty():
    p = StreamProcessor(sid="s1")
    assert p.handle("updates", {}) == []
    assert p.handle("weird", object()) == []


def test_start_and_finalize():
    p = StreamProcessor(sid="s1", session_id="sess")
    assert p.start() == [{"type": "start", "session_id": "sess"}]
    done = p.finalize(session_id="sess", elapsed_time=1.2)
    assert done[0]["type"] == "done"
    assert done[0]["session_id"] == "sess"
    assert done[0]["usage"]["step_count"] == 0
    assert done[0]["blocks"] == []
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_stream_processor.py -v`
Expected: FAIL（`ModuleNotFoundError: easy_agent.services.stream_processor`）

- [ ] **Step 3: 写最小实现**

`easy_agent/services/stream_processor.py`：

```python
"""Shared stream processor for DeepAgents hybrid streaming.

Consumes (mode, data) events from agent.astream(stream_mode=['messages','updates'])
and emits SSE event dicts. Unit-testable with synthetic messages (no agent loop).
"""
import time
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage


class StreamProcessor:
    def __init__(self, *, sid: str = "", current_step: int = 0,
                 blocks: list | None = None, db=None, session_id: str | None = None):
        self.sid = sid
        self.current_step = current_step
        self.blocks = blocks if blocks is not None else []
        self.db = db
        self.session_id = session_id
        self.is_in_thinking = False
        self.thinking_start_time = None
        self.thinking_step_start_len = 0
        self.accumulated_thinking = ""
        self.accumulated_response = ""
        self.tool_call_start_times: dict[str, float] = {}
        self.total_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.last_context_tokens = 0

    def handle(self, mode: str, data: Any) -> list[dict]:
        if mode == "updates":
            return self._handle_updates(data)
        if mode == "messages":
            return self._handle_messages(data)
        return []

    def _handle_updates(self, data: dict) -> list[dict]:
        return []

    def _handle_messages(self, data) -> list[dict]:
        return []

    def start(self) -> list[dict]:
        return [{"type": "start", "session_id": self.session_id}]

    def finalize(self, *, session_id, elapsed_time) -> list[dict]:
        return [{
            "type": "done", "session_id": session_id,
            "elapsed_time": elapsed_time,
            "usage": {**self.total_usage, "step_count": self.current_step},
            "blocks": [dict(b) for b in self.blocks],
        }]
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_stream_processor.py -v`
Expected: PASS

- [ ] **Step 5: Commit（待批准）** - 暂不提交（见末尾提交策略）

---

## Task 2: updates model 节点 -> tool_call + token_usage（修复 A 问题 1）

**Files:**
- Modify: `easy_agent/services/stream_processor.py`（`_handle_updates` + `_on_ai_message`）
- Modify: `tests/test_stream_processor.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_stream_processor.py`：

```python
from langchain_core.messages import AIMessage


def _ai_with_tool_call():
    return AIMessage(
        content="",
        tool_calls=[{"name": "ls", "args": {"path": "/workspace/"}, "id": "tc1"}],
        usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
    )


def test_model_update_emits_tool_call_and_token_usage():
    p = StreamProcessor(sid="s1")
    events = p.handle("updates", {"model": {"messages": [_ai_with_tool_call()]}})
    types = [e["type"] for e in events]
    assert "tool_call" in types and "token_usage" in types
    tc = next(e for e in events if e["type"] == "tool_call")
    assert tc["tool_name"] == "ls"
    assert tc["tool_call_id"] == "tc1"
    assert tc["arguments"] == {"path": "/workspace/"}
    assert tc["step"] == 1
    tu = next(e for e in events if e["type"] == "token_usage")
    assert tu["input_tokens"] == 100
    assert tu["output_tokens"] == 20
    assert tu["total_tokens"] == 120
    assert tu["context_tokens"] == 100
    assert tu["step_count"] == 1
    # block recorded
    assert p.blocks[0]["tool_call_id"] == "tc1"
    assert p.blocks[0]["duration"] is None
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_stream_processor.py::test_model_update_emits_tool_call_and_token_usage -v`
Expected: FAIL（events 为空）

- [ ] **Step 3: 实现 `_handle_updates` + `_on_ai_message`**

在 `stream_processor.py` 中替换 `_handle_updates` 并新增 `_on_ai_message`：

```python
    def _handle_updates(self, data: dict) -> list[dict]:
        events = []
        for node, delta in (data.items() if isinstance(data, dict) else []):
            if not isinstance(delta, dict):
                continue
            for m in delta.get("messages", []):
                if isinstance(m, AIMessage):
                    events.extend(self._on_ai_message(m))
                elif isinstance(m, ToolMessage):
                    events.append(self._on_tool_message(m))
        return events

    def _on_ai_message(self, m: AIMessage) -> list[dict]:
        events = []
        for tc in (m.tool_calls or []):
            tid = tc.get("id", "")
            name = tc.get("name", "")
            args = tc.get("args", {}) or {}
            self.current_step += 1
            self.tool_call_start_times[tid] = time.time()
            self.blocks.append({
                "type": "tool_call", "tool_name": name, "tool_call_id": tid,
                "arguments": args, "result": "", "success": True,
                "duration": None, "step": self.current_step,
            })
            events.append({"type": "tool_call", "tool_name": name,
                           "tool_call_id": tid, "arguments": args,
                           "step": self.current_step})
        um = getattr(m, "usage_metadata", None) or {}
        if um:
            self.total_usage["input_tokens"] += um.get("input_tokens", 0)
            self.total_usage["output_tokens"] += um.get("output_tokens", 0)
            self.total_usage["total_tokens"] += um.get("total_tokens", 0)
            if um.get("input_tokens", 0) > 0:
                self.last_context_tokens = um["input_tokens"]
            events.append({
                "type": "token_usage",
                "input_tokens": self.total_usage["input_tokens"],
                "output_tokens": self.total_usage["output_tokens"],
                "total_tokens": self.total_usage["input_tokens"] + self.total_usage["output_tokens"],
                "context_tokens": self.last_context_tokens,
                "step_count": self.current_step,
            })
        return events
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_stream_processor.py -v`
Expected: PASS

- [ ] **Step 5: 暂不提交**

---

## Task 3: updates tools 节点 -> tool_result（修复 A 问题 3）

**Files:**
- Modify: `easy_agent/services/stream_processor.py`（`_on_tool_message`）
- Modify: `tests/test_stream_processor.py`

- [ ] **Step 1: 写失败测试**

追加：

```python
from langchain_core.messages import ToolMessage


def test_tools_update_emits_matched_tool_result():
    p = StreamProcessor(sid="s1")
    p.handle("updates", {"model": {"messages": [_ai_with_tool_call()]}})
    events = p.handle("updates", {"tools": {"messages": [
        ToolMessage(content="file1\nfile2", tool_call_id="tc1", name="ls")
    ]}})
    assert len(events) == 1
    tr = events[0]
    assert tr["type"] == "tool_result"
    assert tr["tool_call_id"] == "tc1"
    assert tr["tool_name"] == "ls"
    assert tr["result"] == "file1\nfile2"
    assert tr["success"] is True
    assert tr["duration"] is not None
    # block updated in place
    assert p.blocks[0]["duration"] is not None
    assert p.blocks[0]["result"] == "file1\nfile2"


def test_tool_result_id_mismatch_does_not_crash():
    p = StreamProcessor(sid="s1")
    p.handle("updates", {"model": {"messages": [_ai_with_tool_call()]}})
    events = p.handle("updates", {"tools": {"messages": [
        ToolMessage(content="x", tool_call_id="other", name="grep")
    ]}})
    # 未匹配到块：仍下发 tool_result（id=other），原块保持 duration=None
    assert events[0]["tool_call_id"] == "other"
    assert p.blocks[0]["duration"] is None
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_stream_processor.py::test_tools_update_emits_matched_tool_result -v`
Expected: FAIL（`_on_tool_message` 未定义）

- [ ] **Step 3: 实现 `_on_tool_message`**

在 `stream_processor.py` 新增：

```python
    def _on_tool_message(self, m: ToolMessage) -> dict:
        tid = m.tool_call_id
        name = m.name or "tool"
        result = str(m.content) if m.content else ""
        is_error = bool(getattr(m, "additional_kwargs", {}).get("is_error", False))
        start = self.tool_call_start_times.pop(tid, None)
        duration = round(time.time() - start, 2) if start else 0
        for blk in reversed(self.blocks):
            if blk.get("type") == "tool_call" and blk.get("tool_call_id") == tid:
                blk["result"] = result
                blk["success"] = not is_error
                blk["duration"] = duration
                break
        return {"type": "tool_result", "tool_name": name, "tool_call_id": tid,
                "arguments": {}, "result": result, "success": not is_error,
                "duration": duration, "step": self.current_step}
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_stream_processor.py -v`
Expected: PASS（含 id 不匹配用例）

- [ ] **Step 5: 暂不提交**

---

## Task 4: messages thinking/content token 流式（迁移）

**Files:**
- Modify: `easy_agent/services/stream_processor.py`（`_handle_messages` + thinking 生命周期）
- Modify: `tests/test_stream_processor.py`

说明：迁移 `streaming.py` 现有 thinking/content 处理（chat 流 900-1000、resume 流 1900-1960）为 `_handle_messages`。关键：用 `easy_agent.model.extract_reasoning` 提取 reasoning；thinking 切换时发 `thinking_start`、逐 token 发 `thinking`、切换出时发 `thinking_end`（带 duration）；正文逐 token 发 `content`；维护 thinking block 与 content block。**不改语义，仅迁移为方法。**

- [ ] **Step 1: 写失败测试（合成 AIMessageChunk）**

追加：

```python
from langchain_core.messages import AIMessageChunk


def test_messages_reasoning_emits_thinking_events():
    p = StreamProcessor(sid="s1")
    c1 = AIMessageChunk(content="", additional_kwargs={"reasoning_content": "hello "})
    c2 = AIMessageChunk(content="", additional_kwargs={"reasoning_content": "world"})
    e1 = p.handle("messages", (c1, {}))
    assert any(e["type"] == "thinking_start" for e in e1)
    assert any(e["type"] == "thinking" and e["content"] == "hello " for e in e1)


def test_messages_text_emits_content_event():
    p = StreamProcessor(sid="s1")
    c = AIMessageChunk(content="hi there")
    events = p.handle("messages", (c, {}))
    assert any(e["type"] == "content" and e["content"] == "hi there" for e in events)
```

- [ ] **Step 2: 运行确认失败**

Run: `.venv/bin/python -m pytest tests/test_stream_processor.py::test_messages_reasoning_emits_thinking_events tests/test_stream_processor.py::test_messages_text_emits_content_event -v`
Expected: FAIL（`_handle_messages` 返回空）

- [ ] **Step 3: 迁移实现 `_handle_messages`**

在 `stream_processor.py` 顶部新增 `from ..model import extract_reasoning`，并实现（迁移自 streaming.py，语义不变）：

```python
    def _handle_messages(self, data) -> list[dict]:
        chunk, _metadata = data
        events = []
        if not isinstance(chunk, AIMessageChunk):
            return events
        rc = extract_reasoning(getattr(chunk, "additional_kwargs", {}))
        content = chunk.content or ""
        if rc:
            if not self.is_in_thinking:
                self.is_in_thinking = True
                self.thinking_start_time = time.time()
                self.thinking_step_start_len = len(self.accumulated_thinking)
                self.current_step += 1
                self.blocks.append({"type": "thinking", "order": len(self.blocks),
                                    "content": "", "step": self.current_step, "duration": None})
                events.append({"type": "thinking_start", "step": self.current_step})
            self.accumulated_thinking += rc
            if self.blocks and self.blocks[-1]["type"] == "thinking":
                self.blocks[-1]["content"] = self.accumulated_thinking[self.thinking_step_start_len:]
            events.append({"type": "thinking", "content": rc, "step": self.current_step})
        if content and not getattr(chunk, "tool_call_chunks", None):
            if self.is_in_thinking:
                self.is_in_thinking = False
                dur = round(time.time() - self.thinking_start_time, 2) if self.thinking_start_time else 0
                if self.blocks and self.blocks[-1]["type"] == "thinking":
                    self.blocks[-1]["duration"] = dur
                events.append({"type": "thinking_end", "duration": dur, "step": self.current_step})
            self.accumulated_response += content
            events.append({"type": "content", "content": content})
        return events
```

- [ ] **Step 4: 运行确认通过**

Run: `.venv/bin/python -m pytest tests/test_stream_processor.py -v`
Expected: PASS

- [ ] **Step 5: 暂不提交**

---

## Task 5: 正常流薄封装（chat_stream_generator）

**Files:**
- Modify: `easy_agent/services/streaming.py`（`chat_stream_generator` 442-1595 重写为薄封装）

说明：保留函数签名与入口处的会话/文件解析、末尾记忆生成（`_submit_memory_updates`）、HITL 中断（`approval_required`）等流程逻辑；**核心流式循环替换为** `StreamProcessor`。

- [ ] **Step 1: 重写核心循环**

将 `chat_stream_generator` 中 `async for event in agent.agent.astream(..., stream_mode="messages")` 段（streaming.py:819 附近）替换为：

```python
        proc = StreamProcessor(sid=sid, session_id=session_id, db=db)
        yield format_sse(proc.start()[0])
        try:
            async for event in agent.agent.astream(
                input_data, config=stream_config, stream_mode=["messages", "updates"],
            ):
                mode, data = event if isinstance(event, tuple) else ("messages", event)
                for ev in proc.handle(mode, data):
                    # HITL 中断检测：approval_required 仍由原 interrupt 逻辑触发（见下）
                    if ev.get("type") == "approval_required":
                        yield format_sse(ev)
                    else:
                        yield format_sse(ev)
            yield format_sse(proc.finalize(session_id=session_id,
                          elapsed_time=round(time.time() - start_time, 2))[0])
        except asyncio.CancelledError:
            yield format_sse({"type": "error", "content": "请求被取消"})
```

注意：HITL `approval_required` 事件的下发、`_submit_memory_updates`、`_maybe_rename_workspace`、持久化等流程逻辑保留原位置（在循环外/末尾）。`approval_required` 的检测若依赖 interrupt，需保留原 `interrupt` 处理（迁移为 proc 钩子或循环内判断）；实现期确认 interrupt 在 `['messages','updates']` 下仍以 `updates`/`messages` 形式到达或需单独处理。

- [ ] **Step 2: 导入 StreamProcessor**

`streaming.py` 顶部新增 `from .stream_processor import StreamProcessor`。

- [ ] **Step 3: 冒烟（需 config）**

Run（具备 config 时）: `.venv/bin/python -m pytest tests/test_stream_processor.py -v` + 手动 `python tests/test_v4_pure_agent.py --prompt "列出 /workspace/ 文件"`
Expected: 单测 PASS；手动流式正常产出 tool_call/tool_result/thinking/content/done。

- [ ] **Step 4: 暂不提交**

---

## Task 6: 恢复流薄封装（resume_stream_generator）

**Files:**
- Modify: `easy_agent/services/streaming.py`（`resume_stream_generator` 1596-2409 重写为薄封装）

说明：保留 DB 状态载入、审批决策应用（`decision_status_by_id`）、`resume_command` 构造、末尾记忆/审批持久化；核心循环替换为 `StreamProcessor`（初始状态用 DB 载入的 blocks/step）。

- [ ] **Step 1: 重写核心循环**

将 `resume_stream_generator` 中 `async for event in agent.agent.astream(resume_command, ..., stream_mode="messages")` 段（streaming.py:1885 附近）替换为同 Task 5 的 `StreamProcessor` 模式，构造时传入 DB 载入的初始状态：

```python
        proc = StreamProcessor(sid=sid, session_id=session_id, db=db,
                               current_step=current_step, blocks=list(blocks))
        yield format_sse({"type": "start", "session_id": session_id})
        try:
            async for event in agent.agent.astream(
                resume_command, config=stream_config, stream_mode=["messages", "updates"],
            ):
                mode, data = event if isinstance(event, tuple) else ("messages", event)
                for ev in proc.handle(mode, data):
                    yield format_sse(ev)
            # 末尾：用 proc.blocks 写回持久化 + 记忆（保留原 _submit_memory_updates 等调用）
            yield format_sse(proc.finalize(session_id=session_id,
                          elapsed_time=round(time.time() - start_time, 2))[0])
        except asyncio.CancelledError:
            yield format_sse({"type": "error", "content": "请求被取消"})
```

- [ ] **Step 2: 冒烟（需 config + HITL 场景）**

手动：触发文件删除审批 -> 批准 -> 恢复流 -> 工具块结果到达即清"执行中"、无重复块、token 逐回合更新。
Expected: 行为符合 spec 验收。

- [ ] **Step 3: 暂不提交**

---

## Task 7: 删除旧 chunk 重组/id 匹配代码 + 最终验证

**Files:**
- Modify: `easy_agent/services/streaming.py`（删除被取代的 helper 与变量）

- [ ] **Step 1: 删除已废弃代码**

删除 `chat_stream_generator`/`resume_stream_generator` 内被 `StreamProcessor` 取代的：`_token_event`/`_finalize_step`/`_start_thinking`/`_end_thinking*`/`_seal_content_block`/`tool_call_accumulated_args*`/`tool_call_index_to_id`/`pending_tool_args_by_index`/chunk 级 `parse_tool_args` 调用。保留 `_is_error_result`/`_extract_file_paths_from_command`/`format_sse`/`build_assistant_message_dict`/`_submit_memory_updates`/`_maybe_rename_workspace` 等仍用到的模块级函数。

- [ ] **Step 2: 全量单测**

Run: `.venv/bin/python -m pytest tests/test_stream_processor.py tests/test_model.py -v`
Expected: PASS

- [ ] **Step 3: 导入烟雾**

Run: `.venv/bin/python -c "from easy_agent.services.streaming import chat_stream_generator, resume_stream_generator; from easy_agent.services.stream_processor import StreamProcessor; print('ok')"`
Expected: `ok`

- [ ] **Step 4: 行数对比**

Run: `wc -l easy_agent/services/streaming.py easy_agent/services/stream_processor.py`
Expected: streaming.py 显著下降（原 2409 行），新增 stream_processor.py 远小于被删代码。

---

## 提交策略（重要）

工作树存在既有未提交 WIP，且仓库规范要求显式同意才提交。本计划所有任务**暂不提交**，改动留在工作树供复核。待全部任务完成、验证通过后，统一征得用户同意再按 Conventional Commits 提交（如 `refactor(stream): introduce StreamProcessor with hybrid messages+updates streaming`）。

## 风险

- HITL `approval_required` 在 `['messages','updates']` 多模式下的到达形式需实现期确认（可能需额外处理 interrupt 事件）。
- thinking/content 迁移需保证与现有前端事件语义一致（打字机效果）。
- 高风险重写：建议先做 Task 1-4（纯单测覆盖的处理器），再做 Task 5-6（包装器接入），每步冒烟。
