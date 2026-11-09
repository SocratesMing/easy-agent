# Increment 8 执行报告（流锁 heartbeat + 缓存/归属回归测试）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 7） |
| 依据 | 审计 CONC/PERF-02、PERF-09/11/15；`docs/design/2026-10-17-multi-instance-state.md` Phase A 剩余 |
| 约束 | 不改变 SSE/API 契约；不新增外部依赖 |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| 流锁 TTL 续约 | `Database.renew_session_stream()`：仅当 `stream_owner` 仍是自己时延长 `stream_until`；`session_runtime.renew_stream()` 包装；`api/chat.py` 后台流任务每 5 分钟续约（TTL 30 分钟），owner 被接管时停止并告警 | 长任务不再因超过 TTL 被其它副本误判“锁过期”而双跑 |
| heartbeat 生命周期 | `_drive()` 内创建/取消 heartbeat 任务；仅在成功注册流任务后启动，异常/取消时清理 | 不污染共享 hub，不泄漏协程 |
| 回归测试补齐 | 新增/扩展：`tests/db/test_session_stream_lock.py`（+2 续约用例）、`tests/services/test_agent_manager_cache.py`（4 项 LRU/side dict/plain-dict 兼容/流状态）、`tests/db/test_session_owner.py`（2 项轻量归属与 legacy 回退） | 对 PERF-09/11/15 与流锁行为形成显式回归保护 |
| 设计文档 | `multi-instance-state.md` Phase A 更新（heartbeat 完成，剩余 Phase B/C/D） | 设计→实现可追溯 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 新增/扩展测试 | 8 项全部通过（流锁续约 2、缓存 4、归属/legacy 2） |
| 全量 `pytest tests/` | **954 passed, 1 skipped, 0 failed, 0 error（236.8s）** |
| `uvx ruff check .` | 通过 |
| 兼容性 | SQLite/MySQL 单实例行为不变；续约失败只告警（不中断当前流） |

## 3. 未完成/后续

1. heartbeat 的集成级测试（需要可控时钟/短 TTL）未做；当前由 DB/服务层单测覆盖核心条件更新。
2. Phase B（多副本 SSE 事件外置）、Phase C（Agent 缓存/租户）、Phase D（调度 leader 选举）未开始。
3. ARCH-05 后端常量引用、ARCH-08/09/16、ARCH-03 Phase 2/3、FW-06/09/10 未开始；Batch 0/1 安全项未执行。
