# Increment 56 · E3 RED 观测指标报告

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-18 |
| 结论 | 完成：进程内 RED 指标注册表 + 纯 ASGI HTTP 中间件 + 管理员专属 `/agent/metrics` JSON 端点；SSE/工具/HITL 的 `record_*` API 已就绪但未接入业务；目标测试 26 项通过 |
| 范围 | 后端（services / middleware / app / tests / docs）；未改前端、API 业务、db、streaming、checkpoints、mcp、conftest |
| 依据 | `docs/design/2026-10-18-e3-red-metrics.md`；任务 E3；现有 `/agent/live`、`/agent/ready`、`middleware/auth.py` |

## 1. 结论

- 新增 `easy_agent/services/metrics.py`：纯 stdlib、线程安全的 RED 注册表，
  覆盖 HTTP 计数/错误率/延迟分位/QPS、SSE 活跃与时长、工具失败率、HITL 等待。
- 新增 `easy_agent/middleware/metrics.py`：**纯 ASGI** 中间件记录
  `method/path/status/duration`，不缓冲响应体，不干扰 SSE；排除探针、指标自身、
  文档、静态资源与 `OPTIONS` 预检。
- `easy_agent/app.py` 仅做三处必要改动：导入中间件与鉴权依赖、注册中间件、
  新增 `GET /agent/metrics`（`admin` 返回 200，非 `admin` 返回 403，无凭证 401）。
- SSE / 工具 / HITL 按任务要求只提供 `record_*` API，**未修改** streaming、
  工具执行与 HITL 业务链路，避免把观测改动混入高风险路径。
- 目标测试 `25 passed`；`uvx ruff check .` 通过；import graph `0 SCC`。
- 未运行全量测试、未 commit、未执行 Batch 0/1、未回退 wave1 未提交改动。

## 2. 交付物

| 文件 | 变更 |
| --- | --- |
| `easy_agent/services/metrics.py` | 新增：`RedMetrics`（单锁、有界样本、路由基数上限）、模块级单例、`record_http_request` / `record_sse_start` / `record_sse_end` / `record_tool_call` / `record_hitl_wait_start` / `record_hitl_wait_end` / `snapshot_metrics` / `reset_metrics` |
| `easy_agent/middleware/metrics.py` | 新增：`MetricsMiddleware`（纯 ASGI）、`should_record_request` 与排除规则常量 |
| `easy_agent/app.py` | 注册 `MetricsMiddleware`；新增 `GET /agent/metrics`（复用 `get_current_username`，`admin` 专属）；新增 `Depends/HTTPException` 与依赖导入 |
| `tests/services/test_metrics.py` | 新增 13 项：快照结构/JSON、错误率、分位、路径归一化、路由上限、QPS、SSE gauge/失败率、工具失败率、HITL 结果与下溢保护、HTTP 并发、混合并发 |
| `tests/api/test_metrics.py` | 新增 7 项：403/401/200、业务请求记录、404 归入 client_errors、探针/文档/静态排除、`should_record_request` 规则 |
| `docs/design/2026-10-18-e3-red-metrics.md` | 设计文档：指标定义、算法、端点契约、排除规则、风险 |
| 本文件 | 审计报告 |

## 3. 指标定义与端点

### 3.1 指标集合

| 类别 | 指标 | 口径 |
| --- | --- | --- |
| HTTP | `total` / `errors` / `client_errors` | 总数 / 5xx / 4xx |
| HTTP | `error_rate` | `errors / total`（5xx 口径） |
| HTTP | `qps.current` / `qps.average` | 最近窗口（≤60s，分母至少 1s）/ `total / uptime` |
| HTTP | `latency_ms` | count/samples/mean/p50/p95/p99/max（毫秒，线性插值） |
| HTTP | `routes[]` | 按 `(method, 归一化 path)` 聚合；UUID/数字段替换为 `{id}` |
| SSE | `active` / `total` / `failed` / `failure_rate` | 活跃 gauge / 启动数 / 失败数 / 失败率 |
| SSE | `duration_ms` | 流生命周期时长分位/均值 |
| 工具 | `total` / `failures` / `failure_rate` / `duration_ms` / `by_tool[]` | 调用数 / 失败数 / 失败率 / 可选耗时 |
| HITL | `waiting` / `total` / `outcomes` / `wait_ms` | 当前等待 gauge / 结束次数 / 审批结果 / 等待时长 |

有界性：每序列最多 2048 个延迟样本；HTTP 路由序列上限 256，
溢出落入 `(other)`；路径截断 200 字符；SSE/工具/outcome 标签截断 80/80/32。

### 3.2 端点与鉴权

| 场景 | 结果 |
| --- | --- |
| `GET /agent/metrics`，无有效 JWT | 401（`get_current_username` 现有语义） |
| 已登录，`username != "admin"` | 403 `{"detail": "仅管理员可以查看运行指标"}` |
| `username == "admin"` | 200，JSON 快照 |

