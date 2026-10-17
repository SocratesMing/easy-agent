# Increment 3 执行报告（Batch 4 低风险治理 + Batch 2/3 收尾）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 2） |
| 依据 | `docs/audit/2026-10-17-easy-agent-audit.md`；用户确认 Batch 2/3/4 |
| 约束 | 不执行 Batch 0/1 安全项；Web Terminal 不动；前端双基线同时维护；生产 MySQL + 单机 SQLite WAL |

## 1. 本轮完成项

| 计划项 | 状态 | 主要改动 | 验证 |
| --- | --- | --- | --- |
| ARCH-12 死代码/死资源 | ✅ | 删除 `plan.md`、测试写出的 `easy_agent/prompts/unit_test_prompt.md`、3 个只剩 `__pycache__` 的空目录（`compat/`、`db/repositories/`、`services/stream/`）、未挂载的 `easy_agent/static/swagger-ui/`（1.8MB） | 全量测试通过；`tests/api/test_api_docs.py` 仍通过 |
| ARCH-14 依赖收敛 | ✅ | 移除未使用的运行期依赖 `requests`、`mysql-mcp-server`（后者实际由 `uvx` 隔离拉起）；`uv lock` 重新生成（117→115 包），`uv lock --check` 通过 | lock check 通过；全量测试通过 |
| ARCH-05 SSE 契约（第一步） | ✅ | 新增 `easy_agent/services/sse.py`：`SSE_EVENT_TYPES`（15 事件注册表）+ 唯一 `format_sse()` + `validate_event()`；`services/streaming.py` 改为 re-export（保持既有导入）；新增 `docs/sse-events.md`；新增契约测试（扫描 6 个生产端源码、文档与注册表一致性、格式断言） | `tests/services/test_sse_contract.py` 4 项通过 |
| ARCH-06 技能单一源 | ✅ | 新增 `scripts/sync_skills.py`（canonical `skills/` → 包内 `easy_agent/skills/`，支持 `--check`）；测试从只校验 `base.py` 扩展为 **3 个格式全文件树 sha256 一致性** + `--check` 子进程校验 | `tests/skills/test_office_canonical.py` 4 项通过；`sync_skills.py --check` 通过 |
| PERF-15 legacy messages 读取 | ✅ | `Database.get_session()` 不再默认 `SELECT sessions.messages`（大字段）；仅当行表为空的历史会话才回退读取；新增 `update_session_meta()`，chat/expert 仅改标题时不再回写整列 JSON | DB/会话/专家/聊天相关 149 项测试通过 |
| ARCH-11 测试体系（部分） | ✅ | 删除 `tests/conftest.py` 中已失效的 `collect_ignore`（隐藏用例）；确认 prompts 测试写入临时目录而非源码树（已有 conftest 隔离），删除历史残留文件 | 全量 collect 0 error；测试不再污染源码树 |

## 2. 验证结果

| 检查 | 结果 |
| --- | --- |
| 新增/更新测试 | SSE 契约 4 项、技能一致性 4 项、上传/日志/调度/checkpoint 上轮 10 项仍通过 |
| 受影响子系统回归 | `tests/db`、`tests/api/test_sessions.py`、`test_prompts.py`、`test_chat.py`、`test_expert.py`、`test_upload_limits.py`、`tests/skills` 共 **149 passed** |
| 全量 `pytest tests/` | **933 passed, 1 skipped, 0 failed, 0 error（236.9s）**（上轮 924 passed） |
| `uv lock --check` | 通过 |
| `uvx ruff check .` | 通过（E9/F63/F7/F82） |

## 3. 设计/文档产出

- `docs/sse-events.md`：15 个 SSE 事件契约（与代码注册表由测试同步校验）。
- `docs/design/2026-10-17-{module-refactor-plan,sse-event-contract,multi-instance-state,skills-multi-source}.md`（上轮产出，本轮 SSE/技能开始落地第一步）。
- `.github/workflows/ci.yml`：后端 ruff 致命规则 + pytest（含 collect 门禁）+ 双前端 build（上轮）。

## 4. 未完成/后续

1. **ARCH-05 剩余**：前端 3 套 SSE 消费尚未收敛为 `src/api/sse.js`；`api/expert.py`/`knowledges/chat/streaming.py` 还未改为引用注册表常量与唯一序列化器（当前已由契约测试兜住漂移）。
2. **ARCH-06 剩余**：尚未删除包内副本（保留为 pip 安装回退源），仅建立同步脚本 + 哈希门禁；后续可评估单源打包方案。
3. **ARCH-03/04**：本轮只产出设计文档，未动循环依赖/巨型类代码（按约束“先设计后实施”，建议下轮从 Phase 1 砍回边开始）。
4. **ARCH-08/09/16**：API 契约、配置收口、版本化迁移仍未开始。
5. **Batch 0/1 安全项**仍未执行。


## 5. 追加：PERF-16 / PERF-17（Round 2 收尾）

| 项 | 状态 | 改动 | 验证 |
| --- | --- | --- | --- |
| PERF-16 记忆更新 LLM 调用占用默认线程池 | ✅ | `streaming.py` 新增独立有界 `ThreadPoolExecutor(max_workers=2, thread_name_prefix="memory-update")`，会话/长期记忆更新不再占用默认 executor | 新增 `tests/services/test_memory_executor.py` 1 项 |
| PERF-17 日志 handler 每条 flush+fstat | ✅ | `_TimeSizeRotatingFileHandler` 改为 `stream.tell()` 增量维护字节数，`shouldRollover` 不再每条 `flush()+fstat()`；轮转语义不变 | 新增 `tests/test_log_rotation.py`（200 条日志触发按大小轮转） |

最终全量回归：**933 passed, 1 skipped, 0 failed, 0 error（236.9s）**；`uvx ruff check .` 通过。
