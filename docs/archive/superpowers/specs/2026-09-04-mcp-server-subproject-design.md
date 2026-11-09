# 独立 uv 子项目承载 MCP Server 设计

- 日期：2026-09-04
- 状态：已批准，进入实施
- 范围：新建 `mcp-server/` 独立 uv 子项目；主应用 MCP API Key 签发改造；Market 业务迁移
- 选定方案：
  - Key 粒度：**每用户 × 每业务一把**
  - Key 存储：**共用主项目 MySQL**（主应用写、子项目只读校验）
  - 组织方式：**目录自动发现**（`businesses/<name>/` → `/mcp/<name>`）
- 关联：取代 `docs/superpowers/plans/2026-09-04-mcp-server-subproject.md` 中的设计部分（任务拆分仍可参考）

## 背景

现有 MCP 实现位于 `easy_agent/mcp_servers/market/server.py`：一个 FastMCP **stdio** 服务，自行连接 MySQL、自行建并查 `market_mcp_api_keys` 表校验身份，API Key 通过环境变量 `MARKET_MCP_API_KEY` 注入。主应用 `easy_agent/api/settings.py` 通过

```python
from ..mcp_servers.market.server import issue_api_key as generate_market_mcp_api_key
```

直接复用其签发函数——即主应用与 MCP server 处于源码级耦合，MCP server 无法独立部署、独立演进。

配置侧：`easy_agent/services/mcp.py` 已支持 stdio / sse / http 三种 transport，用户级 `mcp.json`（`{workspace}/{username}/mcp.json`）优先于全局 `easy_agent/config/mcp.json`。但现有 http 示例均为"一个 URL = 一个 server"，不存在"按业务分 URL 后缀"的概念。

已核实的关键事实（影响设计可行性）：

1. `langchain_mcp_adapters` 的合法 transport 值为 `streamable_http` / `streamable-http` / `http`（后两者为别名，均走 Streamable HTTP）、`sse`、`stdio`、`websocket`。现有配置中的 `"http"` 合法。
2. `FastMCP.streamable_http_app()` 存在；`streamable_http_path` 默认 `/mcp`，`stateless_http` 默认 `False`。
3. MCP 客户端 httpx 未开启 `follow_redirects`，而 Starlette `Mount` 对无尾斜杠路径会返回 307——故最终 URL 形态必须实测，不能假定。

## 现状问题

1. **源码级耦合**：主应用 import MCP server 内部函数签发 key，子项目无法独立部署/发版。
2. **身份传递方式落后**：API Key 走环境变量，一个进程只能服务一个用户，无法多用户共用同一 MCP 服务。
3. **业务扩展成本**：新增业务需新建 `mcp_servers/<name>/` 并在主应用加对应的 key 签发端点与表（`market_mcp_api_keys` 是按业务建表的思路）。
4. **传输方式不匹配**：stdio 意味着每个 MCP server 是主应用拉起的子进程，无法作为独立远程服务复用。

## 目标与验收

- `mcp-server/` 为独立 uv 项目：拥有自己的 `pyproject.toml` / `uv.lock`，`cd mcp-server && uv run pytest` 可独立跑通，不依赖主应用可导入。
- 新增业务 = 子项目内新增一个包目录，主应用零改动；启动日志打印挂载清单。
- 用户 `mcp.json` 中每个 server 只需 `transport` + `url` + `headers.Authorization` 三项即可接入。
- 主应用不再 import 子项目任何代码（以断言测试锁定）。
- 鉴权：缺失/错误/跨业务的 key 一律 401；同用户同业务重签后旧 key 立即失效。
- 用户数据隔离：A 用户的 key 取不到 B 用户的持仓等私有数据。
- Market 业务行为保持不变（`ask_market` / `get_latest_quotes` / `get_my_positions` 三个工具签名与返回结构不变）。

## 非目标

- 不改造 `services/mcp.py` 的 transport 解析链路（已可用）。
- 不做每个业务独立进程/独立端口（当前仅 1 个业务，运维成本不划算）。
- 不实现 key 的细粒度权限（只读/读写）、配额、调用审计。
- 不提供子项目的 Web 管理界面。
- 不改动前端 MCP 配置编辑器的交互结构（仅"生成 Key"按钮增加业务选择）。

## 设计

### ① 目录结构与发现规则

```
mcp-server/
├── pyproject.toml
├── README.md
├── .env.example
├── easy_mcp_server/
│   ├── __init__.py
│   ├── app.py          # 组合根：扫描 businesses/ 并挂载 /mcp/<name>
│   ├── auth.py         # Bearer 鉴权中间件 + ContextVar 注入
│   ├── db.py           # PyMySQL 连接管理（读环境变量）
│   ├── context.py      # current_username()
│   └── businesses/
│       ├── __init__.py
│       └── market/
│           ├── __init__.py    # build() -> FastMCP
│           ├── server.py
│           └── README.md
└── tests/
```

发现规则：遍历 `businesses/` 下的包，导入其 `build()` 工厂，返回 `FastMCP` 实例；包名即 URL 后缀。启动时输出：

```
[mcp-server] 已挂载业务: market -> /mcp/market/
```

业务模板：

```python
def build() -> FastMCP:
    mcp = FastMCP("forex", streamable_http_path="/", stateless_http=True)

    @mcp.tool()
    def get_rate(symbol: str) -> dict:
        user = current_username()
        ...
    return mcp
```

### ② URL 形态与挂载方式（spike 已验证）

**结论：采用方案 A，且 URL 必须带尾斜杠。**

```python
mcp = FastMCP("market", streamable_http_path="/", stateless_http=True)
app.mount("/mcp/market", mcp.streamable_http_app())   # → /mcp/market/
```

