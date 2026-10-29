# Increment 2 执行报告（Batch 2 收尾 + Batch 3 部分）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 1 续） |
| 依据 | `docs/audit/2026-10-17-easy-agent-audit.md`；用户确认范围 Batch 2/3/4 |
| 范围约束 | 不执行 Batch 0/1 安全项；Web Terminal 不动；前端双基线同时维护；生产 MySQL + 单机 SQLite WAL |

## 1. 本轮完成项

| 计划项 | 状态 | 主要改动 | 验证 |
| --- | --- | --- | --- |
| PERF-05 会话日志 O(n²) | ✅ | `utils/session_logger.py` 重写为 append-only JSONL（每条一行，O(1)），旧 `.json` 自动迁移且保留原文件；`streaming.py` 的每轮全量 `logs/sessions/*.json` 快照改为默认关闭（`EASY_WRITE_SESSION_CONTEXT=1` 才写），并去掉强制 `indent=2` | 新增 JSONL 单测 2 项（追加行数线性、旧格式迁移）；API/DB/services 408 项回归通过 |
| PERF-06 上传/解析/转换阻塞事件循环 | ✅ | 新增 `session_uploads.save_upload_streaming()`：分块 1MB 落盘 + 边读边限流（超限删除半成品，`UploadTooLargeError`）；`api/files.py`、`api/sessions.py` 上传改流式；parse/preview 转换/解析清单改 `asyncio.to_thread` | 新增流式上传单测 2 项（正常写入、超限清理）；408 项回归通过 |
| PERF-08 调度多副本重复执行 | ✅ | `scheduler.py` 新增 `_detect_worker_count()`（EASY_REPLICA_COUNT / WEB_CONCURRENCY / UVICORN_WORKERS）；多 worker 且 `distributed_lock.enabled=false` 时默认 **不注册任何定时任务**（fail-closed），可用 `EASY_ALLOW_DUPLICATE_SCHEDULER=1` 显式放开 | 新增调度保护单测 3 项 |
| PERF-03 checkpoint 全量快照/thread 无界 | ✅ | `checkpoints.py` 增加 `_prune_for_persistence()`：每 thread 保留最近 2 个 checkpoint、最多 3 个 thread，并清理无引用 writes/blobs；flush 前裁剪，持久化 blob 不再随轮数无限增长 | 新增裁剪单测 1 项；checkpoint roundtrip 测试通过 |
| FW-05 摘要私有工厂护栏 | ✅ | `summarization.py`：工厂缺失时显式 `logger.error` 并降级为框架默认阈值（不再 AttributeError 建图失败） | 编译 + 全量回归 |
| FW-07 subagent 规范 | ✅ | 新增 `agent.build_subagent_specs()`：每个 subagent 显式 `skills=["/user-skills/"]` + `LoggingMiddleware`（子代理工具异常兜底），不修改 `DEFAULT_SUBAGENTS` 常量 | 新增 subagent specs 单测 2 项 |
| FW-08 step 预算逃逸 | ✅ | `agent.py` call-limit 中间件改 `exit_behavior="error"`（超限显式报错，不静默结束）；主/恢复两条流 `stream_config["recursion_limit"] = max(2*max_steps, 50)`（约束子代理/工具循环） | 编译 + 全量回归；框架契约测试通过 |

## 2. 本轮未做（有明确理由）

| 项 | 原因 |
| --- | --- |
| FW-04 用 HarnessProfile 替代 FilesystemMiddleware monkeypatch | `deepagents.profiles` 在 0.6.8 标注 **beta**；现有 monkeypatch + 契约测试已能保证升级时显式失败。待 0.7 升级立项时一并迁移，避免在 beta API 上引入新风险（已写入 `docs/framework-integration.md`） |
| FW-06 skills 官方多源（公共技能只读挂载） | 当前“启用技能 = 复制到用户目录”是产品交互的一部分（技能中心开关/停用目录）；改多源需要同时改 skill_center/skill_library/停用语义，属设计级改动，应先出设计文档（Batch 4） |
| FW-09 官方 saver 替换自研 PersistentMemorySaver | 当前环境未安装 `langgraph-checkpoint-sqlite`；Postgres saver 已是可选路径。替换需部署形态决策（本地/单机/多副本），先保裁剪兜底 |
| FW-10 MCP 默认 transport 改 streamable_http | 现有用户 URL 连接器可能仅支持 SSE，默认切换是 breaking change；需迁移开关与灰度 |
| PERF-12 MCP 首消息延迟 | 需要连接器预热/后台加载的架构改造，归入 Batch 4 设计 |

## 3. 验证结果

| 检查 | 结果 |
| --- | --- |
| 新增单测 | 8 项（SessionLogger 2、上传 2、调度 3、checkpoint 1）+ subagent 2 项 = 10 项全部通过 |
| 受影响子系统回归 | `tests/api/test_files.py`、`test_sessions.py`、`test_scheduled_tasks.py`、`tests/db`、`tests/services` 共 **408 passed** |
| 全量 `pytest tests/` | **924 passed, 1 skipped, 0 failed, 0 error（245.0s）**（基线 914 passed / 1 skipped） |
| 编译 | 变更文件 `py_compile` 全部通过 |
| Ruff（致命规则 E9/F63/F7/F82） | `uvx ruff check .` → **All checks passed** |

## 4. 遗留风险 / 待后续

1. `build_subagent_specs` 给每个子代理加 `LoggingMiddleware`，会略微增加子代理调用日志量；如线上日志压力大可改为可配置。
2. `recursion_limit=2*max_steps` 对既有超长任务可能更早触发上限；默认 `max_steps=100` 时 200 个 superstep，若真实任务接近上限需要调大配置。
3. checkpoint 裁剪保留“最近 3 个 thread”：HITL 只能恢复最近的待审批 thread（对话会阻塞在审批，正常不会跨越 3 轮），如业务允许长期挂起审批需调整常量。
4. 上传流式落盘后，`files.py` 上传接口仍缺少 T3 报告的路径/鉴权问题（Batch 1 未执行），本轮未扩大改动。
