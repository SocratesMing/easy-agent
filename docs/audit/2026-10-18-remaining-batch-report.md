# 非安全剩余项批量执行报告（2026-10-18）

范围：FW-04 HarnessProfile 官方迁移、A1 知识流式下沉/SCC=0、A3 技能单一源、C2 finalize_stream、C3 四套 SSE 并一、C1b 服务层边界、E2 配置校验、E3 RED 指标、E4 文档、FW-12/14、三条专项增强。全部仅后端、未改前端、未执行 Batch 0/1、Web Terminal 未动、未 commit。

> 执行过程中 wave1 子智能体并发失败，部分文件（A1 新文件）一度被写成 base64；已解码修复并以全量测试验证。wave1/2 的最终结果以本报告与工作区代码为准，不依赖失败子任务的单方结论。

## 1. 交付清单

| 项 | 状态 | 结果 |
| --- | --- | --- |
| FW-04 execute 描述 | ✅ | 移除 `FilesystemMiddleware.__init__` monkeypatch，改用官方 `HarnessProfile(tool_description_overrides=...)` + `register_harness_profile`；旧函数保留 deprecated 兼容 |
| A1 知识流式下沉 | ✅ | 新增 `knowledges/chat/host_port.py`/`panel.py` 端口；`knowledges.api` 不再反向 import `api.chat`；`api.chat` 注册宿主能力；**import SCC=0**，白名单清空 |
| A3 技能单一源 | ✅ | `scripts/sync_skills.py` 单向生成 + `--check` 全量哈希；办公 12 文件哈希；测试守护 |
| C2 finalize_stream | ✅ | chat/resume 收尾抽 `finalize_stream`；orchestrator 行数下降；golden 测试 |
| C3 SSE 并一 | ✅ | 全仓 24/24 frame 站点经 `SSEEmitter`；`format_sse` 唯一序列化；事件注册表 15 事件；golden 字节等价 |
| C1b 服务层边界 | ✅ | 目标 API 的 `repo.database` 19→7；新增 `UserRepository` 活跃时间、`ScheduledTaskRepository.get_workspace_name`、`session_uploads` 端口；剩余边界登记 |
| E2 配置校验 | ✅ | 新增 `scripts/check_config_schema.py`、`scripts/check_env_usage.py`；未知键 warning/`EASY_CONFIG_STRICT=1` fail-fast；config/example diff |
| E3 RED 指标 | ✅ | `services/metrics.py` + ASGI 中间件 + `/agent/metrics`（admin）；HTTP 自动埋点；SSE/工具/HITL `record_*` API 已提供 |
| E4 文档 | ✅ | AGENTS/CLAUDE/README 事实同步；配置校验说明；API 文档脚本检查 |
| FW-12 动态 HITL | ✅ | `build_interrupt_config()` 只保留实际注入工具；execute 条件审批/MCP 写工具/未注入工具过滤；目录删除策略文档化 |
| FW-14 MCP server 错误 | ✅ | mcp-server 3 工具失败改 `ToolError`，`isError=true`，脱敏；structuredContent 锁定；43 passed |
| 三条专项增强 | ✅ | 记忆跨进程 `fcntl.flock` + 原子双写；外部连接器/知识库来源引用规则（prompt loader 同步） |
| FW-11 语义检索 | ⚠ 记录 | SQL store 的 `query` 仍不支持（需 embedding/向量索引）；官方 PostgresStore 启用时由官方实现；见 `docs/design/2026-10-18-fw11-store-query.md` |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 全量 `pytest tests/` | **1347 passed, 1 skipped, 0 failed, 0 error（256.4s）** |
| 架构门禁 | `import_graph --check-scc` → 217 modules / 494 edges / **0 SCC**；双写冻结 OK |
| 静态检查 | `uvx ruff check .` 通过 |
| MCP server | `cd mcp-server && uv run pytest -q` → 43 passed |
| 关键定向 | HITL/FW12 31+20 passed；C3 180 passed；C1b 211 passed；E3 26 passed；专项 59 passed |

## 3. 明确遗留 / 不做的部分

1. **FW-11 语义检索**：无 embedding/向量索引，`SqlStore` 的 `query` 语义不实现；PostgresStore 路径由官方支持。
2. **C3 wire 心跳 / 5 处 SSE 响应头**：需前端配合/后续 polish，本轮未做。
3. **E3 埋点覆盖**：HTTP 自动接入；SSE/工具/HITL 只提供 API，尚未在业务链路调用。
4. **C1 边界**：知识域 `repo.database`、流式引擎 3 处、`resolve_owned_expert`、`api/sessions.py` 2 处保留并登记。
5. **B2 SSE 共享事件日志、多副本 Phase B/C**：本轮不在目标内（此前用户暂缓），未做。
6. **Batch 0/1 安全项**：未执行。
7. **前端**：未改。

## 4. 产物

- 设计：`docs/design/2026-10-18-fw04-harness-profile.md`、`a1-knowledge-streaming.md`、`a3-skills-single-source.md`、`c2-streaming-split.md`、`c3-sse-producers.md`、`c1-service-boundaries.md`、`e3-red-metrics.md`、`fw12-dynamic-hitl.md`、`fw14-mcp-error.md`、`specialty-hardening.md`、`2026-10-18-fw11-store-query.md`
- 报告：`docs/audit/2026-10-18-increment5x-*`、`increment60-fw14-report.md`、`increment61-c1b-report.md` 等
- 测试：新增/扩展 100+ 用例
