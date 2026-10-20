# Increment 60 — FW-14 MCP server 错误语义 交付报告

| 项 | 内容 |
| --- | --- |
| 任务 | FW-14：MCP server 错误语义（仅 `mcp-server/` 子项目） |
| 日期 | 2026-10-18 |
| 范围 | `mcp-server/easy_mcp_server/**`、`mcp-server/tests/**`、两份 docs |
| 状态 | 完成（未 commit；未执行 Batch 0/1；未改主仓/前端/deploy） |
| 设计 | [2026-10-18-fw14-mcp-error.md](../design/2026-10-18-fw14-mcp-error.md) |

## 1. 结论

- `mcp-server` 当前**没有**把异常包成 JSON 文本“成功”的工具（`json.dumps` +
  `{"error": ...}` 扫描 0 处）；但未捕获异常依赖 FastMCP 兜底，错误文本包含原始
  异常，可能泄露 DSN/host/密码片段，且“空结果 vs 失败”边界未文档化。
- 本次把 3 个 market 工具的失败统一改为 `ToolError`，对外只暴露稳定错误码 +
  中文说明；原始异常脱敏后仅进服务端日志。
- `structuredContent` 在 `mcp==1.29.1` 下本就自动生成；本次用
  `structured_output=True` 显式锁定，并用测试固定其形状，文本输出保持兼容。
- 审计点名的 `mcp/jmfx_mcp_server.py` 的 `_safe()` JSON 成功包装不在允许改动
  范围，未修改（见 §5）。

## 2. 修改工具清单

| 工具 | 修改 | 失败时错误码 |
| --- | --- | --- |
| `ask_market`（行情意图） | `try/except` → `ToolError`；`structured_output=True` | `market_quotes_unavailable` |
| `ask_market`（持仓意图） | 同上 | `market_positions_unavailable` |
| `get_latest_quotes` | 同上 | `market_quotes_unavailable` |
| `get_my_positions` | 同上 | `market_positions_unavailable` |

空结果（如 `ask_market` 查无行情）保持 `isError=false`，返回
`{"quotes": [], "answer": "暂无匹配的行情数据。"}`；不把空结果伪装成错误。

## 3. 错误契约

失败响应（MCP 标准）：

```json
{
  "content": [{"type": "text", "text": "Error executing tool get_latest_quotes: market_quotes_unavailable: 行情数据暂时不可用，请稍后重试"}],
  "isError": true
}
```

- `isError=true`、`structuredContent=null`；
- 文本不含异常类型/`repr`/堆栈/SQL/DSN/host/密码/API Key；
- 原始异常经 `redact()` 脱敏（`password|passwd|pwd|secret|token|api_key=`、
  `Bearer <token>`、`MYSQL_PASSWORD` 字面值）后写 `logger.error`，单行、≤500 字符；
- 错误码 `snake_case`，客户端可稳定分支；中文文案可调整。

## 4. 修改文件

| 文件 | 类型 | 说明 |
| --- | --- | --- |
| `mcp-server/easy_mcp_server/errors.py` | 新增 | `redact()` + `tool_failure()` 错误契约 |
| `mcp-server/easy_mcp_server/businesses/market/server.py` | 修改 | 3 个工具接入 `ToolError`；显式 `structured_output=True` |
| `mcp-server/tests/test_market.py` | 修改 | 新增 4 个失败语义用例 + 3 个 structuredContent 用例 + 1 个空结果用例 |
| `mcp-server/tests/test_errors.py` | 新增 | 5 个脱敏/日志/文案单元用例 |
| `mcp-server/README.md` | 修改 | 补充错误语义、错误码与空结果约定 |
| `docs/design/2026-10-18-fw14-mcp-error.md` | 新增 | 设计与契约 |
| `docs/audit/2026-10-18-increment60-fw14-report.md` | 新增 | 本报告 |

代码/文档 diff（不含新增文件）：`3 files changed, 190 insertions(+), 28 deletions(-)`
（README + market/server.py + test_market.py）。

## 5. structuredContent

`mcp==1.29.1` 已根据返回注解自动生成 `outputSchema` 与 `structuredContent`；
本次不改变返回形状，只显式声明并测试锁定：

| 工具 | `structuredContent` |
| --- | --- |
| `ask_market` | `{category, quotes|positions, answer}`（原 dict） |
| `get_latest_quotes` | `{"result": [...]}` |
| `get_my_positions` | `{"result": [...]}` |

文本 `content` 与修改前一致：`ask_market` 为 dict JSON；list 工具为每行一个
`TextContent`（FastMCP 1.x 兼容行为）。未引入严格 Pydantic 输出模型，避免真实
`datetime`/`Decimal` 行在输出校验阶段被拒。

## 6. 测试命令与结果

```bash
cd mcp-server
uv run pytest -q
```

- 基线（改动前）：`30 passed, 8 warnings in 3.14s`
- 改动后：`43 passed, 16 warnings in 4.81s`
  （30 + 13：test_market 新增 8，test_errors 新增 5）
- `uv run python -m compileall -q easy_mcp_server tests` → `COMPILE_OK`
- 依赖环境：`uv run` 自动完成 editable 构建/安装，无需额外 `uv sync`；
  `mcp==1.29.1` 来自 `mcp-server/uv.lock`。

新增用例覆盖：

1. `get_latest_quotes` / `get_my_positions` 查询异常 → `isError=true`、错误码正确、
   无 `hunter2`、无 `Traceback`、无 `RuntimeError`、`structuredContent=null`；
2. `ask_market` 行情/持仓两分支异常 → 对应错误码；
3. 三个工具成功时 `structuredContent` 形状与文本兼容；
4. 空结果仍是成功；
5. `redact` 覆盖 password/token/Bearer/`MYSQL_PASSWORD`、换行与截断；
6. `tool_failure` 对外文案只含 `code: message`，日志脱敏。

## 7. 未做项

| 项 | 原因 |
| --- | --- |
| 主仓 `mcp/jmfx_mcp_server.py` 的 `_safe()` / `_envelope()` JSON 成功包装 | 不在本任务允许改动范围（仅 `mcp-server/`）；建议单独立项 |
| OAuth / 传输超时（FW-10） | 与错误语义无关，未触碰 |
| 严格 Pydantic 输出模型 / 更细 `outputSchema` | 真实行含 `datetime`/`Decimal`，严格字段会拒绝合法数据；保留 `Any` 级 schema |
| 主仓全量 1335 用例回归 | 本次只改独立 uv 子项目；按任务要求执行 mcp-server 测试，主仓全量由 Lead 统一跑 |
| commit / Batch 0/1 | 任务明确禁止 |

## 8. 风险与回归

- **低风险**：只改 `mcp-server/` 三个工具与新增错误模块；返回形状、字段名、
  文本序列化未变。
- FastMCP 仍会在错误文本前加 `Error executing tool <name>: `；错误码紧随其后，
  客户端解析时需按前缀剥离或使用 `isError` 判断。
- `structured_output=True` 依赖 mcp 1.27+（本项目锁 1.29.1）；若未来降级到无该
  参数的版本会启动即报错，属预期的显式失败。
- **测试抖动（非本次改动引入）**：一次全量运行出现
  `test_app.py::test_unknown_business_returns_401` 的 `httpx.RemoteProtocolError`；
  单独重跑与随后 3 次全量均通过（43 passed），判断为 uvicorn 测试夹具的偶发
  连接抖动，未改该用例。
