# 子项目 C1：模型创建流程 Phase 1 重构设计

- 日期：2026-07-28
- 状态：已批准，待 spec 复核
- 范围：easy-agent 模型创建流程（`model.py` + `agent.py._create_agent` 模型相关部分）
- 阶段：Phase 1（去重 + 理清结构，严格保持现有行为）
- 选定方案：方案 1（最小结构清理）
- 关联：属「基于 DeepAgents 重构」整体计划的第一子系统；C2（流式流程重构，含/取代子项目 A）留待下一轮

## 背景

模型创建链路：`config` -> `resolve_llm_config` -> `create_model`（按 `protocol` 分发到 `_create_openai_compatible` / `_create_anthropic_compatible`）-> `EasyAgent._create_agent` 中 `create_deep_agent(model=...)`。模型热切换由 `agent_manager` 驱逐缓存并整体重建（agent_manager.py:83-85），无单独 `switch_model`。

调用面：`create_model` 有 6+ 调用方（app.py×2、agent_manager.py、agent.py、streaming.py、tests/）；`_resolve_llm_config` 仅 model.py 内部 + agent.py（打日志）使用；`ReasoningChatOpenAI` 与 `_create_*_compatible` 仅 model.py 内部，封闭。

## 现状问题

1. **magic number 硬编码**：`_create_anthropic_compatible` 中 `thinking.budget_tokens=10000`、`max_tokens=16000` 为裸数字，无具名。
2. **`ReasoningChatOpenAI` 两职责混杂**：reasoning 提取（`_convert_chunk_to_generation_chunk` + `_REASONING_KEYS`/`_extract_reasoning`）与图片过滤（`_maybe_strip_images` + `strip_image_content` + 四个 stream/generate override）混在一个子类，缺清晰边界。
3. **私有函数跨模块导入**：`agent.py._create_agent` 以 `from .model import _resolve_llm_config` 导入私有函数仅为打日志；且 `create_model` 已日志输出 provider/model/protocol，agent.py 重复解析配置并重复日志部分字段。
4. **docstring 陈旧**：model.py 顶部写「deepseek: ChatOpenAI / minimax: ChatAnthropic」，实际按 `protocol`（openai/anthropic）分发。

## 目标与验收

- 去重 + 理清结构，**严格保持现有行为**：不新增配置面、不改 `create_model` 对外签名、不改对外接口。
- 行为零变化：现有直接造模型的测试（`tests/test_dev_streaming.py`、`tests/test_v3_streaming.py`、`tests/test_v4_pure_agent.py`）及相关流式测试通过；模型热切换（agent_manager 驱逐重建）正常。
- 结构：无私有函数跨模块导入；magic number 有名可循；子类两职责清晰可读；docstring 与实现一致。

## 非目标

- 不改 `create_model` 签名/返回（6+ 调用方不受影响）。
- 不配置化 magic number（① 仅常量化；配置化属后续）。
- 不引入 `ModelFactory` / mixin（方案 2/3）。
- 不动 backend/middleware/permissions/HITL（非 model 流程，属 agent 装配）。
- 不评估 langchain 原生 reasoning_content 支持（Phase 2）。
- C2（流式流程）不在本 spec 范围。

## 设计

### ① magic number 常量化（`model.py`）
- 新增模块级具名常量 `ANTHROPIC_THINKING_BUDGET_TOKENS = 10000`、`ANTHROPIC_MAX_TOKENS = 16000`。
- `_create_anthropic_compatible` 引用上述常量。值不变，零行为变化。

### ③ 消除私有函数跨模块导入（`model.py` + `agent.py`）
- 将 `_resolve_llm_config` 提升为公开 `resolve_llm_config`（去下划线），作为「解析模型配置」唯一公开入口；`create_model` 内部改用之。
- `agent.py._create_agent` 改导入公开 `resolve_llm_config`，替代 `from .model import _resolve_llm_config`。
- **不改 `create_model` 返回签名**。
- 去重日志：保留 agent.py 带 session 上下文的日志（含 max_input_tokens），去除与 `create_model` 重复的 provider/model/protocol 字段，避免重复输出。

### ② `ReasoningChatOpenAI` 职责分清（`model.py`）
- 保持单一子类（不引入 mixin，避免 MRO 风险）。
- 用清晰分段注释 + 内聚方法组显式区分两职责：
  - 「reasoning 提取」：`_convert_chunk_to_generation_chunk` + `_REASONING_KEYS`/`_extract_reasoning`。
  - 「图片过滤」：`_maybe_strip_images` + `strip_image_content` + 四个 stream/generate override（`_astream`/`_agenerate`/`_stream`/`_generate`）。
- 不改对外接口与行为。

### ④ docstring 修正（`model.py`）
- 顶部改为说明按 `protocol`（openai/anthropic）分发；移除陈旧 deepseek/minimax 表述。

## 涉及文件

- `easy_agent/model.py`：常量化；`resolve_llm_config` 公开化；子类职责分段；docstring。
- `easy_agent/agent.py`：`_create_agent` 改用公开 `resolve_llm_config`；去重日志。
- 其余调用方（app.py、agent_manager.py、streaming.py、tests/）：无改动（`create_model` 签名不变）。

## 验证与测试

- 单元/流式测试：`pytest tests/test_dev_streaming.py tests/test_v3_streaming.py tests/test_v4_pure_agent.py -v`（直接造模型路径）+ 相关流式测试通过。
- 行为对比：重构前后模型实例创建参数一致（protocol 分发、retry、supports_vision、anthropic thinking/max_tokens 值不变）。
- 热切换：切换模型触发 agent_manager 驱逐重建，新模型生效。

## 风险

- 极低：均为原地重命名 / 常量提取 / 注释整理 / 日志去重，无逻辑改动、无签名变化。
- 注意 `_resolve_llm_config` -> `resolve_llm_config` 重命名需同步 model.py 内部与 agent.py 两处，勿遗漏。
