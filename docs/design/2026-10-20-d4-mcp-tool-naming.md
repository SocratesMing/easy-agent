# D4 方案：MCP 工具按 server 命名 + 兼容层

> 状态：待评审（未实施）。
> 背景与证据见 [DeepAgents 规范一致性审计 §5 D4](2026-10-20-deepagents-conformance-audit.md)。
> 目标：多 server 场景下工具名可区分、冲突不再静默丢弃，同时不破坏既有会话、技能与审批。

## 1. 现状与问题

| 现状 | 位置 | 后果 |
| --- | --- | --- |
| 包装 MCP 工具时保持原名 | `services/mcp_evidence.py:405-417`（`wrap_mcp_tool` 注释「保持 name 不变」） | 两个 server 暴露同名工具时，模型/审计无法区分来源 |
| 与内建工具重名直接丢弃 | `services/mcp.py:461-467` | 静默不可用，只在日志里 warning |
| 多 server 重名保留第一个 | `services/mcp.py:468-470` | 后加入的 server 的同类能力静默缺失 |
| HITL 名字启发式 | `agent/hitl.py:124-130`（`looks_like_mutating_tool`）、`_NAME_HEURISTIC_EXEMPT` | 依据工具名判断是否审批；改名会影响判定 |
| 证据信封按 server 记账 | `services/mcp_evidence.py:417` | UI 可显示来源，但工具名本身不含来源 |
| 技能 `include_tools` / 子代理 `use_tools` 按名字匹配 | `agent/skills.py:declared_include_tools`、`agent/subagents.py:resolve_subagent_tools` | 改名后两处白名单都会失效 |

## 2. 命名方案

```
{server}__{tool}          # 例：github__create_issue、jira__create_issue
```

- **server**：取 `mcp.json` 里的 server key，按 `[^a-zA-Z0-9_-]` 替换为 `_`；长度截断到 24 字符，超出部分用 6 位 sha256 后缀（`server__abcd12` 形式）。
- **tool**：服务端返回的原始名，只做非法字符替换（保留 `_ -`），总长截断到 64（OpenAI 工具名上限），截断时同样加哈希后缀，保证稳定且不撞车。
- **来源元数据**：证据信封继续记 `server`，并新增 `original_name`；UI 展示 `server / tool`，不展示拼接名。
- 重名规则（前缀后）：
  1. 与内建工具同名 → 仍然丢弃并**报错级**告警（理论上不该发生）；
  2. 跨 server 同名 → 不再冲突（前缀已区分）；
  3. 同一 server 内重复 → 保留第一个 + warning（服务端自身问题）。

## 3. 兼容层（三处必须同时处理）

### 3.1 技能 `metadata.include_tools` 与子代理 `use_tools`

两处都按字符串匹配工具名。改名后用户技能里写的旧名会失效，处理方式：

- 解析时做**双向匹配**：先精确匹配；未命中则按「后缀 == 原名」唯一匹配（`save_expert` 可匹配 `github__save_expert`）；
- 多个 server 都有该后缀时视为歧义，记 error 并跳过（提示用户写全名）。

### 3.2 HITL 审批

- **判定**：`looks_like_mutating_tool()` 改用 `original_name`（前缀后仍会命中 `create_/update_/delete_` 等词根，但去掉前缀干扰更稳）；
- **豁免表** `_NAME_HEURISTIC_EXEMPT` 仍存内建名，前缀化后不会误命中；
- **annotations 路径不受影响**（`mcp_annotation_decision` 读 metadata，与名字无关）；
- **进行中的审批**：`Command(resume={"decisions": [...]})` 是位置式，不受影响；只有 `edit` 决策需要带 `name`，升级时用 3.1 的后缀匹配把旧名映射到新名，映射失败则拒绝该次恢复并提示重新发起。

### 3.3 历史会话与提示词

- 历史消息里的旧 tool_call 只作为上下文，不会执行，无需迁移；
- `SKILL.md` / 系统提示词里若硬编码了 MCP 工具名，需要同步改（当前 `skills/` 下未发现硬编码 MCP 工具名，`grep mcp_` 只命中 mcp-builder 的文档链接）；
- 自定义 MCP 服务的工具描述由服务端提供，不受影响。

## 4. 实施顺序（可回滚）

| 阶段 | 内容 | 默认值 |
| --- | --- | --- |
| S1 | 引入命名函数 + `EASY_MCP_TOOL_PREFIX` 开关（默认 **off**），只做重命名与日志，行为不变 | off |
| S2 | 技能/子代理后缀匹配 + HITL 改用 `original_name`（与开关无关，独立可用） | — |
| S3 | 灰度打开开关（dev 环境），观察工具调用成功率、HITL 触发率、审计证据里的 server/original 字段 | dev on |
| S4 | 默认 on，保留一个版本的回退开关；补齐迁移说明 | on |

回退：开关置 off 即回到「原名 + 冲突丢弃」的旧行为，但 S2 的兼容匹配与 HITL 改进可保留。

## 5. 影响面清单

| 模块 | 改动 |
| --- | --- |
| `services/mcp.py` | 载入后统一改名；冲突规则改为「前缀后判重」 |
| `services/mcp_evidence.py` | 信封与 wrapper 增加 `original_name`；`wrap_mcp_tool` 接收最终名 |
| `agent/hitl.py` | 启发式函数改看 `original_name`；`edit` 决策的名字映射 |
| `agent/skills.py` | `declared_include_tools` 支持后缀匹配（歧义报错） |
| `agent/subagents.py` | `resolve_subagent_tools` 支持后缀匹配 |
| `services/mcp_policy.py` | 若按名字过滤，同步支持新名（需确认，当前按 server 过滤） |
| 前端「连接器/工具」列表 | 展示 `server / tool`（可选，非阻塞） |

## 6. 测试与验收

- 单测：命名函数（特殊字符、超长截断、哈希稳定性）、前缀后无冲突、内建同名丢弃、后缀匹配歧义路径；
- 集成：两个 server 各暴露 `create_issue` → 模型工具列表出现两个可区分名字；HITL 对 `github__create_issue` 的判定与改名前一致；
- 兼容：在技能里写 `include_tools: create_issue`（无前缀）仍能门控到唯一工具；写歧义名时返回明确错误；
- 回滚：开关 off 后工具名与当前完全一致（用快照断言）。

## 7. 工作量

约 1.5 天：S1 0.5 天（命名 + 开关 + 单测），S2 0.5 天（两处匹配 + HITL 原名字段），集成测试与灰度验证 0.5 天。前端展示可延后。
