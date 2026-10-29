# E3 · RED 运行观测指标（后端）

| 项 | 内容 |
| --- | --- |
| 状态 | 已落地（2026-10-18，Increment 56） |
| 范围 | 后端：进程内指标注册表、HTTP 中间件、`/agent/metrics` JSON 端点、测试与文档 |
| 关联 | E3（RED 观测指标）；现有 `/agent/live`、`/agent/ready`；`middleware/auth.py` 鉴权 |
| 非目标 | 不改前端；不改 API 业务、db、streaming、checkpoints、mcp；不引入 Prometheus 客户端；不 commit；不回退 wave1 改动 |

## 1. 问题与目标

现状只有存活/就绪探针与按会话维度的日志：

- 没有统一的请求量、错误率、延迟指标；
- SSE 流、工具调用、HITL 等待没有可聚合的计数；
- 官方依赖中没有强制 Prometheus，引入 `prometheus_client` 会新增运行期依赖，
  且当前部署形态（单机/少量副本）不需要完整 TSDB。

目标：用 **stdlib 进程内指标 + JSON 文本暴露** 补齐最小 RED 闭环：
请求 QPS / 错误率 / 延迟，SSE 活跃数与时长，工具失败率，HITL 等待数与时长。
不改变任何业务响应；埋点 API 先提供，SSE/工具/HITL 的实际接入留给后续改动。

## 2. 指标集合

### 2.1 指标定义

| 类别 | 字段 | 类型 | 语义 |
| --- | --- | --- | --- |
| HTTP | `total` | counter | 纳入统计的请求总数 |
| HTTP | `errors` | counter | 状态码 **5xx** 数 |
| HTTP | `client_errors` | counter | 状态码 **4xx** 数 |
| HTTP | `error_rate` | gauge | `errors / total`（0~1） |
| HTTP | `qps.current` | gauge | 最近窗口内请求数 / 窗口秒数 |
| HTTP | `qps.average` | gauge | `total / uptime` |
| HTTP | `latency_ms` | summary | count/samples/mean/p50/p95/p99/max（毫秒） |
| HTTP | `status_codes` | map | `{"200": 10, "500": 1}` |
| HTTP | `routes[]` | list | 按 `(method, 归一化 path)` 聚合的 count/errors/延迟 |
| SSE | `active` | gauge | 当前活跃流数 |
| SSE | `total` / `failed` | counter | 启动流数 / 失败流数 |
| SSE | `failure_rate` | gauge | `failed / total` |
| SSE | `duration_ms` | summary | 流生命周期时长分位/均值 |
| 工具 | `total` / `failures` | counter | 工具调用数 / 失败数 |
| 工具 | `failure_rate` | gauge | `failures / total` |
| 工具 | `duration_ms` | summary | 可选：调用耗时分位/均值 |
| HITL | `waiting` | gauge | 当前等待人工审批的请求数 |
| HITL | `total` | counter | 已结束的等待次数 |
| HITL | `outcomes` | map | `{"approved": n, "rejected": n}` |
| HITL | `wait_ms` | summary | 等待时长分位/均值 |

所有延迟统计统一输出**毫秒**，内部以秒累计。

### 2.2 分位算法与精度

- 精确值：`count`、`total_seconds`、`max_seconds` 永不丢样本；
- 分位值：每个序列保留最近 `MAX_SAMPLES = 2048` 个样本，排序后线性插值
  （与 Prometheus `histogram_quantile` 的近似目标一致）；
- 快照中 `count` 是累计观测数，`samples` 是参与分位计算的样本数；当
  `samples < count` 时说明序列已滚动。

### 2.3 基数与内存保护

| 机制 | 规则 |
| --- | --- |
| 路径归一化 | UUID / 纯数字段替换为 `{id}`，去掉 query string，截断到 200 字符 |
| 路由上限 | 最多 `MAX_ROUTE_SERIES = 256` 条 `(method, route)` 序列，溢出归入 `"(other)"`（额外 1 条） |
| 标签长度 | SSE 路由/工具名/HITL outcome 截断到 80/80/32 字符，空值回退 `unknown` |
| 延迟样本 | 每序列最多 2048 个，`deque(maxlen)` 自动淘汰 |
| QPS | 只保留最近 60 秒的按秒计数桶，桶数上限约 60 |

### 2.4 HTTP 错误口径

- `errors` 只统计 **5xx**（服务端错误，RED 的错误率口径）；
- 4xx 单列 `client_errors`，避免把“用户参数错误/未登录”混入服务健康度；
- 若调用方需要“非 2xx 比例”，用 `(errors + client_errors) / total` 自行计算。

