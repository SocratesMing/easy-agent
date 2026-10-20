# Increment 27 执行报告（FW-11：SqlStore filter/分页语义补全）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 26） |
| 依据 | 审计 FW-11（自研 `SqlStore` 语义不完整：filter 只支持直接相等、先 LIMIT 后过滤导致丢页） |
| 约束 | 保持既有 `get/put/delete/search/list_namespaces` 调用兼容；不引入向量检索 |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| filter 操作符 | `SqlStore._matches_filter`：支持直接相等 + `$eq/$ne/$gt/$gte/$lt/$lte/$in/$nin`；未知操作符保守不匹配 | 与 LangGraph BaseStore 常用 filter 语义对齐 |
| 过滤后分页 | `_search` 改为按批扫描（limit=max(200, op.limit)）→ 内存过滤 → 收够 `offset+limit` 后切片；扫描上限 `_SEARCH_SCAN_LIMIT=5000` | 修复“SQL 先 LIMIT 再过滤导致结果少页”；大表有界 |
| 测试 | 新增 `tests/services/test_sql_store.py` 3 项：等值/操作符、过滤后分页、get+delete | 语义有回归保护 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 相关测试 | 3 passed |
| 全量 `pytest tests/` | **979 passed, 1 skipped, 0 failed, 0 error（244.4s）** |
| `uvx ruff check .` | 通过 |
| 兼容性 | `BaseStore` 接口不变；`search(list)` 的结果顺序仍按 SQL `(namespace, item_key)` |

## 3. 未完成/后续（FW-11 完整项）

1. 语义检索 `query` 参数未实现（当前忽略；需要 embedding + 向量列，与官方 `InMemoryStore`/Postgres store 对齐是独立 L 项）。
2. `list_namespaces` 的 match_conditions 仅支持 prefix/suffix（官方还支持 wildcard）；当前无消费者，暂不扩展。
3. 多副本 Phase B、ARCH-03 Phase 3 未开始；Batch 0/1 安全项未执行。
