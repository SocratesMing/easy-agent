# Increment 22 执行报告（PERF-14：知识库轮询 worker leader 选举）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 21） |
| 依据 | 审计 PERF-14（多副本下知识库解析轮询被 N 个副本重复执行，重复调用 Ragflow） |
| 约束 | 分布式锁未启用（单机）时行为完全不变 |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| leader 选举 | `KnowledgeParsePollWorker.poll_once()` 先抢 `knowledge:parse-poll` 分布式锁（TTL=max(30s, 3×轮询间隔)），未抢到直接跳过本轮；`try/finally` 保证释放 | 多副本下每轮只有一个副本真正调用 Ragflow；单机因 `LockHandle.acquired=True` 行为不变 |
| 结构 | 新增 `_poll_locked(db)` 承载实际轮询；`lock_provider` 可注入（默认 `get_distributed_lock`） | 便于测试与替换实现 |
| 测试 | 新增 `tests/knowledges/test_worker_leader.py` 2 项：非 leader 跳过且释放、leader 执行并释放；`tests/knowledges` 81 passed | 并发语义有回归保护 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 相关知识库测试 | 81 passed |
| 全量 `pytest tests/` | **965 passed, 1 skipped, 0 failed, 0 error（239.4s）** |
| `uvx ruff check .` | 通过 |
| 兼容性 | 分布式锁关闭时 `LockHandle.acquired=True`（单实例路径不变）；锁开启时按 TTL 自动过期，单副本故障不会永久阻塞 |

## 3. 未完成/后续

1. 锁 TTL 到期与单轮耗时关系：单轮超过 TTL 时可能被另一副本并行执行；后续可在处理每个 base 前续约（`renew`）或按 base 加锁。
2. ARCH-08 API 契约、FW-06/09/10、多副本 Phase B、ARCH-03 Phase 3 未开始。
3. Batch 0/1 安全项（4 P0 + 8 P1）仍未执行。