## 3. 实现结构

```text
easy_agent/services/metrics.py       RedMetrics 容器 + 模块级单例 + record_* API
easy_agent/middleware/metrics.py     MetricsMiddleware（纯 ASGI）+ 排除规则
easy_agent/app.py                    注册中间件、GET /agent/metrics
tests/services/test_metrics.py       计数器、分位、基数、并发安全
tests/api/test_metrics.py            鉴权、中间件路径、排除规则、快照字段
```

### 3.1 线程安全

- `RedMetrics` 内部只有一把 `threading.RLock`，所有写路径与快照都在锁内；
- 中间件运行在事件循环线程，未来工具执行/线程池调用 `record_*` 也安全；
- 不依赖 asyncio，不创建后台任务，不引入定时器。

### 3.2 HTTP 中间件

采用**纯 ASGI** 中间件而不是 `BaseHTTPMiddleware`：

- 包装 `send` 捕获 `http.response.start` 的最终状态码；
- 在 `finally` 中记录 `time.perf_counter()` 差值，异常统一按 500 记录后继续抛出；
- 不缓冲/不改写响应体，避免干扰 SSE 流式输出；
- HTTP 延迟包含流式响应时长（SSE 请求的 HTTP 耗时会等于流生命周期）；
  SSE 自身的活跃数/时长由 `record_sse_*` 单独提供。

注册顺序：`MetricsMiddleware` 在 `RateLimitMiddleware` 外层、`LegacyHostRoutes`
内层。因此限流返回的 429 会被统计，`/api/*` 兼容路径按改写后的 `/agent/*` 记录。

排除规则（`should_record_request`）：

| 类型 | 示例 |
| --- | --- |
| 探针 | `/`, `/health`, `/agent/health`, `/api/live`, `/agent/live`, `/ready`, `/api/ready`, `/agent/ready` |
| 指标自身 | `/agent/metrics`, `/api/metrics` |
| 文档 | `/docs`, `/redoc`, `/openapi.json` |
| 静态资源 | `/static/*`, `/assets/*`, `/js/*`, `/css/*`, `/img/*`, `/fonts/*`，以及 `.js/.css/.map/.svg/.ico/.png/.woff.../.html` 后缀 |
| 预检 | `OPTIONS` |

## 4. 端点契约

### 4.1 鉴权

`GET /agent/metrics` 复用 `middleware/auth.py` 的 `get_current_username`：

| 场景 | 状态码 |
| --- | --- |
| 无有效 JWT / 登录过期 | 401（现有统一错误体） |
| 已登录但不是 `admin` | 403 `{"detail": "仅管理员可以查看运行指标"}` |
| `admin` | 200 JSON |

当前只认 `username == "admin"`；公共空间管理员（白名单）暂不授予指标读取权限，
避免把“人员管理”权限与“运行观测”权限隐式绑定。若后续需要，可复用
`personnel/api.py:require_personnel_editor` 的判定逻辑单独评估。

### 4.2 响应示例

```json
{
  "generated_at": "2026-10-18T01:35:00.000000+00:00",
  "uptime_seconds": 12.5,
  "process": {"pid": 1234, "started_at": "2026-10-18T01:34:47.500000+00:00"},
  "http": {
    "total": 2,
    "errors": 0,
    "client_errors": 1,
    "error_rate": 0.0,
    "qps": {"window_seconds": 12.5, "current": 0.16, "average": 0.16},
    "latency_ms": {
      "count": 2, "samples": 2, "mean": 12.5,
      "p50": 15.0, "p95": 19.75, "p99": 19.95, "max": 20.0
    },
    "status_codes": {"200": 1, "404": 1},
    "routes": [
      {
        "method": "GET",
        "route": "/agent/config",
        "count": 1,
        "errors": 0,
        "client_errors": 0,
        "error_rate": 0.0,
        "latency_ms": {
          "count": 1, "samples": 1, "mean": 5.0,
          "p50": 5.0, "p95": 5.0, "p99": 5.0, "max": 5.0
        }
      }
    ]
  },
  "sse": {
    "active": 1,
    "total": 3,
    "failed": 0,
    "failure_rate": 0.0,
    "duration_ms": {
      "count": 2, "samples": 2, "mean": 1800.0,
      "p50": 1800.0, "p95": 3200.0, "p99": 3360.0, "max": 3400.0
    },
    "routes": [
      {
        "route": "/agent/chat/stream",
        "active": 1,
        "total": 3,
        "failed": 0,
        "failure_rate": 0.0,
        "duration_ms": {
          "count": 2, "samples": 2, "mean": 1800.0,
          "p50": 1800.0, "p95": 3200.0, "p99": 3360.0, "max": 3400.0
        }
      }
    ]
  },
  "tools": {
    "total": 4,
    "failures": 1,
    "failure_rate": 0.25,
    "duration_ms": {
      "count": 3, "samples": 3, "mean": 120.0,
      "p50": 80.0, "p95": 232.0, "p99": 246.4, "max": 250.0
    },
    "by_tool": [
      {
        "tool": "execute",
        "total": 2,
        "failures": 1,
        "failure_rate": 0.5,
        "duration_ms": {
          "count": 1, "samples": 1, "mean": 250.0,
          "p50": 250.0, "p95": 250.0, "p99": 250.0, "max": 250.0
        }
      }
    ]
  },
  "hitl": {
    "waiting": 1,
    "total": 2,
    "outcomes": {"approved": 1, "rejected": 1},
    "wait_ms": {
      "count": 2, "samples": 2, "mean": 3000.0,
      "p50": 3000.0, "p95": 5500.0, "p99": 5900.0, "max": 6000.0
    }
  }
}
```

