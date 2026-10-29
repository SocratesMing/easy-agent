# Increment 35 执行报告（ARCH-08 完成：Connectors 全端点 response_model）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 33） |
| 依据 | 审计 ARCH-08；connectors 为剩余高价值批次 |
| 约束 | JSON 字段不变；仅声明/校验 schema |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| 新增模型 | `ConnectorToggleResponse`、`SessionConnectorSwitchesResponse`、`SessionConnectorSetResponse`、`ConnectorToolInfo/ConnectorToolsResponse`、`ConnectorConfigUpdateResponse`、`McpRaw/Save/Add/DeleteResponse` | Connectors 全部端点契约显式化 |
| 路由声明 | toggle/session GET+POST/tools/config/mcp raw GET+PUT/mcp POST/delete 共 9 个端点加 `response_model` | OpenAPI schema 稳定 |
| 回归 | `tests/api/test_connectors.py` 30 passed | 字段无变化 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 相关测试 | 30 passed |
| 全量 `pytest tests/` | **984 passed, 1 skipped, 0 failed, 0 error（237.7s）** |
| `uvx ruff check .` | 通过 |
| 兼容性 | `/status`、`/install`、`/uninstall` 及本轮 9 个端点 JSON 字段均保留 |

## 3. 后续

1. ARCH-08 剩余：knowledge 路由批次、expert 路由（裸挂根、6 路由）单独处理。
2. 多副本 Phase B、streaming 深度拆分、HITL 策略暂缓（用户指示）；Batch 0/1 安全项未执行。
