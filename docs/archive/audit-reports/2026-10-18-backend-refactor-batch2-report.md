# 后端架构重构批次 2：memory_store / expert API

## 2A. services/memory_store.py

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `memory_store.py` | 1228 → 578 | `MemoryStore`、结构化写入、Markdown 同步 |
| `memory_locks.py` | 新增 245 | 跨进程锁、进程内锁、原子写、锁状态 |
| `memory_entries.py` | 新增 542 | MemoryEntry/校验/合并/Markdown 解析渲染/摘要/脱敏 |

- AST 证明 40 个顶层 def/class、34 个顶层赋值逐字节一致；`__all__` 37 项不变。
- 旧导入路径全部 re-export，对象身份 `is` 一致。
- 唯一测试调整：3 处 monkeypatch 从 `memory_store._fcntl/...` 改到 `memory_locks`。

## 2B. api/expert.py

| 文件 | 行数 | 职责 |
| --- | --- | --- |
| `api/expert.py` | 1268 → 678 | 4 个请求模型 + repository provider + 6 条路由 |
| `api/expert_helpers.py` | 新增 673 | 20 个非路由辅助函数（prompt/precheck/stream/relevance/persist） |

- 路由 URL/方法/tags/响应/SSE 不变；旧内部名字 re-export，`is` 一致。
- AST 证明 20 个移动函数 + 12 个保留定义逐字节一致。
- 测试调整：14 处 monkeypatch 目标改到 `expert_helpers`（实现已迁移）。

## 验证

| 检查 | 结果 |
| --- | --- |
| 全量 pytest（并发两批合并后） | **1582 passed / 1 skipped / 0 failed（289.8s）** |
| memory 定向 | 92 passed |
| expert 定向 | 70 passed |
| ruff | All checks passed |
| import 图 | 0 SCC（235 modules / 550 edges） |
| double-write | OK |

## 风险

1. 私有 monkeypatch 语义变化：外部若 patch 旧模块私有名/实现名，需改到新模块。
2. `expert_helpers.py` 673 行、`memory_entries.py` 542 行仍偏大，可继续按
   prompt/precheck/stream 与 entry/codec 拆分。
3. 无行为改动；API/SSE/存储格式契约不变。
