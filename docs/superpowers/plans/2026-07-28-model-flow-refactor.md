# 模型创建流程 Phase 1 重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在保持行为零变化的前提下，去重并理清 easy_agent 模型创建流程的结构（magic number 常量化、私有函数公开化、子类职责分清、docstring 修正）。

**Architecture:** 纯结构重构，不改 `create_model` 对外签名、不新增配置面。先加表征测试锁定现有行为作为安全网，再逐项重构并保持测试全绿。模型构造是惰性的（仅 invoke 时联网），故测试用伪 api_key 即可，无需网络。

**Tech Stack:** Python 3.11+, Pydantic, langchain-openai (ChatOpenAI), langchain-anthropic (ChatAnthropic), langchain-core (HumanMessage), pytest, pytest-asyncio

**对应 spec:** `docs/superpowers/specs/2026-07-28-model-flow-refactor-design.md`

---

## File Structure

- **Create:** `tests/test_model.py` — 表征测试，锁定 `create_model` / `extract_reasoning` / `strip_image_content` / `resolve_llm_config` 的现有行为（重构安全网）。
- **Modify:** `easy_agent/model.py` — ① magic number 常量化；③ `_resolve_llm_config` -> `resolve_llm_config` 公开化；② `ReasoningChatOpenAI` 两职责分段；④ 顶部 docstring 修正。
- **Modify:** `easy_agent/agent.py` — ③ `_create_agent` 改用公开 `resolve_llm_config`、去重日志字段。
- 其余 `create_model` 调用方（`app.py`、`agent_manager.py`、`streaming.py`、其他 tests/）：**无改动**（签名不变）。

---

## Task 1: 表征测试安全网

**Files:**
- Create: `tests/test_model.py`

说明：表征测试在**当前未重构代码**上即应通过，作为后续重构的安全网（非「先红后绿」，而是「基线全绿」）。后续每个重构任务须保持其全绿。

- [ ] **Step 1: 写表征测试**

创建 `tests/test_model.py`：

```python
"""Characterization tests for easy_agent.model.

Pin current behavior of model creation, reasoning extraction and image
stripping so the Phase 1 structural refactor can be verified to preserve
behavior. Chat model constructors are lazy (no network until invoke), so
tests use a fake api_key.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from easy_agent.config import (
    AgentConfig,
    Config,
    LLMConfig,
    ProviderConfig,
    RetryConfig,
    ToolsConfig,
)
from easy_agent.model import (
    ReasoningChatOpenAI,
    create_model,
    extract_reasoning,
    strip_image_content,
)


def _make_config(protocol="openai", supports_vision=False, model_name="m"):
    provider = ProviderConfig(
        provider="p",
        api_key="sk-fake",
        model="mod",
        api_base="http://x",
        max_input_tokens=128000,
        protocol=protocol,
        supports_vision=supports_vision,
    )
    return Config(
        llm=LLMConfig(
            api_key="sk-fake",
            model="mod",
            provider="p",
            max_input_tokens=128000,
            protocol=protocol,
            supports_vision=supports_vision,
            retry=RetryConfig(enabled=True, max_retries=2),
        ),
        agent=AgentConfig(),
        tools=ToolsConfig(),
        models={model_name: provider},
        active_model=model_name,
    )


class TestCreateModel:
    def test_openai_protocol_returns_reasoning_chat_openai(self):
        model = create_model(_make_config(protocol="openai", supports_vision=False))
        assert isinstance(model, ReasoningChatOpenAI)
        assert isinstance(model, ChatOpenAI)
        assert model.model == "mod"
        assert model.supports_vision is False
        assert model.max_retries == 2

    def test_openai_protocol_supports_vision(self):
        model = create_model(_make_config(protocol="openai", supports_vision=True))
        assert model.supports_vision is True

    def test_anthropic_protocol_returns_chat_anthropic(self):
        model = create_model(_make_config(protocol="anthropic"))
        assert isinstance(model, ChatAnthropic)
        assert model.model == "mod"
        assert model.max_retries == 2

    def test_anthropic_thinking_budget_and_max_tokens(self):
        model = create_model(_make_config(protocol="anthropic"))
        assert model.thinking == {"type": "enabled", "budget_tokens": 10000}
        assert model.max_tokens == 16000

    def test_missing_api_key_raises(self):
        provider = ProviderConfig(provider="p", api_key="", model="mod", protocol="openai")
        config = Config(
            llm=LLMConfig(api_key="", model="mod", provider="p", protocol="openai"),
            agent=AgentConfig(),
            tools=ToolsConfig(),
            models={"m": provider},
            active_model="m",
        )
        with pytest.raises(ValueError):
            create_model(config)


class TestExtractReasoning:
    def test_reasoning_content(self):
        assert extract_reasoning({"reasoning_content": "thought"}) == "thought"

    def test_reasoning_alias(self):
        assert extract_reasoning({"reasoning": "r"}) == "r"

    def test_reason_content_alias(self):
        assert extract_reasoning({"reason_content": "rc"}) == "rc"

    def test_first_key_wins(self):
        assert extract_reasoning({"reasoning_content": "a", "reasoning": "b"}) == "a"

    def test_empty(self):
        assert extract_reasoning({}) == ""
        assert extract_reasoning(None) == ""


class TestStripImageContent:
    def test_replaces_image_url_with_text_placeholder(self):
        msgs = [HumanMessage(content=[
            {"type": "text", "text": "看这张图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
        ])]
        result = strip_image_content(msgs)
        content = result[0].content
        assert isinstance(content, list)
        assert content[0] == {"type": "text", "text": "看这张图"}
        assert content[1]["type"] == "text"
        assert "图片内容已省略" in content[1]["text"]

    def test_single_image_collapses_to_string(self):
        msgs = [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "x.png"}}])]
        result = strip_image_content(msgs)
        assert isinstance(result[0].content, str)
        assert "图片内容已省略" in result[0].content

    def test_passes_through_plain_text(self):
        msgs = [HumanMessage(content="hello")]
        assert strip_image_content(msgs)[0].content == "hello"

    def test_idempotent(self):
        msgs = [HumanMessage(content=[{"type": "text", "text": "hi"}])]
        once = strip_image_content(msgs)
        twice = strip_image_content(once)
        assert twice[0].content == once[0].content
```

