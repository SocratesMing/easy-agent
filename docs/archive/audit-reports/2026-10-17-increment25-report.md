# Increment 25 执行报告（ARCH-08 部分：统一 API 错误体 + 连接器请求模型修正）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 24） |
| 依据 | 审计 ARCH-08（错误体/契约不一致；`ConnectorInstallRequest.validate` 字段遮蔽告警） |
| 约束 | 保留前端现有 `detail` 字段与 `validate` JSON 契约 |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| 统一 HTTP 错误体 | 新增 `StarletteHTTPException` handler：`{"detail": ..., "code": "HTTP_<status>", "request_id"?}`；透传原 headers（含 401 `WWW-Authenticate`） | 客户端可稳定按 `code` 分支；`detail` 兼容旧前端 |
| 统一 422 校验错误体 | 新增 `RequestValidationError` handler：`detail` 固定文案 + `code=VALIDATION_ERROR` + `errors`（jsonable） | 不再把 Pydantic 内部对象直接返回；错误结构可预期 |
| 连接器模型告警 | `ConnectorInstallRequest.validate` 更名为 `validate_after_install`，用 `Field(alias="validate")` 保持 JSON 契约 | 消除每个测试都出现的 `Field name "validate" ... shadows` 警告 |
| 测试 | 新增 `tests/api/test_error_contract.py` 2 项（404 code/detail、422 code/errors）；connectors 30 passed | 契约有回归保护 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 相关测试 | 28 passed（error contract + auth）；connectors 30 passed |
| 全量 `pytest tests/` | **975 passed, 1 skipped, 0 failed, 0 error（236.1s；warnings 4→3）** |
| `uvx ruff check .` | 通过 |
| 兼容性 | `detail` 保持；`validate` JSON 字段仍被接受（alias）；401 headers 透传 |

## 3. 未完成/后续（ARCH-08 完整项）

1. 137 路由的三套前缀（`/agent/*`、`/api/*`、裸 expert 路由）与 `/api/*` 别名 deprecation 未做。
2. `response_model` 覆盖率（约 50/137）与 handler `return {}` 内联模型迁移未做；需分端点批次推进。
3. FW-09 官方 saver、多副本 Phase B、ARCH-03 Phase 3 未开始；Batch 0/1 安全项未执行。
