# docs 目录索引

本目录仅保留仍在维护的架构、契约、运维与现行设计文档；历史归档、阶段性审计与一次性验收报告已清理（如需追溯可查 Git 历史）。

## 现行文档（维护中）

| 文档 | 内容 |
| --- | --- |
| [architecture-current.md](architecture-current.md) | 当前后端架构、分层与依赖方向 |
| [framework-integration.md](framework-integration.md) | DeepAgents / LangGraph 集成方式与版本约束 |
| [sse-events.md](sse-events.md) | SSE 事件契约（前后端共同遵守） |
| [chat-behavior-contract.md](chat-behavior-contract.md) | 普通聊天与技能/专家边界行为契约 |
| [distributed-lock.md](distributed-lock.md) | 分布式锁与多实例协作 |
| [requirements-agent.md](requirements-agent.md) | 智能体需求与行为约束 |
| [token-usage-api.md](token-usage-api.md) | Token 用量统计 API 契约 |
| [upload-limits-design.md](upload-limits-design.md) | 上传限额与存储策略 |
| [API接口文档.xlsx](API接口文档.xlsx) | 接口总览 / 请求参数 / 响应参数（由 `scripts/docs/generate_api_docs.py` 生成） |

## 知识库文档（`docs/knowledge/`）

| 文档 | 内容 |
| --- | --- |
| [api-v1-contract.md](knowledge/api-v1-contract.md) | 知识库 v1 API 契约 |
| [team-space-management.md](knowledge/team-space-management.md) | 团队空间管理说明 |
| [p0-production-runbook.md](knowledge/p0-production-runbook.md) | 生产运维手册 |
| [p0-quality-baseline.yaml](knowledge/p0-quality-baseline.yaml) | 质量基线配置 |
| [p0-release-rollback.md](knowledge/p0-release-rollback.md) | 发布与回滚手册 |

## 现行设计（`docs/design/`）

- [2026-10-20-d4-mcp-tool-naming.md](design/2026-10-20-d4-mcp-tool-naming.md) — MCP 工具按 server 命名方案与兼容层
- [2026-10-20-da13-skills-sources.md](design/2026-10-20-da13-skills-sources.md) — 技能多来源与只读共享
- [2026-10-20-services-subpackages-and-layering.md](design/2026-10-20-services-subpackages-and-layering.md) — `services` 领域子包划分与分层门禁
- [2026-10-28-提示词增强设计.md](design/2026-10-28-提示词增强设计.md) — 提示词增强设计
- [2026-10-30-Token用量统计设计.md](design/2026-10-30-Token用量统计设计.md) — Token 用量统计设计
- [2026-10-30-会话手动归档管理设计.md](design/2026-10-30-会话手动归档管理设计.md) — 会话手动归档管理设计
- [2026-11-11-self-hosted-mysql-saver-plan.md](design/2026-11-11-self-hosted-mysql-saver-plan.md) — 自研 `AsyncMySQLSaver` 技术方案
- [2026-11-12-chat-visuals.md](design/2026-11-12-chat-visuals.md) — 对话内可视化（图表/流程图/交互组件）
- [2026-11-13-会话文件与文件夹引用设计.md](design/2026-11-13-会话文件与文件夹引用设计.md) — 当前会话文件与文件夹 `@` 引用设计
- [指令菜单于手动压缩设计.md](design/指令菜单于手动压缩设计.md) — `/` 指令菜单与手动压缩设计
