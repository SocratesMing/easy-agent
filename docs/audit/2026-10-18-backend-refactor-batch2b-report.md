# 后端架构重构批次 2B：`api/expert.py` 辅助逻辑分离

| 项 | 内容 |
| --- | --- |
| 范围 | 仅后端；纯移动 + re-export；不改 URL / HTTP 方法 / 响应 / SSE 契约 |
| 基线 | HEAD `20e471d`（批次 1 之后） |
| 约束 | 不 commit / push；不触碰 config / knowledges / chat / memory_store |

## 1. 变更概览

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `easy_agent/api/expert.py` | **1268 → 678** | 4 个请求模型 + `get_expert_repository` / `_as_expert_repository` + 6 条路由 |
| `easy_agent/api/expert_helpers.py` | 新增 **673** | 20 个非路由辅助符号（生成/配置/提示词/预检/拒答流） |
| `tests/api/test_expert_helpers.py` | 新增 **298** | 12 个用例：re-export 身份、依赖方向、纯函数样本、路由/OpenAPI 契约 |
| `tests/api/test_expert.py` | 1235 → 1236（+1 import） | 14 处 monkeypatch 目标改到实现模块（仅测试适配） |

`expert.py` 的 6 条路由与批次变更前逐字节一致；URL、方法、`tags=["Expert"]`、
响应字段、SSE 事件形状均未改动。

## 2. 移动的符号（20 个，纯移动）

`_current_user_id`、`_to_public`、`_get_config`、`_tag_options`、
`_ensure_name_available`、`_generate_and_store`、`_expert_flags`、
`_with_guardrail`、`_load_expert_prompt`、`_expert_meta_for_message`、
`_as_str_list`、`_expert_profile`、`_expert_tag_name`、`_precheck_llm`、
`_precheck_disable_thinking`、`_session_logger_for`、`_persist_user_turn`、
`_recent_context`、`_relevance_gate`、`_refusal_event_stream`。

保留在 `expert.py`（共享 provider / 路由层）：

- `get_expert_repository`、`_as_expert_repository`（`get_database` 仅出现在该 provider）；
- `ExpertAddRequest` / `ExpertUpdateRequest` / `ExpertDelRequest` / `ExpertRunRequest`；
- `add_expert` / `update_expert` / `list_experts` / `list_public_experts` /
  `delete_expert` / `stream_export_run`。

## 3. 兼容与依赖方向

- `expert.py` 从 helpers 显式 import 上述 20 个名字，并写入 `__all__`；
  `from easy_agent.api.expert import _to_public` 等旧导入路径与
  `expert_api._expert_profile` 属性访问继续可用，且与
  `expert_helpers._expert_profile` 是**同一对象**（`is` 断言）。
- `expert_helpers.py` 不 import `api.expert`（AST 测试固化），依赖方向单向：
  `api.expert → api.expert_helpers → db/model/services/utils`。
- 纯移动校验：20 个移动函数 + 12 个保留定义（provider / 模型 / 路由）的源码与
  HEAD 逐字节一致（AST source segment 对比），无逻辑改动。

## 4. 测试适配说明

辅助逻辑移动后，其内部名字在 `expert_helpers` 模块全局解析；对 `api.expert`
属性做 `monkeypatch` 不再能拦截实现（re-export 只保证读取到同一对象）。因此
`tests/api/test_expert.py` 仅做测试适配：新增 `expert_helpers` 导入，把 14 处
`monkeypatch.setattr(expert_api, ...)` 的目标改为 `expert_helpers`（`create_model`、
`generate_expert_config`、`get_agent_config`、`expert_relevance`、`_expert_profile`），
断言与业务逻辑零改动。

## 5. 验证

| 检查 | 结果 |
| --- | --- |
| `pytest tests/api/test_expert.py tests/api/test_expert_helpers.py -q` | **70 passed** |
| `pytest tests/services/test_public_expert.py tests/services/test_sse_contract.py tests/api/test_c1_remaining_domains.py -q` | **49 passed** |
| 全量 `pytest tests/ -q`（WSL venv, Python 3.11） | **1582 passed / 1 skipped / 0 failed（289.83s）** |
| `uvx ruff check .` | All checks passed |
| `python3 scripts/quality/import_graph.py --check-scc` | 235 modules / 550 edges / **0 SCC** |
| OpenAPI 路径/方法/tags | 6 条路由断言通过（URL 不变、各挂载一次） |

说明：任务中点名的 `tests/api/test_public_experts.py` 在仓库中不存在；公共专家
用例实际位于 `tests/services/test_public_expert.py`（已运行，通过）。全量计数
1582 = 批次 1 基线 1519 + 并行批次 memory_store 拆分新增 51 用例（共享工作区中
已存在，**非本批改动**）+ 本批新增 12 用例；本批未触碰 memory_store。

## 6. 风险

1. **monkeypatch 语义变化**：仓库内已适配；仓库外若有人 patch
   `expert_api.create_model` / `get_agent_config` / `expert_relevance`，需改为
   patch `expert_helpers`。re-export 不提供 patch 透传。
2. **helpers 仍偏大（673 行）**：本批坚持纯移动，未再拆
   prompt / precheck / stream；后续可继续按这三块下沉。
3. `_refusal_event_stream` 依赖 `SSEEmitter` 与 `streaming.build_assistant_message_dict`，
   现放在 helpers；SSE 契约由 `tests/services/test_sse_contract.py` 覆盖。
4. 未 commit / push；工作区仅本批代码、测试与本文档变更。
