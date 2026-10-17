# Increment 28 执行报告（ARCH-08 分批：Settings 路由 response_model）

| 项 | 内容 |
| --- | --- |
| 执行时间 | 2026-10-17（Round 27） |
| 依据 | 审计 ARCH-08（`response_model` 覆盖率低、handler 返回裸 dict） |
| 约束 | 响应字段保持完全一致（仅声明 schema，不裁剪字段） |

## 1. 本轮完成项

| 项 | 改动 | 效果 |
| --- | --- | --- |
| 新增响应模型 | `models/api.py`：`MemoryContentResponse/MemoryUpdateResponse/ContentResponse/SkillInfo/SkillsResponse/ModelInfo/ModelsResponse/WebSearchStatusResponse/UploadLimitsResponse` | Settings 域契约显式化 |
| 路由声明 | `api/settings.py` 7 个端点加 `response_model`（memory GET/PUT、system-prompt、skills、models、web-search、upload-limits） | OpenAPI 有稳定 schema，前端/代码生成可依赖 |
| 契约测试 | 新增 `tests/api/test_openapi_contract.py`：校验 6 个 GET 路径的 200 schema 存在且非空 | 防止后续改回裸 dict |
| 测试 | `tests/api/test_settings.py` 8 passed（响应字段未变） | 无行为回归 |

## 2. 验证

| 检查 | 结果 |
| --- | --- |
| 相关测试 | 8 passed（settings + openapi contract） |
| 全量 `pytest tests/` | **980 passed, 1 skipped, 0 failed, 0 error（243.0s）** |
| `uvx ruff check .` | 通过 |
| 兼容性 | 返回 JSON 字段不变；`response_model` 仅用于校验/文档 |

## 3. 未完成/后续（ARCH-08 完整项）

1. 其余 ~80 个路由仍未声明 `response_model`；按域分批推进（auth/sessions/files/expert/knowledge/connectors）。
2. 三套路由前缀统一 + `/api/*` 别名 deprecation 未做（需前端同步，属 L 项）。
3. 多副本 Phase B、ARCH-03 Phase 3 未开始；Batch 0/1 安全项未执行。