- 复用现有 JWT 鉴权，不新增 token/权限模型；
- 仅认 `admin`，公共空间管理员（白名单）未授予读取权限，避免权限隐式扩散；
- 指标端点自身被排除，抓取频率不会污染 QPS。

### 3.3 中间件行为

- 纯 ASGI 包装 `send`，在响应结束（含 SSE 流结束）后记录一次；
- 下游异常按 500 记录后原样抛出；指标写入失败只告警，不影响响应；
- 注册在 `RateLimitMiddleware` 外层、`LegacyHostRoutes` 内层：
  429 会被统计，`/api/*` 兼容路径按改写后的 `/agent/*` 记录；
- 排除：`/`, `/health`, `/agent/health`, `/api/live`, `/agent/live`, `/ready`,
  `/api/ready`, `/agent/ready`, `/agent/metrics`, `/api/metrics`, `/docs`,
  `/redoc`, `/openapi.json`, 静态前缀/后缀与 `OPTIONS`。

## 4. 执行记录

### 4.1 目标测试

```text
$ .venv/bin/python -m pytest \
    tests/services/test_metrics.py tests/api/test_metrics.py tests/integration/test_health.py -q
.........................                                                [100%]
26 passed, 1 warning in 9.08s
```

警告为既有的 `StarletteDeprecationWarning: Using httpx with starlette.testclient`
（依赖库提示，与本改动无关）。26 项 = 指标服务 13 + 指标 API 7 + health 6。

### 4.2 静态检查

```text
$ uvx ruff check .
All checks passed!
```

```text
$ python3 scripts/quality/import_graph.py --check-scc
import graph: 217 modules, 488 internal edges, 0 SCC(s) / 0 modules
[check-scc] OK — 实际 SCC 与白名单完全一致
```

`middleware/metrics.py → services/metrics.py` 为单向依赖（`services.metrics`
只依赖 stdlib），未形成新的 import 环。

### 4.3 未执行的检查

- 未运行全量 `pytest tests/`（按要求只跑目标用例，保留 wave1 的 1274 passed 基线）；
- 未执行 Batch 0/1；
- 未 commit、未 push、未改动前端/Web Terminal。

## 5. 未接入的埋点

| API | 当前状态 | 建议接入点 |
| --- | --- | --- |
| `record_sse_start` / `record_sse_end` | 未接入 | `services/streaming/orchestrator.py` 生成器开始与 `finally`；`failed` 按异常/客户端断开标记 |
| `record_tool_call` | 未接入 | 工具执行统一出口（`ToolNode` 包装或 `tools/registry.py`），传入工具名、是否成功、可选耗时 |
| `record_hitl_wait_start` | 未接入 | 发出 `approval_required` / 进入 interrupt 等待处 |
| `record_hitl_wait_end` | 未接入 | `/agent/chat/resume` 或 interrupt 恢复路径，按审批结果记录 outcome |
| `record_http_request` | **已接入** | `MetricsMiddleware` 自动记录全部业务 HTTP |

保持未接入是有意为之：任务要求“不强行改 streaming”，且 SSE 生成器的取消、
客户端断开、异常路径需要单独设计 `failed` 判定，避免观测代码改变流式行为。

## 6. 风险

| 风险 | 影响 | 当前处理 / 后续 |
| --- | --- | --- |
| 进程内指标 | 多 worker/多副本时每个实例独立，端点只反映当前实例 | 外部采集器逐 pod 抓取后聚合；后续可加共享存储/推模式 |
| 进程重启归零 | 无历史趋势 | RED 属短期运行态；趋势交给外部 TSDB |
| 分位近似 | 只保留最近 2048 样本，长尾历史被淘汰 | count/mean/max 精确；需要精确历史分位时导出 histogram |
| 路由基数 | 恶意构造大量不同路径可占满序列上限 | 归一化 + 256 上限 + `(other)` 兜底，保证内存有界 |
| HTTP 延迟含 SSE | SSE 请求的 HTTP duration 等于流生命周期，抬高整体 p99 | 同时提供 `sse.duration_ms` 单独观察 |
| 拉取鉴权 | 外部 Prometheus 若不能带 JWT 无法直接抓取 | 本轮不放开匿名；由带鉴权的 sidecar/网关转发，或后续单独设计只读 token |
| 权限范围 | 公共空间管理员不可读 | 仅 `admin`；如需扩展应单独评审，避免与人员管理权限耦合 |

## 7. 边界核对

- 改动文件严格限定在允许清单内：`services/metrics.py`（新增）、
  `middleware/metrics.py`（新增）、`app.py`（三处必要段）、两个测试文件、
  两篇文档。
- 未修改 `easy_agent/api/*` 业务、`db/`、`services/streaming/*`、
  `checkpoints.py`、`services/mcp*`、前端、`tests/conftest.py`。
- wave1 的未提交改动未回退；本增量只新增文件并在 `app.py` 追加注册/端点。
- 工作区处于未提交状态（按要求不 commit）。