- [ ] **Step 2: 运行确认基线通过**

Run: `pytest tests/test_model.py -v`
Expected: PASS（全部用例在当前代码上通过）。若 `ChatAnthropic` 构造报参数错，确认 `langchain-anthropic` 已安装（`uv sync`）。

- [ ] **Step 3: Commit**

```bash
git add tests/test_model.py
git commit -m "test(model): add characterization tests for model creation flow"
```

---

## Task 2: magic number 常量化（①）

**Files:**
- Modify: `tests/test_model.py`（追加常量断言）
- Modify: `easy_agent/model.py`（新增常量 + `_create_anthropic_compatible` 引用）

- [ ] **Step 1: 写失败测试**

在 `tests/test_model.py` 末尾追加：

```python
class TestAnthropicConstants:
    def test_constants_exist_and_match_previous_values(self):
        from easy_agent.model import (
            ANTHROPIC_MAX_TOKENS,
            ANTHROPIC_THINKING_BUDGET_TOKENS,
        )
        assert ANTHROPIC_THINKING_BUDGET_TOKENS == 10000
        assert ANTHROPIC_MAX_TOKENS == 16000
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_model.py::TestAnthropicConstants -v`
Expected: FAIL（`ImportError: cannot import name 'ANTHROPIC_THINKING_BUDGET_TOKENS'`）

- [ ] **Step 3: 引入常量并使用**

在 `easy_agent/model.py` 中，`_REASONING_KEYS` 定义之后新增：

```python
# Anthropic-compatible models: extended-thinking budget and output cap.
# Values identical to the previous hardcoded literals (Phase 1 behavior-preserving).
ANTHROPIC_THINKING_BUDGET_TOKENS = 10000
ANTHROPIC_MAX_TOKENS = 16000
```

将 `_create_anthropic_compatible` 中的两个字面量替换为常量：

```python
def _create_anthropic_compatible(llm_config) -> ChatAnthropic:
    """Create model using Anthropic-compatible API.

    Works with any provider that exposes an Anthropic-compatible endpoint,
    such as MiniMax, AWS Bedrock, or direct Anthropic API.

    Configure api_base in config.yaml, e.g.:
    - MiniMax China: https://api.minimaxi.com/anthropic
    - MiniMax Global: https://api.minimax.io/anthropic
    - Anthropic direct: https://api.anthropic.com
    """
    kwargs = {
        "model": llm_config.model,
        "api_key": llm_config.api_key,
        "base_url": llm_config.api_base,
        "max_retries": llm_config.retry.max_retries if llm_config.retry.enabled else 0,
        "thinking": {"type": "enabled", "budget_tokens": ANTHROPIC_THINKING_BUDGET_TOKENS},
        "max_tokens": ANTHROPIC_MAX_TOKENS,
    }
    return ChatAnthropic(**kwargs)
```

- [ ] **Step 4: 运行确认通过**

