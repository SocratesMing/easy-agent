# Increment 43：B3 第二批共享限流计数（2026-10-18）

| 项 | 内容 |
| --- | --- |
| 目标 | 多副本共享限流计数，避免 N 副本阈值放大 N 倍 |
| 状态 | ✅ 完成（SQLite/MySQL 共用实现；Redis 未做） |

## 1. 改动

| 文件 | 内容 |
| --- | --- |
| `easy_agent/db/schema.py` | `SCHEMA_VERSION` 6→7；`EXPECTED_TABLES` 增加 `rate_limit_counters`；新增固定窗口表 DDL |
| `easy_agent/middleware/rate_limit.py` | 新增 `DBRateLimitCounter`（`is_shared=True`）；`EASY_RATE_LIMIT_BACKEND=auto|db|memory`；默认 auto：多 worker/副本选 DB、单机选内存；DB 故障降级进程内并告警一次 |
| `.env.*.example` | 增加 `EASY_RATE_LIMIT_BACKEND` 注释 |
| `tests/middleware/test_rate_limit_shared.py` | 5 项：限额/窗口重置/key 隔离/DB 故障降级/选择规则/schema 表 |
| `tests/middleware/test_rate_limit.py` | 多副本告警用例显式传内存计数器（auto 现在选 DB） |

## 2. 实现要点

- 表 `rate_limit_counters(bucket, window_start, count, updated_at)`，主键 `(bucket, window_start)`。
- 原子流程：`UPDATE ... count=count+1 WHERE count < limit` → rowcount=1 放行；否则 `INSERT` 当前窗口；INSERT 冲突则重试一次条件 UPDATE（处理并发窗口创建竞态）。
- 每次调用清理本 bucket 上一窗口之前的行，避免大基数 IP 无界增长。
- 计数器不可用（DB 未初始化/异常）→ 降级 `InMemorySlidingWindowCounter` 并 warning 一次，不阻断业务。
- 多副本 auto 选择 DB 后中间件不再打印“进程内计数”告警；`EASY_RATE_LIMIT_BACKEND=memory` 可强制回退。

## 3. 验证

| 检查 | 结果 |
| --- | --- |
| `tests/middleware` + `tests/db` | 88 passed |
| 全量 `pytest tests/` | **1119 passed, 1 skipped, 0 failed, 0 error（243.7s）** |
| `scripts/import_graph.py --check-scc` | OK（200 modules / 417 edges / 1 SCC(16)） |
| `scripts/check_double_write.py` | OK |
| `uvx ruff check .` | All checks passed |

## 4. 未做/风险

1. Redis 共享实现未做；DB 计数每个受限请求额外 1–2 次 DB 往返（仅登录/聊天入口，可接受）。
2. 固定窗口在窗口边界可能出现约 2× 瞬时放行；如需严格滑动窗口需存事件行/Redis ZSET。
3. 表随启动 DDL 创建；滚动发布时新旧 Pod 均兼容（新表 IF NOT EXISTS）。
4. 未做 Ingress 层限流、JWT 轮换/kid。
