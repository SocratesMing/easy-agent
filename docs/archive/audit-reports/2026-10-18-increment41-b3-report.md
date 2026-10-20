# Increment 41 · B3 第一批报告（限流 429 / 生产 JWT 契约）

| 项 | 内容 |
| --- | --- |
| 日期 | 2026-10-18 |
| 范围 | 架构改进计划阶段 B3 第一批（不依赖多副本的项） |
| 结论 | 相关测试 43 passed；`uvx ruff check .` 通过；**未跑全量**（由 Lead 统一执行） |
| 设计 | `docs/design/2026-10-18-b3-rate-limit-jwt.md` |

## 1. 改动清单

| 文件 | 改动 |
| --- | --- |
| `easy_agent/middleware/rate_limit.py` | 重写限流中间件：真实 IP 键、JSON 429、可插拔 `RateLimitCounter` + `InMemorySlidingWindowCounter`、多副本告警；保留 `SlidingWindowLimiter` 别名与 `limiter` 属性兼容 |
| `easy_agent/utils/auth.py` | 新增纯函数 `validate_production_jwt_secret(env=None, secret=None)`；`_load_or_create_secret()` 在 prod 无环境变量时不再写 `data/.jwt_secret`，改为临时密钥 + error 日志（由 lifespan 拒绝启动） |
| `easy_agent/app.py` | lifespan 早期调用 `validate_production_jwt_secret(env=agent_env)`（仅 prod 生效） |
| `tests/middleware/test_rate_limit.py` | 新增 8 项：429 JSON、真实 IP、IP 隔离、共享计数器注入、多副本告警/不告警、旧滑动窗口行为 |
| `tests/test_jwt_prod_contract.py` | 新增 8 项：prod 无/空/有 secret、dev/test/缺省 env、prod 不落文件、dev 文件回退、lifespan fail-fast |
| `.env.prod.example` | JWT 注释改为“生产必填、缺失启动失败、不回退文件”；补充 `EASY_TRUST_PROXY` 说明 |
| `docs/design/2026-10-18-b3-rate-limit-jwt.md` | 新增设计文档（含后续 Redis/MySQL 共享计数路径） |
| `docs/audit/2026-10-18-increment41-b3-report.md` | 本报告 |

未改动：API 层、DB、streaming、MCP、memory、`tests/conftest.py`、前端；未
`git commit`。

## 2. 关键行为

### 2.1 限流

- 限流键：`f"{prefix}:{get_client_ip(request)}"`，不再只用
  `request.client.host`；`get_client_ip` 仅在 `EASY_TRUST_PROXY=1` 时信任
  `X-Forwarded-For` / `X-Real-IP`。
- 超限返回：

  ```json
  {"detail": "请求过于频繁，请稍后再试", "code": "RATE_LIMITED"}
  ```

  `JSONResponse(status_code=429)`，不再 `raise HTTPException`，因此不会变 500。
- 既有规则/阈值未变。
- 中间件实例化时若 `detect_worker_count() > 1` 且计数器
  `is_shared == False`，打印一次 warning（不阻断），明确说明阈值会随副本数
  放大并指向设计文档。
- 共享计数接口：`RateLimitCounter.allow(key, limit, window_seconds)`；
  自定义实现标记 `is_shared = True` 即不再告警。

### 2.2 生产 JWT

| 场景 | 结果 |
| --- | --- |
| `AGENT_ENV=prod` + 未设/空白 `EASY_JWT_SECRET` | lifespan 抛 `RuntimeError`，启动失败 |
| `AGENT_ENV=prod` + 已设 `EASY_JWT_SECRET` | 通过 |
| dev / test / 未设置 `AGENT_ENV` | 通过；保留 `data/.jwt_secret` 文件回退 |
| prod 下 `_load_or_create_secret()` | 返回进程内临时密钥，**不创建** `data/.jwt_secret` |

校验在 lifespan 而非 import 期，pytest/工具仅 import `app` 不会失败。错误信息
明确要求通过部署 Secret 注入 `EASY_JWT_SECRET`。

## 3. 测试

执行命令（只跑相关子集）：

```bash
uv run pytest tests/middleware tests/test_jwt_prod_contract.py tests/api/test_auth.py -q
```

结果：

```text
43 passed, 1 warning in 16.92s
```

1 条 warning 为既有 `fastapi.testclient` 的 StarletteDeprecationWarning
（httpx → httpx2），与本次改动无关。

Lint：

```bash
uvx ruff check .
# All checks passed!
```

**未执行全量 `pytest tests/`**，需由 Lead 统一回归；本批改动集中在限流中间件、
auth 纯函数与 lifespan 单点，未触碰 API/DB/streaming/MCP/memory。

## 4. 未做 / 后续风险

1. **多副本共享计数未实现（最重要）**：当前仍是每进程独立内存计数，N 个
   worker/副本时实际放行量约为阈值的 N 倍；只有一条 warning。设计文档给出了
   Redis（INCR/EXPIRE 或 ZSET+Lua）与 MySQL 原子 upsert 两条落地路径，以及
   fail-open / fail-closed 选择。多副本上线前必须补齐，或在 Ingress 统一限流。
2. 内存计数器 `_hits` 中空 bucket 不淘汰，公网大基数 IP 下内存会增长；本批
   未改（避免扩大改动面），后续换共享实现或有界 LRU。
3. `EASY_TRUST_PROXY=1` 的前提是代理覆盖而非追加转发头；配置错误会让攻击者
   伪造 IP 绕过限流。
4. JWT 密钥轮换/`kid`/多密钥并存未做；从 `data/.jwt_secret` 迁移到环境变量会
   使旧 token 全部失效（需提前把文件值写入 Secret）。
5. k8s 样例 `deploy/k8s/agent-deployment.yaml` 已存在
   `EASY_JWT_SECRET <- easy-agent-secrets/jwt-secret` 注入，本批未改部署文件。
6. 全量回归、跨副本行为、真实 Ingress 下的 IP 取值未在本批验证。