spike 实测（`tests/test_spike.py`）：

| 项 | 实测结果 |
| --- | --- |
| `POST /mcp/hello/`（带尾斜杠） | 客户端直连成功，工具可调用 |
| `POST /mcp/hello`（无尾斜杠） | **307**，客户端不跟随重定向 → 配置中 URL 必须带尾斜杠 |
| 未知业务 `/mcp/does-not-exist/` | 401（先鉴权），不返回 404，避免暴露业务存在性 |

**关键坑（已解决）**：`app.mount()` 不会执行子应用的 lifespan，而 MCP 的 session manager 必须在 lifespan 中启动，否则每个请求都报
`RuntimeError: Task group is not initialized`。必须在父应用 lifespan 中手动驱动：

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as stack:
        for _, mcp in items:
            await stack.enter_async_context(mcp.session_manager.run())
        yield
```

### ③ 鉴权链路

数据库表（主应用建表，子项目只读）：

```sql
CREATE TABLE IF NOT EXISTS mcp_api_keys (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  username   VARCHAR(64) NOT NULL,
  business   VARCHAR(64) NOT NULL,
  key_hash   CHAR(64)    NOT NULL UNIQUE,
  created_at DATETIME NOT NULL,
  updated_at DATETIME NOT NULL,
  revoked    TINYINT(1) NOT NULL DEFAULT 0,
  UNIQUE KEY uk_user_business (username, business)
);
```

`UNIQUE(username, business)` 保证每用户每业务仅一把有效 key，重签即覆盖 `key_hash`。

请求链路：

```
客户端 POST /mcp/market/ + Authorization: Bearer <key>
  → 全局中间件（仅对 /mcp/ 前缀生效，/health 免鉴权）
  → 从 path 提取 business
  → sha256(key) 查 mcp_api_keys(business, key_hash, revoked=0)
  → 未命中 401 {"error": "invalid_api_key"}
  → 命中则 ContextVar.set(username)，业务用 current_username() 读取
```

- 错误响应不区分"key 不存在"与"业务不匹配"，避免探测。
- key → username 结果做 60s TTL 进程内缓存，减少查库；吊销最多 60s 生效（写入 README）。

### ④ 主应用改动

| 位置 | 改动 |
| --- | --- |
| 新增 `easy_agent/services/mcp_api_keys.py` | `issue_api_key(username, business)` / `revoke_api_key` / `list_key_status`（仅元数据） |
| `easy_agent/api/settings.py` | 删除 `from ..mcp_servers.market.server import ...`；`/agent/settings/mcp/api-key` 接受 `business` 参数并改调新 service |
| 新增 `GET /agent/settings/mcp/api-keys` | 返回各业务 key 的已生成状态与更新时间 |
| 删除 `easy_agent/mcp_servers/` | market 逻辑迁入子项目 |
| 数据迁移 | `market_mcp_api_keys` → `mcp_api_keys`（`business='market'`）一次性脚本；旧 key 明文不可恢复，提示用户重签 |
| `frontend/src/components/SettingsPanel.vue` | "生成 Key"按钮增加业务下拉（当前仅 `market`），生成后展示可复制的配置片段 |

边界原则：主应用只负责签发 key 与发请求，子项目只负责校验 key 与提供工具，二者仅通过 MySQL 表结构与 HTTP 协议耦合。

### ⑤ 配置形态

```json
{
  "servers": {
    "market-data": {
      "transport": "streamable_http",
      "url": "http://127.0.0.1:8100/mcp/market/",
      "headers": {
        "Authorization": "Bearer <设置页生成的 Key>"
      }
    }
  }
}
```

### ⑥ 测试策略

子项目（`cd mcp-server && uv run pytest`）：

- `test_app.py`：挂载清单正确、未知业务 404、`/health` 免鉴权
- `test_auth.py`：缺 key / 错 key / 跨业务 key 均 401；重签后旧 key 失效
- `test_market.py`：三个工具行为不变；A 用户取不到 B 用户持仓

主项目：

- 导入隔离断言：主应用代码中不出现对 `mcp_server` 子项目的 import
- key 签发写入统一表、明文仅返回一次、重复签发覆盖旧 key

### ⑦ 风险与验证结论

| 项 | 结论 |
| --- | --- |
| A. URL 尾斜杠 | **已验证**：必须用 `/mcp/<business>/`。无尾斜杠返回 307，而 MCP 客户端不跟随重定向 |
| B. DNS rebinding 保护 | **已验证**：非白名单 Host 返回 421。部署通过域名/反代访问时，必须显式配置 `allowed_hosts`（否则全部 421） |
| C. 有状态 vs 无状态 | **已验证**：`stateless_http=True` 可用，匹配 langchain"每次调用新建 session"的行为 |
| D. session manager lifespan | **已验证（新发现）**：`mount()` 不传播 lifespan，必须在父应用 lifespan 中手动 `session_manager.run()` |
| E. mcp 版本 | **已验证**：子项目必须锁 `mcp>=1.27,<2`。mcp 2.x 客户端 API 有 breaking change，会与主项目 1.27.x 不兼容 |
| F. 旧 key 迁移 | 明文不可恢复，存量 key 需用户重签，前端需提示重新生成 |

## 实施顺序

1. **Spike**：搭子项目骨架 + 一个 hello 工具，实测 A/B/C 三项，确定 URL 形态与运行参数
2. 子项目骨架落地：发现规则、鉴权中间件、db、context
3. Market 业务迁移 + 子项目测试
4. 主应用改造：service、端点、删除 `easy_agent/mcp_servers/`、数据迁移脚本
5. 前端改造 + 文档（`mcp-server/README.md`、主 README）
6. 全量回归：子项目 pytest、主项目 pytest、`npm run build`
