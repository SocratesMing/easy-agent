# Easy MCP Server

Easy Agent 的独立 MCP（Model Context Protocol）服务子项目，使用 [uv](https://docs.astral.sh/uv/) 管理依赖，
与主应用 `easy-agent` 解耦，可独立部署。

```
主应用 easy-agent          mcp-server 子项目
────────────────          ─────────────────
设置页签发 API Key  ──写入──▶  mcp_api_keys 表（共享 MySQL）
用户 mcp.json        ──HTTP──▶  /mcp/<业务>/  (Bearer key)
```

边界：主应用不 import 本子项目；本子项目不 import 主应用。二者仅通过
`mcp_api_keys` 表结构与 MCP HTTP 协议耦合。

## 快速开始

```bash
cd mcp-server
uv sync                                   # 安装依赖
cp .env.example .env                      # 填写 MySQL 连接等配置
uv run python -m easy_mcp_server.businesses.market.seed   # 建 market 业务表 + 演示数据
uv run pytest                             # 运行测试
uv run uvicorn easy_mcp_server.main:app --host 0.0.0.0 --port 8100   # 启动
```

启动后 `GET /health` 可用，`GET /health` 返回已挂载的业务清单。

## 业务目录约定（新增业务零主代码改动）

`easy_mcp_server/businesses/<name>/` 下的每个包是一个业务，包内暴露 `build() -> FastMCP`，
启动时自动发现并挂载到 **`/mcp/<name>/`**（包名即 URL 后缀）。

```python
# easy_mcp_server/businesses/forex/__init__.py
from mcp.server.fastmcp import FastMCP
from ...context import current_username

def build() -> FastMCP:
    mcp = FastMCP("forex", streamable_http_path="/", stateless_http=True)

    @mcp.tool()
    def get_rate(symbol: str) -> dict:
        user = current_username()   # 鉴权中间件注入，业务零感知
        ...
    return mcp
```

## 客户端接入（mcp.json）

用户在主应用设置页为每个业务生成 API Key（每用户 × 每业务一把，明文只显示一次），
然后把下面片段粘进 `mcp.json` 即可：

```json
{
  "servers": {
    "market-data": {
      "transport": "streamable_http",
      "url": "http://<mcp-server 地址>:8100/mcp/market/",
      "headers": { "Authorization": "Bearer <API Key>" }
    }
  }
}
```

注意：

- **URL 必须带尾斜杠**。MCP 客户端不跟随 307 重定向，`/mcp/market` 会被 307 到 `/mcp/market/` 而失败
- `transport` 支持 `streamable_http` / `streamable-http` / `http`（三者等价）

## 鉴权

- 所有 `/mcp/<业务>/` 路径需要 `Authorization: Bearer <API Key>`；`/health` 免鉴权
- 子项目按 `(business, sha256(key))` 只读查询 `mcp_api_keys` 表反查 `username`
- 校验结果有 60s 进程内缓存（`MCP_KEY_CACHE_TTL` 可调），吊销/重签后最多延迟一个 TTL 生效
- 数据库不可用时拒绝所有请求（fail closed）

## 传输安全（重要）

FastMCP 对 `127.0.0.1/localhost` 默认开启 DNS rebinding 保护；通过域名或反向代理访问时，
**不配置白名单所有请求会返回 421**。在 `.env` 中：

```
MCP_ALLOWED_HOSTS=mcp.example.com,mcp.example.com:*
MCP_ALLOWED_ORIGINS=https://mcp.example.com
```

注意：`host:*` 模式只匹配**带端口**的 Host；https 默认端口的 Host 不带端口，
两种写法都要列上。

## 表结构

主应用 `easy_agent/db/database.py::init_tables` 负责建 `mcp_api_keys` 表；
业务数据表（如 `market_quotes` / `market_positions`）由 `businesses/market/seed.py` 创建。

## 测试

```bash
uv run pytest          # 30+ 用例：挂载、鉴权、身份注入、用户隔离、传输安全
```
