# 三条专项收尾报告（2026-10-18）

范围：**提示词安全**、**MCP 查询证据链**、**用户记忆结构化** 三条独立专项；外加 FW-09 官方 checkpointer 默认化（同轮完成）。全部未 git commit。

## 1. 提示词安全

| 项 | 内容 |
| --- | --- |
| 改动 | `config/prompts/system.md` 强化专家从属；新增 `config/prompts/fragments/20-prompt-security.md`（指令层级 R1–R6）；新增 `prompt_guard.py`（`wrap_expert_prompt` / `assemble_system_prompt`）；`agent.py` 装配改为固定顺序「基础规则 → 用户上下文 → 联网附加 → 从属补充 → HITL（最后）」；`subagents.py` 增加子 Agent 安全规则；同步 `prompt_loader.DEFAULT_SYSTEM_PROMPT` 与文件终稿一致 |
| 效果 | 明确「系统/开发者 > 用户 > 外部数据」；外部文件/网页/连接器/知识库/工具描述只当数据；系统提示词保密；专家内容强制包裹且不能绕过 |
| 测试 | 新增 `tests/test_prompt_security.py` 12 项；`test_system_prompt.py` 契约测试恢复通过；相关 16+ passed |
| 设计 | `docs/design/2026-10-18-prompt-security.md` |

## 2. MCP 查询证据链

| 项 | 内容 |
| --- | --- |
| 改动 | 新增 `services/mcp_evidence.py`：`MCPEvidence` 信封（server/tool/ok/is_error/fetched_at/truncated/content/error）；`wrap_mcp_tool(s)` 透传包装；`services/mcp.py:get_mcp_tools()` 返回前按 server 包装 |
| 效果 | 每次 MCP 工具输出带来源标记、时间、错误/截断标记；异常与文本错误显式标注；默认 20k 截断（`EASY_MCP_MAX_CONTENT_CHARS` 可调）；工具名/描述/参数 schema 不变 |
| 测试 | 新增 `tests/services/test_mcp_evidence.py` 18 项；MCP transport/config + connector 回归 50 passed |
| 设计 | `docs/design/2026-10-18-mcp-evidence.md` |

## 3. 用户记忆结构化

| 项 | 内容 |
| --- | --- |
| 改动 | 新增 `services/memory_store.py`（MemoryEntry/JSONL 原子写/校验脱敏/去重 supersede/Markdown 解析渲染/预算裁剪/旧文件导入）；`services/memory_manager.py` 集成：成功写 JSONL + Markdown，失败回退旧路径；日志不再打印记忆全文 |
| 效果 | 结构化条目 + 来源/版本；密钥/PII 启发式脱敏；Markdown 仍是权威读取文件，旧代码/提示词路径不变；`EASY_STRUCTURED_MEMORY` 默认开（=0 回退旧行为） |
| 测试 | 新增 `tests/services/test_memory_store.py` 26 项 + 现有 memory 测试，共 36 passed |
| 设计 | `docs/design/2026-10-18-structured-memory.md` |

## 4. FW-09 官方 checkpointer 默认化（同轮）

- `EASY_CHECKPOINT_BACKEND=auto|postgres|sqlite|legacy`（默认 auto）：PG URI → 官方 AsyncPostgresSaver；SQLite 业务库 → 官方 AsyncSqliteSaver（默认 `data/checkpoints.db`）；MySQL 无 PG URI → 回退自研 + warning；legacy 强制自研。
- 启动失败降级 postgres→sqlite→legacy，不阻断启动；shutdown 关闭并清理。
- 报告：`docs/audit/2026-10-17-increment38-fw09-report.md`；测试 `tests/services/test_checkpoint_backend.py` 23 项。

## 5. 集成验证

| 检查 | 结果 |
| --- | --- |
| 全量 `pytest tests/` | **1077 passed, 1 skipped, 0 failed, 0 error（241.9s）** |
| `uvx ruff check .` | All checks passed |
| `uv lock --check` | 通过（118 packages） |
| prompt 契约 | `DEFAULT_SYSTEM_PROMPT` 已与 `system.md + fragments` 同步；`test_builtin_prompt_matches_prompt_file` 通过 |

## 6. 已知风险 / 未做

1. 提示词安全是软约束；未做工具描述入库清洗、输出密钥扫描、输入侧 injection 分类器。
2. MCP 文本错误启发式可能误报/漏报；20k 截断可能截断大表格；异步-only 工具同步调用会返回 `is_error`。
3. 记忆：无跨进程文件锁（同用户多会话仍 last-writer-wins）；PII 扫描为启发式；预算裁剪有损；旧 Markdown 明文要等下一次结构化写入才清洗；sidecar 在 agent 可访问目录，可被误删（可由 Markdown 重建）。
4. 多租户 `tenant_id`、多副本 Phase B/C、streaming 深度拆分、HITL 策略未做（用户暂缓/未选）。

## 7. 环境变量（已补 `.env.*.example`）

`EASY_CHECKPOINT_BACKEND`、`EASY_CHECKPOINT_SQLITE_PATH`、`EASY_STRUCTURED_MEMORY`、`EASY_MCP_MAX_CONTENT_CHARS`。
