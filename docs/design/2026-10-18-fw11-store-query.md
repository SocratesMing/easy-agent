# FW-11 遗留：SqlStore 语义检索（2026-10-18）

## 现状

- `easy_agent/stores.py:SqlStore._search()` 已支持 `filter` 操作符与“先过滤后分页”，但 **忽略 `SearchOp.query`**（无 embedding/向量列）。
- 生产启用 `EASY_STORE_BACKEND=postgres` 且官方 `langgraph.store.postgres.AsyncPostgresStore` 可用时，语义检索由官方 store 负责（需要 pgvector/embedding 配置）；当前默认/MySQL 回退仍为 `SqlStore`。
- 官方 `BaseStore.search(query=...)` 的语义是 embedding 相似度；在 MySQL/SQLite 上实现需要：
  1. 选择 embedding 模型与维度；
  2. 新建向量列/表（MySQL 无原生向量索引，需应用层余弦计算或外部向量库）；
  3. 索引构建、迁移、回填、失败降级；
  4. 与 `SqlStore` 现有 filter/分页语义合并。

## 决策

本轮**不实现** SQL 侧语义检索，原因：
- 无可用 embedding 配置/向量存储，强实现会把可选记忆能力变成外部依赖；
- 官方 PostgresStore 已覆盖该能力，B1 已提供 `EASY_STORE_BACKEND=postgres` 可选路径；
- 现有 DeepAgents 记忆/跨线程用法不依赖 `query`，无内建消费者。

## 后续建议

1. 若确认需要 SQL 语义检索：引入 embedding 服务 + `store_embeddings` 表（JSON/二进制向量），在 `SqlStore._search` 中做“filter 后余弦排序 + limit/offset”，并补迁移与降级。
2. 或统一迁移到官方 PostgresStore（需 pgvector + 运维 Postgres），删除 SQL 分支。
3. 无论哪条，必须先补一致性/性能测试与迁移文档，不在当前批次内实施。