## 5. 埋点 API 与接入状态

| API | 接入状态 | 建议接入位置（后续） |
| --- | --- | --- |
| `record_http_request(method, path, status, duration)` | **已接入**（`MetricsMiddleware`） | — |
| `record_sse_start(route)` | 未接入 | `services/streaming/orchestrator.py` 生成器开始处 |
| `record_sse_end(route, duration, failed=)` | 未接入 | 生成器 `finally`；按是否正常完成/客户端断开标记 `failed` |
| `record_tool_call(name, success, duration)` | 未接入 | 工具执行包装层（`ToolNode` 调用或 `tools/registry.py` 统一出口） |
| `record_hitl_wait_start()` | 未接入 | 发出 `approval_required`/进入 interrupt 等待处 |
| `record_hitl_wait_end(duration, outcome)` | 未接入 | `/agent/chat/resume` 或 interrupt 恢复路径 |

本轮**不修改 streaming / 工具 / HITL 业务代码**，避免把观测改动混入高风险链路；
上述 API 已按“先 start 后 end、end 幂等不落负”的语义实现并测试。

## 6. 风险与限制

| 风险 | 说明 | 缓解/后续 |
| --- | --- | --- |
| 进程内 vs 多副本 | 指标只在当前进程聚合；多 worker/多副本时每个实例各存一份，JSON 端点只反映被请求的那个实例 | 由 Prometheus/采集器逐 pod 抓取后聚合；或增加共享存储/推模式（本轮不做） |
| 重启丢失 | 无持久化，进程重启后归零 | RED 指标本就是短期运行态；趋势数据由外部 TSDB 保存 |
| 分位近似 | 分位基于最近 2048 个样本，长尾历史会被淘汰；count/mean/max 仍精确 | 需要精确历史分位时导出到 Prometheus histogram |
| 路由基数 | 归一化 + 256 上限 + `(other)` 兜底；恶意构造大量不同静态路径仍可能占满上限 | 上限保证内存不爆；安全网关负责限制异常路径扫描 |
| HTTP 延迟含 SSE | SSE 请求的 HTTP duration 等于流生命周期，会抬高整体 p99 | 用 `sse.duration_ms` 单独观察流时长；两者都保留 |
| 鉴权范围 | 仅 `admin`，公共空间管理员不可读 | 需要时单独评审权限模型 |
| 指标端点自身 | 不计入 HTTP，避免抓取频率污染 QPS | 设计如此；如需审计抓取，由访问日志承担 |
| 拉取方式 | 外部 Prometheus 若不能带 JWT，无法直接抓取 | 通过带鉴权的 sidecar/网关转发，或后续增加只读 token（本轮不做） |

## 7. 验证

```bash
.venv/bin/python -m pytest \
  tests/services/test_metrics.py tests/api/test_metrics.py tests/integration/test_health.py -q
uvx ruff check .
python3 scripts/quality/import_graph.py --check-scc
```

- 目标测试：`26 passed`（含 health 6 项）；
- ruff：`All checks passed!`；
- import graph：`217 modules, 488 internal edges, 0 SCC(s)`，`[check-scc] OK`。

未运行全量测试（按任务要求只跑目标用例），未 commit。
