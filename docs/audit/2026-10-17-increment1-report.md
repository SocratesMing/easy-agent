# Increment 1 执行报告（Batch 2 / 3 / 4 快速项）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17 |
| 依据 | `docs/audit/2026-10-17-easy-agent-audit.md` 第 7 章；用户确认范围：Batch 2（性能并发）、Batch 3（框架规范）、Batch 4（架构工程化） |
| 本轮未执行（用户选择） | Batch 0/1 安全项（含 Web Terminal、SPA 穿越、免密登录、MCP stdio RCE 等）；前端双基线保持同时维护；生产强制 MySQL + 单机 SQLite WAL |
| 代码状态 | 基于仓库原有未提交改动之上增量修改，未回滚任何既有工作；未执行 git commit |

## 1. 本轮完成项

| 计划项 | 状态 | 主要改动 | 验证 |
| --- | --- | --- | --- |
| ARCH-01 测试可收集性 + 沙箱测试漂移 | ✅ | `tests/integration/test_sandbox_isolation.py` 改为从 `easy_agent.fs_safety`/`easy_agent.backends.shell`/`easy_agent.hitl` 导入与 patch（原 import 已失效）；`test_auth_session_expiry.py` 因针对已移除的 `_user_activity_cache`，改为**带原因的模块级 skip**（待按新 DB 契约重写，审计项 ARCH-11） | `pytest --collect-only`：**926 collected，0 error**（原 900 collected + 1 error）；沙箱 15 项全通过 |
| PERF-01 thinking O(n²) | ✅ | `stream_processor.py`：`full_content` 由「每 chunk 全量」改为「首包/1.5s/增长 8K 字符快照」；同时用 `_thinking_step_content` 增量拼接替代每 chunk 对全量字符串切片；`api/chat.py` 的 `_StreamHub.history` 增加 4M 字符上限，并改用 `deque` O(1) 淘汰 | 单测 60 项通过；新增 hub 上限测试 2 项；SSE 体积由 O(n²) 降为 O(n)（设计口径） |
| PERF-04 流式落库写放大 | ✅ | 删除 7 处重复 `update_last_assistant_message_row`（`update_last_assistant_message` 内部已转调）；增量落库由「每 500 字符」改为「≥500 字符且（≥2s 或 ≥20K 字符）」双阈值 | `streaming.py` 编译通过；全量套件回归 |
| PERF-07 SQLite 单机性能 | ✅ | `db/database.py`：`journal_mode=WAL` + `synchronous=NORMAL` + `busy_timeout=30000` + `timeout=30`（WAL 不支持时降级告警，不阻断启动） | 全量测试通过；审计报告中“未开 WAL”项关闭 |
| PERF-02 同会话并发流 | ✅ | `agent_manager.register_stream_task` 不再 `cancel()` 旧任务，改为抛 `SessionBusyError`；新增 `is_session_streaming`；`chat/stream`、`chat/resume` 在写入用户消息前返回 **409**；`_detached_event_stream` 入口兜底独立错误流；`_drive` 被拒时只结束本请求队列、不污染共享 hub；新增 `tests/services/test_stream_concurrency.py` 2 项 | 2 项并发互斥测试通过；全量回归 |
| PERF-09 Agent 缓存 | ✅ | `_session_agents` 改 `OrderedDict` + 命中 `move_to_end`（LRU）；淘汰时同步清理 4 个 side dict 与空闲锁（普通 dict 环境下自动降级，兼容测试替换） | 全量 services 测试通过 |
| PERF-11 轻量归属查询 | ✅ | `Database.get_session_owner()` + `get_owned_session_meta()`；文件 API、chat 取消/状态/resume 改用轻量查询，避免 `get_session()` 全量加载消息 | 53 项相关测试通过 |
| FW-01 版本上界 + 契约测试 | ✅ | `pyproject.toml`：`deepagents>=0.6.8,<0.7`、`langchain>=1.3,<1.4`、`httpx` 去重；新增 `tests/test_framework_contract.py`（6 项：版本窗口、`create_deep_agent` 公开参数、FilesystemMiddleware 注入点、摘要工厂、HarnessProfile）；新增 `docs/framework-integration.md`；`uv lock` 已更新且 `uv lock --check` 通过（deepagents 0.6.8 / langchain 1.3.4 / langgraph 1.2.4 未变） | 契约测试 6 项通过；lock check 通过 |
| FW-02 HITL thread 绑定 | ✅ | `chat_resume` 校验 `thread_id.startswith(f"{session_id}-")`，非法返回 400；防止 Postgres checkpointer 全局共享时跨会话读取/续跑 | 全量回归 |
| FW-03 MCP 工具名冲突 | ✅ | `services/mcp.py`：MCP 工具与内建工具（`ls/read_file/write_file/edit_file/glob/grep/execute/task/write_todos`）同名时跳过并告警；跨 server 重名保留第一个；`invalidate_mcp_cache` 同步清理 client 缓存防泄漏 | 全量回归 |
| ARCH-02 打包/部署 | ✅ | 删除指向不存在模块的 `easy-knowledge-worker` 入口；新增 `[tool.setuptools.package-data]`（config/prompts/static）；新增 `MANIFEST.in`；`Dockerfile` 增加 `COPY skills/ prompts/ experts/`；`.dockerignore` 放行三者的 `.md` | `uv lock --check` 通过；镜像构建未在本机执行（需 Docker 环境，见“未验证项”） |

## 2. 验证结果

| 检查 | 结果 |
| --- | --- |
| `pytest --collect-only -q` | **926 collected，0 error**（修复前：900 collected + 1 collection error） |
| 全量 `pytest tests/` | **914 passed, 1 skipped, 0 failed, 0 error（227.9s）**；基线：890 passed / 10 failed / 13 errors + collection error |
| 变更文件编译 | `py_compile` 全部通过 |
| `uv lock --check` | 通过（版本窗口收紧后已重新生成 lock） |
| 新增/更新测试 | 框架契约 6、hub 上限 2、并发互斥 2、stream_processor 契约 2、沙箱测试 15 |

## 3. 未验证 / 待后续

1. **Docker 镜像构建与容器内启动**：本机未执行 `docker build`（缺 Docker 环境）；`COPY` 路径与 `.dockerignore` 已按仓库结构核对，建议在发布流水线验证镜像内 `discover_skills()` ≥ 7、预置专家 ≥ 5。
2. **真实 provider 的 thinking chunk 粒度**：PERF-01 的收益按 chunk 数线性成立；真实环境建议用一条 ≥2 分钟 thinking 回归 SSE 字节量（目标 < 5MB）。
3. **多副本/MySQL 路径**：本轮只改 SQLite 单机 pragma；k8s 多副本强制 MySQL、分布式锁默认开启属于部署配置，未在无集群环境下实测。
4. **`test_auth_session_expiry.py` 重写**：按新 DB 持久化契约补测（ARCH-11）。
5. **剩余审计项**（未在本轮）：PERF-03 checkpoint 全量快照、PERF-05 SessionLogger、PERF-06 解析/转换线程池、PERF-08 调度多副本、FW-04/05/06/07/08/09/10、ARCH-03/04/05/06/07/08/09/10/11/15/16/18 等。

## 4. 安全提醒（未在本轮执行）

用户本轮未选择 Batch 0/1，因此审计报告中的 4 个 P0（未认证 Web Terminal、SPA 任意文件读、免密登录接管、MCP stdio RCE）与 8 个 P1 **仍然存在**。建议在任何对外暴露前单独排期修复；本轮改动不涉及这些攻击面（也不会使其恶化）。
