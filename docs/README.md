# docs 目录索引

2026-10-20 做过一次整理：过程汇报与历史方案统一收进 `docs/archive/`，根目录只保留**仍在维护**的文档。归档文件仅在需要追溯决策时查阅，不再更新。

## 现行文档（维护中）

| 文档 | 内容 |
| --- | --- |
| [architecture-current.md](architecture-current.md) | 当前后端架构、分层与依赖方向 |
| [framework-integration.md](framework-integration.md) | DeepAgents / LangGraph 集成方式与版本约束 |
| [sse-events.md](sse-events.md) | SSE 事件契约（前后端共同遵守） |
| [distributed-lock.md](distributed-lock.md) | 分布式锁与多实例协作 |
| [requirements-agent.md](requirements-agent.md) | 智能体需求与行为约束 |
| [upload-limits-design.md](upload-limits-design.md) | 上传限额与存储策略 |
| [merge-history-2026-09-17-vue26.md](merge-history-2026-09-17-vue26.md) | vue26 合并冲突处理记录 |
| [frontend-vue2-改动说明.md](frontend-vue2-改动说明.md) | Vue2 前端的改动说明 |
| [API接口文档.xlsx](API接口文档.xlsx) | 接口总览 / 请求参数 / 响应参数 |
| [接口文档.xlsx](接口文档.xlsx) | 接口清单 / 模块索引 |

## 现行设计（尚未收口的方案）

- [2026-10-18-architecture-improvement-plan.md](design/2026-10-18-architecture-improvement-plan.md) — 后端改造总纲
- [2026-10-18-multi-replica-target.md](design/2026-10-18-multi-replica-target.md) — 多副本目标态
- [2026-10-20-da13-skills-sources.md](design/2026-10-20-da13-skills-sources.md) — 技能多来源

## 审计结论（长期保留）

- [2026-10-17-easy-agent-audit.md](audit/2026-10-17-easy-agent-audit.md) — 首轮全面审计
- [2026-10-17-final-acceptance.md](audit/2026-10-17-final-acceptance.md) — 首轮验收结论
- [2026-10-19-deepagents-best-practices-audit.md](audit/2026-10-19-deepagents-best-practices-audit.md) — DeepAgents 最佳实践审计

## 归档（不再更新，仅追溯用）

| 目录 | 内容 |
| --- | --- |
| [archive/audit-reports/](archive/audit-reports/) | 逐增量交付汇报（increment / batch / phase report） |
| [archive/audit-evidence/](archive/audit-evidence/) | 2026-10-17 审计的 T1-T5 证据快照 |
| [archive/audit-evidence-deepagents/](archive/audit-evidence-deepagents/) | 2026-10-19 审计的复现脚本 |
| [archive/design/](archive/design/) | 已落地的历史设计方案 |
| [archive/superpowers/](archive/superpowers/) | 2026-07 ~ 2026-10 的实施计划与设计稿 |
| [archive/knowledge-reports/](archive/knowledge-reports/) | P0 实施报告与阶段验收记录 |

## 约定

- 新增设计文档放 `docs/design/`，落地完成后移入 `docs/archive/design/`；
- 交付汇报不留在 `docs/audit/` 根下，直接写 `docs/archive/audit-reports/`；
- 代码注释里引用文档时用相对仓库根的路径，移动文件后同步更新引用（本次整理已批量修正 24 个文件的引用）。
