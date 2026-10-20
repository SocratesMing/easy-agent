# 后端架构重构批次 1：长文件职责分离

## 目标

不改 API/SSE/配置契约，先拆最长的三个后端文件，建立「模型/校验/加载」「领域
纯逻辑/存储适配」「服务/API 流生命周期」边界。

## 1. config.py

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `easy_agent/config.py` | 1331 → 702 | `Config` 门面（load/from_yaml/parse/路径 helpers） |
| `easy_agent/config_models.py` | 新增 332 | 18 个 Pydantic 配置模型 + 默认常量 |
| `easy_agent/config_validation.py` | 新增 376 | KNOWN/DEPRECATED 键、键校验、`${VAR}` 展开、missing env |

- 两个新模块不 import config.py；依赖方向单向。
- 36 个公开/半公开名字从 config re-export，`is` 同一对象。
- `_unresolved_env_vars` 状态保留在 validation，`get_missing_env_vars` 行为不变。

## 2. knowledges/service.py

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `knowledges/service.py` | 2668 → 2402 | `KnowledgeService` 业务编排 |
| `knowledges/core/original_store.py` | 新增 194 | `LocalOriginalStore`/句柄/存储异常 |
| `knowledges/core/permissions.py` | 新增 135 | 角色动作、`effective_role`、`allowed_actions` 等纯逻辑 |

- 纯移动，AST/逐字节对比一致；旧导入路径 re-export。
- `KnowledgeService` 仍偏大，后续按 bases/documents/retrieval/team spaces 继续拆。

## 3. api/chat.py

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `api/chat.py` | 1058 → 814 | 聊天/取消/恢复路由 + `_detached_event_stream` |
| `services/stream_hub.py` | 新增 170 | `_StreamHub`、进程内 hub 注册表、常量 |
| `api/chat_live.py` | 新增 202 | `/stream/status`、`/stream/live` DB-first 只读端点 |

- URL 保持不变；`chat.py` re-export `_StreamHub` 等旧名字。
- 路由挂载用无前缀 mount 后并入 `router.routes`（避免 FastAPI 重复前缀）。
- 风险：chat_live 暂时复制了 chat 的 repository provider（不 import chat.py）；
  后续抽到共享 dependency 模块。

## 验证

| 检查 | 结果 |
| --- | --- |
| 全量 pytest | **1519 passed / 1 skipped / 0 failed（289.9s）** |
| import SCC | 232 modules / 540 edges / **0 SCC** |
| double-write | OK（153 methods / 115 delegations） |
| ruff | All checks passed |
| 配置 schema | 通过 |

## 后续（批次 2 建议）

1. `knowledges/service.py`（2402）继续拆：bases / documents / retrieval / team spaces。
2. `knowledges/ragflow/client.py`（1470）：transport / endpoints / models。
3. `api/expert.py`（1268）：CRUD / stream / export 路由。
4. `agent.py`（1264）：backend 装配 / middleware / graph 构建。
5. `services/memory_store.py`（1228）：锁 / JSONL/Markdown / structured memory。
6. 把 `chat_live` 的 repository provider 抽到共享 `api/deps.py`。