Run: `pytest tests/test_model.py -v`
Expected: PASS（含新增常量断言与原有表征测试）

- [ ] **Step 5: Commit**

```bash
git add tests/test_model.py easy_agent/model.py
git commit -m "refactor(model): extract anthropic thinking/max_tokens to named constants"
```

---

## Task 3: resolve_llm_config 公开化 + 去重日志（③）

**Files:**
- Modify: `tests/test_model.py`（追加 `resolve_llm_config` 测试）
- Modify: `easy_agent/model.py`（`_resolve_llm_config` -> `resolve_llm_config`）
- Modify: `easy_agent/agent.py`（改导入 + 调用 + 去重日志）

- [ ] **Step 1: 写失败测试**

在 `tests/test_model.py` 末尾追加：

```python
class TestResolveLlmConfig:
    def test_resolves_named_model(self):
        from easy_agent.model import resolve_llm_config
        config = _make_config(protocol="anthropic", supports_vision=True)
        cfg = resolve_llm_config(config, "m")
        assert cfg.provider == "p"
        assert cfg.model == "mod"
        assert cfg.protocol == "anthropic"
        assert cfg.max_input_tokens == 128000
        assert cfg.supports_vision is True

    def test_none_falls_back_to_active(self):
        from easy_agent.model import resolve_llm_config
        config = _make_config(protocol="openai")
        assert resolve_llm_config(config, None) is config.llm

    def test_unknown_model_falls_back_to_active(self):
        from easy_agent.model import resolve_llm_config
        config = _make_config(protocol="openai")
        assert resolve_llm_config(config, "nope") is config.llm
```

- [ ] **Step 2: 运行确认失败**

Run: `pytest tests/test_model.py::TestResolveLlmConfig -v`
Expected: FAIL（`ImportError: cannot import name 'resolve_llm_config'`）

- [ ] **Step 3: 重命名为公开函数**

在 `easy_agent/model.py` 中，将函数定义与内部调用处的 `_resolve_llm_config` 全部改为 `resolve_llm_config`：

- `def _resolve_llm_config(config: Config, model_name: str | None):` -> `def resolve_llm_config(config: Config, model_name: str | None):`
- `create_model` 内 `llm_config = _resolve_llm_config(config, model_name)` -> `llm_config = resolve_llm_config(config, model_name)`

确认 `rg -n "_resolve_llm_config" easy_agent/` 无残留（仅应剩 `resolve_llm_config`）。

- [ ] **Step 4: 更新 agent.py 导入、调用并去重日志**

在 `easy_agent/agent.py` 的 `_create_agent` 方法中：

将局部导入
```python
        from .model import _resolve_llm_config
```
改为
```python
        from .model import resolve_llm_config
```

将调用与日志块
```python
        actual_llm_cfg = _resolve_llm_config(self.config, self.model_name)
        logger.info(
            f"[{self.session_id}] 🤖 当前使用模型 | "
            f"model_key: {self.model_name} | "
            f"provider: {actual_llm_cfg.provider} | "
            f"model: {actual_llm_cfg.model} | "
            f"protocol: {actual_llm_cfg.protocol} | "
            f"max_input_tokens: {actual_llm_cfg.max_input_tokens}"
        )
```
改为（去除与 `create_model` 日志重复的 provider/model/protocol，保留 session 上下文与 max_input_tokens）：
```python
        actual_llm_cfg = resolve_llm_config(self.config, self.model_name)
        logger.info(
            f"[{self.session_id}] 🤖 当前使用模型 | "
            f"model_key: {self.model_name} | "
            f"max_input_tokens: {actual_llm_cfg.max_input_tokens}"
        )
```

- [ ] **Step 5: 运行确认通过**

Run: `pytest tests/test_model.py -v`
Expected: PASS

补充导入烟雾测试（确保 agent.py 仍可导入）：
Run: `python -c "from easy_agent.agent import EasyAgent; from easy_agent.model import resolve_llm_config; print('ok')"`
Expected: 输出 `ok`

- [ ] **Step 6: Commit**

```bash
git add tests/test_model.py easy_agent/model.py easy_agent/agent.py
git commit -m "refactor(model): expose resolve_llm_config publicly and dedupe agent log"
```

---

## Task 4: ReasoningChatOpenAI 职责分清（②）

**Files:**
- Modify: `easy_agent/model.py`（`ReasoningChatOpenAI` 类文档与分段注释）

说明：纯结构整理（文档+注释+方法分组），不改任何方法体、不改对外接口。表征测试为安全网。

- [ ] **Step 1: 更新类文档与分段注释**

