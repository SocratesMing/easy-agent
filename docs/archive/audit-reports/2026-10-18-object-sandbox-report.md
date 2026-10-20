# 对象存储 + Sandbox 执行报告（2026-10-18）

用户确认方案：
- 对象存储 = **数据库 LangGraph StoreBackend**（不是 S3）；
- Sandbox = **官方 LangSmithSandbox**（opt-in，默认 local bwrap/Landlock 不变）；
- 对象存储先覆盖**上传原始文件**。

## 1. 上传原始文件 → LangGraph StoreBackend

新增 `easy_agent/services/upload_storage.py`：

- `EASY_UPLOAD_BACKEND=local|store`（默认 local）；
- store 模式用 `StoreBackend(store=get_agent_store(), namespace=("uploads", safe_user))`，
  文件字节落 LangGraph Store；
- DB/API 记录 `store://uploads/{user}/{saved_name}`；
- 读取时 materialize 到 `data/uploads_cache/{user}/{name}`（原子写、0600/0700、跨用户拒绝）；
- 异常映射 503/403/404，不静默丢文件；
- 处理 Store 主键 VARCHAR(191) 截断，以及中文用户名 → ASCII 稳定命名空间别名。

接入：

- `/agent/files/upload`：store 模式写 Store、删本地临时副本；
- `/download`、`/parse`、`/preview`：store URI 先 materialize；
- `/list`（无 session 资产页）：store 模式从 Store 列举并回填真实 size；
- `delete`：删 DB 后尽力删 Store 对象；
- `session_uploads.store_parsed_content` / `_find_source_path`：解析前 materialize；
- 启动 `preflight_upload_store()`：store 模式 Store 不可用即 fail-closed。

## 2. LangSmith Sandbox（实验性）

新增 `easy_agent/backends/langsmith_sandbox.py`：

- `EASY_SANDBOX=local|langsmith`（默认 local，非法值 fail-closed）；
- `create_langsmith_backend()`：`SandboxClient().create_sandbox(snapshot_name=...)`
  + 官方 `LangSmithSandbox`；缺 key/SDK 不可用/创建失败清晰 RuntimeError；
- `LangSmithExecuteShell(_PathTranslatingShell)`：
  - 文件工具仍走本地 `/workspace`、`/memories`、`/user-skills`；
  - `execute` 前上传本地 workspace（排除 `.deps/.venv/node_modules/__pycache__`，
    单文件 >20MB 跳过），远端执行 `cd /workspace && command`，执行后回写远端文件；
  - 首次上传失败拒绝执行；回写失败只 warning；
- `agent.py`：仅在 `EASY_SANDBOX=langsmith` 时替换 default execute backend；
- `ContextBackend` 最小修复：声明 `SandboxBackendProtocol`/代理 execute，使 0.7
  共享图在带 permissions 时 execute 可达；真实文件路由不变。

## 3. 验证

| 检查 | 结果 |
| --- | --- |
| 全量 pytest | **1471 passed / 1 skipped / 0 failed（271.9s）** |
| 对象存储定向 | 84 + list 用例通过（含真实 SqlStore/sqlite 往返） |
| Sandbox 定向 | 17 passed（全 fake，不联网） |
| ruff | All checks passed |
| import 图 | 0 SCC（226 modules / 525 edges） |
| double-write | OK（153 methods / 115 delegations） |
| 前端/API/SSE 契约 | 未改 |

## 4. 明确未做 / 风险

1. **workspace / 生成文件 / 会话聊天 `uploadfiles/` 未迁移**：仍是本地 RWX；对象存储只覆盖 `/agent/files/upload` 的原始文件。
2. Store 模式大文件需整文件 bytes + base64 膨胀；建议 ≤20MB；无孤儿对象回收/TTL/历史迁移。
3. 未用 PostgresStore 实机联调（SQLite SqlStore 往返已测）。
4. LangSmith Sandbox 未做真实网络冒烟；无 destroy/快照持久化（依赖 idle TTL）；远端删除不回流；memories/skills/external 不同步；无文件数/总大小预算；workspace 全量上传有数据出境/费用风险。
5. 严格多副本门禁仍拒绝 `EASY_SANDBOX=langsmith`/`file_backend=store`（因为 workspace 未外置）；K8s 保持 strict=0。

## 5. 文档

- `docs/design/2026-10-18-object-store-uploads.md`
- `docs/audit/2026-10-18-increment64-object-store-report.md`
- `docs/design/2026-10-18-langsmith-sandbox.md`
- `docs/audit/2026-10-18-increment65-sandbox-report.md`
