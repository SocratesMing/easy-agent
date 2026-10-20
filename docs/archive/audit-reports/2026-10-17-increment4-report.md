# Increment 4 执行报告（ARCH-03 Phase 1：砍回边）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 3） |
| 依据 | `docs/design/2026-10-17-module-refactor-plan.md` Phase 1；用户确认 Batch 4 |
| 约束 | 不改外部 API/SSE 契约；每步可独立回滚；行为变更有测试 |

## 1. 本轮完成项

| 改动 | 内容 | 效果 |
| --- | --- | --- |
| 新增 `easy_agent/runtime_config.py` | 无依赖的运行期配置持有者 + Agent 缓存失效回调注册表 | 基础层（config/utils/middleware）不再 import services |
| `config.py` 去反向依赖 | 4 处 `services.agent_manager.get_agent_config` 改为 `runtime_config.get_runtime_agent_config` | 断开 `config → services.agent_manager` 回边 |
| `middleware/auth.py` 去反向依赖 | 改用 runtime_config（保留 `get_agent_config` 兼容别名给既有测试 monkeypatch） | 断开 `middleware → services` 回边 |
| `tools/expert_writer.py` 去反向依赖 | 配置读取走 runtime_config；缓存失效走 `runtime_config.invalidate_user_agents` 注册回调（agent_manager 导入时注册） | 断开 `tools.expert_writer → services.agent_manager` 回边 |
| `services/session_uploads.py` 去反向依赖 | 2 处配置读取改 runtime_config | 断开 `services.session_uploads → services.agent_manager` 回边 |
| `resolve_context_length` 迁移 | 从 `agent.py` 移到 `model.py`；`agent.py`/`streaming.py` 从 model 导入，`summarization.py` 不再 import agent | 断开 `agent ↔ summarization` 环 |
| `tests/test_runtime_config.py` | 新增 3 项：目录解析走 runtime 配置、显式 config 优先、invalidator 注册表 | 覆盖新契约 |
| `tests/test_expert_writer.py` | 失效断言改为 patch runtime_config（新架构契约） | 回归通过 |

## 2. 量化结果（AST import 图，脚本 `_analysis/t4_import_graph4.py`）

| 指标 | 改动前（Round 2 末） | 本轮后 |
| --- | --- | --- |
| 模块数 / 内部边 | 177 / 347 | 179 / 351 |
| 循环依赖 SCC | 2 个（23 + 2 模块 = 25） | **2 个（16 + 2 模块 = 18）** |
| 环内模块数 | 25 | **18（-28%）**；相对审计基线 39+2 模块，累计 **-55%** |
| 大环成员变化 | 含 config、middleware.auth、summarization、tools.expert_writer、services.session_uploads | 这些模块已移出大环；剩余大环集中在 api.chat ↔ knowledges.* ↔ services（设计文档 Phase 2/3 处理） |

## 3. 验证

| 检查 | 结果 |
| --- | --- |
| 受影响测试 | runtime_config 3、expert_writer 29、auth/multi-worker、summarization、上传、会话等 131+ 项回归通过 |
| 全量 `pytest tests/` | **936 passed, 1 skipped, 0 failed, 0 error（239.5s）**（上轮 933 passed） |
| `uvx ruff check .` | 通过（E9/F63/F7/F82） |
| `uv lock --check` | 通过 |

## 4. 未完成/后续（ARCH-03 Phase 1 剩余）

1. `knowledges.api → api.chat` 回边：需把知识面板流式下沉到 `knowledges/chat/`（设计文档 Phase 1 第 3 项，改动面较大，建议独立 PR）。
2. `db.database ↔ knowledges.core.schema` 2 模块环：建议 schema 注册制（Phase 1 第 4 项）。
3. `tools.scheduled_task → services.scheduler` / `services.__init__` 相关边：需要调度器依赖注入或 service locator，归 Phase 4。
4. ARCH-03 Phase 2（Database 拆 repository）、Phase 3（streaming 拆分）未开始。
5. ARCH-08/09/15/16 未开始；Batch 0/1 安全项未执行。
