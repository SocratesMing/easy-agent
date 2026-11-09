# B3 限流与生产 JWT 契约（第一批）

| 项 | 内容 |
| --- | --- |
| 状态 | 第一批已实现；多副本共享计数仅留设计与告警，**未实现共享存储** |
| 日期 | 2026-10-18 |
| 范围 | `easy_agent/middleware/rate_limit.py`、`easy_agent/utils/auth.py`、`easy_agent/app.py`（lifespan）、`.env.prod.example` |
| 依据 | `docs/design/2026-10-18-architecture-improvement-plan.md` 阶段 B3 |
| 报告 | `docs/audit/2026-10-18-increment41-b3-report.md` |

本批只做不依赖多副本的项：真实客户端 IP、429 正确返回、可插拔计数接口 +
单机内存实现、多副本告警、prod JWT 密钥 fail-fast。共享计数（Redis/DB）与
Ingress 全局限流留待 B3 后续批次。

## 1. 限流中间件

### 1.1 问题

改造前 `RateLimitMiddleware.dispatch()` 存在三个问题：

1. 使用 `request.client.host` 作为唯一键。在 Ingress / 反向代理后这是代理地址，
   所有用户共享同一个桶；极端情况下正常流量会被误限流，而真实攻击者换 IP
   也不会被单独计数。
2. 超限时 `raise HTTPException(status_code=429)`。`BaseHTTPMiddleware` 的
   `call_next` 在下游任务组中执行，抛出的 `HTTPException` 不经过 FastAPI
   `ExceptionMiddleware`（位于更内层），最终表现为 **500**，前端无法按 429
   做退避。
3. 计数只存在进程内字典，多 worker / 多副本时各算各的，且没有任何提示。

### 1.2 对外契约

超限响应固定为：

```http
HTTP/1.1 429 Too Many Requests
Content-Type: application/json

{"detail": "请求过于频繁，请稍后再试", "code": "RATE_LIMITED"}
```

- 通过 `JSONResponse(status_code=429, content=...)` 返回，不再抛异常；
- `code` 为新增稳定字段，方便前端/网关区分限流与其它 4xx；
- 既有路径前缀与阈值完全不变（登录 20 次/60s，聊天流/续传 30 次/60s）。

### 1.3 真实客户端 IP

限流键为 `f"{prefix}:{get_client_ip(request)}"`，`get_client_ip` 位于
`easy_agent/utils/client_ip.py`：

- 默认只信 `request.client.host`；
- 仅当 `EASY_TRUST_PROXY=1`（`1/true/yes/on`）时读取 `X-Forwarded-For`
  的第一个地址或 `X-Real-IP`。

部署注意：

- 若服务在 Ingress 后且 **Ingress 会覆盖** `X-Forwarded-For`，设置
  `EASY_TRUST_PROXY=1` 才能按真实用户 IP 限流；否则所有请求共用 Ingress IP。
- 若链路中存在客户端可直连、且代理只是追加而不覆盖转发头的节点，**不要**开启
  该开关，否则攻击者可伪造头绕过限流。
- 更稳妥的做法仍是在 Ingress / API Gateway 做第一层全局限流，应用层作为兜底。

### 1.4 可插拔计数接口

`rate_limit.py` 新增 `RateLimitCounter` Protocol：

```python
class RateLimitCounter(Protocol):
    is_shared: bool

    def allow(self, key: str, limit: int, window_seconds: float) -> bool: ...
```

- 默认实现 `InMemorySlidingWindowCounter`：`is_shared = False`，按
  `time.monotonic()` 的滑动窗口计数；
- 保留 `SlidingWindowLimiter` 别名，兼容既有 import / 测试；
- `RateLimitMiddleware(..., counter=...)` 可注入自定义实现；`is_shared=True`
  的实现不会再触发多副本告警。

### 1.5 多副本告警

中间件实例化时（Starlette 在启动构建 middleware stack 时执行）调用
`detect_worker_count()`（`EASY_REPLICA_COUNT` / `WEB_CONCURRENCY` /
`UVICORN_WORKERS`）：

- `workers > 1` 且 `counter.is_shared == False`：打印一条 warning，说明
  「各实例独立计数，实际放行量约为配置阈值的 N 倍」，并指向本设计文档；
- **不阻断启动**，也不改变单机行为；
- 每个进程各打印一次，便于在启动日志中直接看到。

这是「不静默」的最低要求，不等于解决了全局限流：多副本下仍需共享计数或
网关限流，见 §1.6。

### 1.6 后续：共享计数实现路径（未做）

两种可插拔实现均可直接满足 `RateLimitCounter`：

1. **Redis（推荐，低延迟）**
   - 固定窗口：`INCR rl:{prefix}:{ip}:{floor(now/window)}`，首次 `EXPIRE
     window+1`，`count > limit` 拒绝；
   - 精确滑动窗口：ZSET + `ZREMRANGEBYSCORE` + `ZCARD` + `ZADD`，用 Lua
     保证原子性；
   - 需明确 Redis 故障策略：fail-open（放行，保可用）或 fail-closed（拒绝，
     保安全），并加超时与降级日志。
2. **MySQL（不引入新组件）**
   - 表 `rate_limit_counter(limit_key, window_start, hits, PRIMARY KEY(limit_key))`；
   - 单条原子 upsert：同窗口 `hits = hits + 1`，跨窗口重置为 1，再判断是否超限；
   - 需定期清理过期行，避免表膨胀；热点 IP 会有行锁竞争。

另外：

- 多副本下**会话粘滞不能替代限流**，攻击者仍可把请求打到不同副本；
- Ingress / API Gateway 的 IP 维度限流应作为第一层；
- 共享计数上线后，建议同时补充命中率、拒绝率、Redis/DB 失败降级次数指标。