将 `easy_agent/model.py` 中 `class ReasoningChatOpenAI(ChatOpenAI):` 的 docstring 替换为明确两职责的版本，并在两组方法之间加分段注释。最终类骨架（方法体保持不变，仅加注释/分组）：

```python
class ReasoningChatOpenAI(ChatOpenAI):
    """ChatOpenAI 子类，承载两个独立职责（Phase 1 仅做结构分清，不改行为）：

    职责 A — 图片过滤：当 supports_vision=False 时，在流式/非流式入口
        过滤消息中的 image_url/image 多模态块，避免 DeepSeek 等纯文本模型
        收到图片内容报 400 invalid_request_error。
    职责 B — reasoning 提取：把 OpenAI 兼容接口返回的思考内容
        (reasoning_content / reasoning / reason_content) 写入 additional_kwargs，
        供前端展示。
    """

    supports_vision: bool = False

    # ── 职责 A：图片过滤 ─────────────────────────────────────────────
    def _maybe_strip_images(self, messages, **kwargs):
        # 方法体不变
        ...

    async def _astream(self, messages, stop=None, **kwargs):
        # 方法体不变
        ...

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        # 方法体不变
        ...

    def _stream(self, messages, stop=None, **kwargs):
        # 方法体不变
        ...

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        # 方法体不变
        ...

    # ── 职责 B：reasoning 提取 ───────────────────────────────────────
    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ):
        # 方法体不变
        ...
```

注意：上方 `...` 仅为占位示意，实施时**保留各方法原有完整方法体**，只新增类 docstring 与两条 `# ── 职责 X ──` 分段注释。

- [ ] **Step 2: 运行确认通过**

Run: `pytest tests/test_model.py -v`
Expected: PASS（行为未变）

Run: `python -c "from easy_agent.model import ReasoningChatOpenAI; print('ok')"`
Expected: 输出 `ok`

- [ ] **Step 3: Commit**

```bash
git add easy_agent/model.py
git commit -m "refactor(model): clarify ReasoningChatOpenAI responsibility boundaries"
```

---

## Task 5: model.py docstring 修正（④）

**Files:**
- Modify: `easy_agent/model.py`（顶部模块 docstring）

- [ ] **Step 1: 修正顶部 docstring**

将 `easy_agent/model.py` 顶部模块 docstring 由

```python
"""Model factory for creating LLM instances.

Supported providers:
- deepseek: ChatOpenAI (OpenAI-compatible API)
- minimax: ChatAnthropic (Anthropic-compatible API)
"""
```
改为

```python
"""Model factory for creating LLM instances.

Model selection is protocol-driven (see ``ProviderConfig.protocol``):
- "openai":     ChatOpenAI subclass (ReasoningChatOpenAI), OpenAI-compatible API.
- "anthropic":  ChatAnthropic, Anthropic-compatible API (e.g. MiniMax / Bedrock / Anthropic).

Use ``create_model(config, model_name)`` to build an instance; config resolution
is handled by the public ``resolve_llm_config``.
"""
```

- [ ] **Step 2: 运行确认通过**

Run: `pytest tests/test_model.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add easy_agent/model.py
git commit -m "docs(model): fix stale provider docstring to reflect protocol dispatch"
```

---

## Final Verification

- [ ] **Step 1: 全量表征测试**

Run: `pytest tests/test_model.py -v`
Expected: 全部 PASS

- [ ] **Step 2: 导入与签名回归烟雾测试**

Run: `python -c "from easy_agent.model import create_model, resolve_llm_config, ReasoningChatOpenAI, ANTHROPIC_THINKING_BUDGET_TOKENS, ANTHROPIC_MAX_TOKENS; from easy_agent.agent import EasyAgent; print('ok')"`
Expected: 输出 `ok`（确认公开符号可导入、agent.py 仍可导入）

- [ ] **Step 3: 残留私有引用检查**

Run: `rg -n "_resolve_llm_config" easy_agent/`
Expected: 无输出（已全部改为 `resolve_llm_config`）

- [ ] **Step 4: 调用方未受影响检查**

Run: `rg -n "create_model\(" easy_agent/ | rg -v "def create_model"`
Expected: 仅 `app.py`、`agent_manager.py`、`agent.py`、`streaming.py` 原有调用点，签名未变，无需改动。

- [ ] **Step 5: 可选—流式脚本手动验证（需 config.yaml）**

若环境有 `easy_agent/config/config.yaml`：
Run: `python tests/test_v4_pure_agent.py --prompt "说一句你好"`
Expected: 正常创建模型并流式输出（验证模型创建端到端无回归）。
