# Increment 7 执行报告（原子流锁 + 生产部署校验）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 6） |
| 依据 | 审计 CONC/PERF-02、ARCH-15 Phase A；用户决策「生产强制 MySQL」 |
| 约束 | 不改变 SSE/API 契约；单机 SQLite 仍可零依赖运行 |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| 原子会话流锁 | `Database.try_acquire_session_stream()`：条件 `UPDATE ... WHERE stream_owner='' OR stream_until < now` 原子抢占；行不存在时 INSERT，唯一键冲突回退 busy | 替换旧「先 GET 再 UPSERT」的 TOCTOU；跨副本并发只有一个实例能拿到流锁 |
| `session_runtime.acquire_stream` | 改用原子方法；同进程/跨实例占用统一抛 `SessionBusyError` | 与 `is_session_streaming`（进程内）形成双层互斥 |
| 生产部署校验 | 新增 `easy_agent/utils/deployment.py`：`detect_worker_count()` / `deployment_errors()` / `deployment_warnings()`；`app.py` 启动时 fail-fast：`AGENT_ENV=prod` + workers>1 + SQLite 直接拒绝启动；dev/test 仅告警 | 实现用户决策「生产强制 MySQL」，避免运行期 `database is locked`/丢消息 |
| 调度器复用检测 | `services/scheduler.py` 删除重复的 `_detect_worker_count`，改用 `utils.deployment.detect_worker_count` | 单一实现，避免两处阈值不一致 |
| 测试 | 新增 `tests/db/test_session_stream_lock.py` 5 项（原子抢占、过期抢占、20 并发唯一赢家、跨实例忙、释放后可重入）；`tests/utils/test_deployment.py` 5 项 | 覆盖并发/部署两条新逻辑 |
| 文档 | 设计文档 `multi-instance-state.md` Phase A 标记完成 | 设计 → 实现可追溯 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 新增单测 | 13 项（流锁 5 + 部署 5 + 调度 3）全部通过 |
| 全量 `pytest tests/` | **946 passed, 1 skipped, 0 failed, 0 error（241.7s）** |
| `uvx ruff check .` | 通过 |
| 兼容性 | 单进程/单实例 SQLite 行为不变；dev/test 多 worker 只告警 |

## 3. 未完成/后续

1. 流锁 TTL 续约（heartbeat）尚未实现：长任务超过 30 分钟仍需额外处理（设计文档 Phase A 剩余）。
2. 多副本 SSE 事件尚未外置（Phase B）；agent 缓存跨副本一致性未处理。
3. ARCH-05 后端生产端统一引用 `services/sse.py` 常量未做（有契约测试兜底）。
4. ARCH-08/09/16、ARCH-03 Phase 2/3、FW-06/09/10 未开始；Batch 0/1 安全项未执行。