### 1.7 已知风险（本批未改）

- 默认内存计数器的 `_hits` 字典按 `prefix:IP` 累积，空 bucket 不会主动删除；
  公网大基数 IP 下内存会持续增长。单机小流量可接受，公网部署应换共享实现或
  有界 LRU 实现（后续批次）。
- 默认实现非原子跨进程，阈值会随副本数放大（见 §1.5）。
- 只按「路径前缀 + IP」计数，未按用户/租户维度区分。

## 2. 生产 JWT 密钥契约

### 2.1 纯函数

`easy_agent/utils/auth.py` 新增：

```python
def validate_production_jwt_secret(
    env: str | None = None, secret: str | None = None
) -> None: ...
```

语义：

| env | secret / `EASY_JWT_SECRET` | 结果 |
| --- | --- | --- |
| `prod`（大小写不敏感） | 缺失或仅空白 | 抛 `RuntimeError`，信息明确要求设置 `EASY_JWT_SECRET` |
| `prod` | 非空 | 通过 |
| `dev` / `test` / 其它 / 空 | 任意 | 通过（保留文件回退） |
| `env=None` | — | 读取 `AGENT_ENV`，缺省 `dev` |

函数无副作用，不在模块 import 时调用。

### 2.2 启动期调用

`easy_agent/app.py` 的 `lifespan` 在读取 `agent_env` 后、其余初始化之前调用：

```python
from .utils.auth import validate_production_jwt_secret

validate_production_jwt_secret(env=agent_env)
```

- 仅 `AGENT_ENV=prod` 且未配置密钥时启动失败（uvicorn 启动报错退出）；
- dev/test 不校验，pytest 与工具脚本仅 import `app` 不会失败；
- 校验放在 lifespan 而非 import 期，避免「import 即崩」影响测试收集。

### 2.3 禁止 prod 回退 `data/.jwt_secret`

`_load_or_create_secret()` 调整：

1. `EASY_JWT_SECRET` 非空（去空白后）直接使用；
2. **prod 且未配置**：记录 error 日志，返回进程内临时随机密钥，**不写**
   `data/.jwt_secret`；随后 lifespan 校验直接拒绝启动；
3. dev/test：原有行为不变——优先已有 `data/.jwt_secret`，没有则生成并
   `chmod 600`，使单机多 worker 共享同一密钥；
4. 文件读写异常时回退进程内随机密钥并 warning（行为不变）。

这样即使有人绕过 lifespan（例如只 import 模块跑脚本），prod 也不会把密钥
落到本地运行目录。

### 2.4 运维约定

- 通过部署 Secret 注入 `EASY_JWT_SECRET`（k8s 样例
  `deploy/k8s/agent-deployment.yaml` 已从 `easy-agent-secrets/jwt-secret`
  注入），禁止写进仓库；
- 建议 >=32 字节随机值，例如 `openssl rand -base64 48`；
- 所有副本必须使用同一密钥，否则 A 副本签发的 token 在 B 副本校验失败；
- **迁移注意**：若旧部署依赖 `data/.jwt_secret`，直接改为环境变量会使旧
  token 全部失效（一次性重新登录）。平滑做法是把现有文件值复制到 Secret，
  再切换部署；
- 轮换密钥等同全量登出；当前没有 `kid` / 多密钥并存机制。

## 3. 验收与测试映射

| 验收点 | 测试 |
| --- | --- |
| 429 返回 JSON 而非 500 | `tests/middleware/test_rate_limit.py::test_rate_limit_returns_json_429` |
| 限流按 `get_client_ip` 而非 socket 对端 | `...::test_rate_limit_uses_client_ip_not_socket_peer`、`...::test_rate_limit_separates_different_client_ips` |
| 可插拔共享计数器 | `...::test_custom_shared_counter_is_used` |
| 多副本非共享告警且只提示一次 | `...::test_multiworker_warns_once_for_process_local_counter` |
| 共享计数不告警 | `...::test_multiworker_does_not_warn_for_shared_counter` |
| prod 无 secret 启动失败 | `tests/test_jwt_prod_contract.py::test_prod_without_env_secret_raises`、`...::test_lifespan_prod_fails_before_serving` |
| prod 有 secret 通过 | `...::test_prod_with_secret_passes` |
| dev/test 无 secret 通过 | `...::test_dev_and_test_without_secret_pass`、`...::test_dev_secret_loader_still_uses_file` |
| prod 不写密钥文件 | `...::test_prod_secret_loader_does_not_fall_back_to_file` |

## 4. 明确未做

- 多副本共享计数（Redis/MySQL）落地与故障策略；
- Ingress / API Gateway 限流样例；
- 限流指标（拒绝率、降级次数）与告警规则；
- 按用户/租户维度限流、验证码/人机校验；
- JWT 密钥轮换、`kid`、多密钥并存；
- 全量回归（由 Lead 统一执行，本批只跑相关子集）。


## 2026-10-18 第二批进展：共享计数

- `easy_agent/middleware/rate_limit.py` 新增 `DBRateLimitCounter`（表 `rate_limit_counters`，
  固定窗口、原子 UPDATE→INSERT→UPDATE 重试）；`EASY_RATE_LIMIT_BACKEND=auto|db|memory`
  默认 `auto`：多 worker/副本选 DB，单机选内存。
- DB 不可用时降级为进程内计数并告警一次。
- 表随 `SCHEMA_VERSION=7` 由启动 DDL 创建；`EXPECTED_TABLES` 已加入。
- 测试：`tests/middleware/test_rate_limit_shared.py` 5 项 + 既有限流测试。
- 未做：Redis 共享实现、窗口桶 GC 指标、Ingress 层限流；DB 计数器每个受限请求多 1–2 次 DB 往返。
