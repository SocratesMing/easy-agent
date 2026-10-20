# Increment 10 执行报告（ARCH-05 后端常量收口 + schema 注册时序修复）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 9） |
| 依据 | 审计 ARCH-05；schema 注册制后续问题（Round 4 引入） |
| 约束 | 事件/块 schema 兼容；不改变 SSE 线上格式 |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| `SSEEvent` 常量类 | `services/sse.py` 新增 15 个事件常量并导出 | 生产端不再手写事件字符串 |
| 生产端引用常量 | `stream_processor.py`、`streaming.py`、`api/chat.py`、`api/expert.py`、`knowledges/chat/chat_bridge.py` 的事件字面量全部替换为 `SSEEvent.*`（持久化 block 类型仍用字符串，保持历史数据兼容） | 契约测试新增“禁止事件字面量 / 生产端引用常量”断言 |
| schema 注册时序修复 | `knowledges/__init__.py` import 时显式注册 schema（`from .core import schema`） | 修复 schema 注册制引入后的真实缺陷：只启动 app 而不先 import 知识库测试模块时，`Database.init_tables()` 不建 knowledge 表（`tests/api/test_chat.py` 单独运行为此失败） |
| 回归测试 | `tests/services/test_sse_contract.py` 扩展生产端常量检查 | 5 项通过；`tests/api/test_chat.py` 全部恢复 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| `tests/api/test_chat.py` | 全部通过（修复前单跑 10 项失败：`no such table: session_knowledge_scopes`） |
| `tests/services/test_sse_contract.py` | 5 passed |
| 全量 `pytest tests/` | **955 passed, 1 skipped, 0 failed, 0 error（245.5s）** |
| `uvx ruff check .` | 通过 |

## 3. 未完成/后续

1. ARCH-08/09/16、ARCH-03 Phase 2/3、FW-06/09/10 未开始。
2. Batch 0/1 安全项（4 P0 + 8 P1）仍未执行。
