# Increment 23 执行报告（FW-10：MCP transport 默认值与别名归一化）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 22） |
| 依据 | 审计 FW-10（URL-only 配置默认 `sse` 旧传输；`streamable-http`/`http` 写法未归一化） |
| 约束 | 显式 `type: sse` 的现有配置保持兼容 |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| 默认传输 | `normalize_servers_mapping()`：URL-only 不再默认 `sse`，改为现代 `streamable_http`；`command` 仍默认 `stdio` | 新配置默认走 MCP streamable HTTP 传输 |
| 别名归一化 | 新增 `_normalize_transport`：`streamable-http` / `streamablehttp` / `http` → `streamable_http`；`sse`/`stdio`/`websocket` 保持 | 避免适配器因写法差异拒绝连接；去掉 `type` 键统一为 `transport` |
| 兼容 | 显式 `transport: sse` 配置不变；`type: sse` 同样保留 | 旧 MCP server 无需改配置 |
| 测试 | 新增 `tests/services/test_mcp_transport.py` 5 项；更新 legacy 测试中对 `http` 别名的期望 | MCP/connectors 相关 44 passed |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 相关测试 | 44 passed（transport/config/connectors API） |
| 全量 `pytest tests/` | **970 passed, 1 skipped, 0 failed, 0 error（243.8s）** |
| `uvx ruff check .` | 通过 |
| 文档 | `docs/framework-integration.md` 补充 URL-only 默认值与别名说明 |

## 3. 未完成/后续

1. FW-10 的 timeout/sse_read_timeout 透传与 OAuth 仍未做（当前配置字段原样传给适配器，未做校验/默认值）。
2. FW-09（官方 saver）、FW-06（skills 多源）、多副本 Phase B、ARCH-03 Phase 3 未开始。
3. Batch 0/1 安全项（4 P0 + 8 P1）仍未执行。
