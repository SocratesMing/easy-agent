# Market MCP 示例

这是一个“智能问数”示例：后端通过 MCP 调用行情工具，用户可以直接问“查询最新的贵金属行情，黄金的行情”。服务器不会把 SQL 暴露给智能体，只提供固定的、参数化的数据工具。

## 1. 初始化示例数据

在仓库根目录执行：

```bash
.venv/bin/python -m easy_agent.mcp_servers.market.seed_mysql
```

脚本会：

- 创建 `market_quotes`、`market_positions`、`market_mcp_api_keys` 三张表；
- 写入贵金属和外汇的伪造行情；
- 写入两个用户 `szm`、`zr6` 的示例持仓；
- 为每个用户生成一个一次性展示的 API Key。

如需重新生成 Key：

```bash
.venv/bin/python -m easy_agent.mcp_servers.market.seed_mysql --rotate-keys
```

## 2. 数据库连接配置

Market MCP 和 Key 生成接口默认复用主应用配置文件中的 `database.mysql` 配置，数据库连接信息
不会写入 `mcp.json`。因此只要后端日志显示 MySQL 初始化成功，生成 Key 时会使用同一套连接配置。

如需独立运行 MCP 进程且主应用配置不是 MySQL，可再使用 `.env` 作为兜底配置：

```bash
cp .env.example .env
```

然后在 `.env` 中填写：

```dotenv
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your-mysql-password
MYSQL_DATABASE=agent
```

服务器会先读取主应用 MySQL 配置；读取失败或配置为 SQLite 时，再加载项目根目录的 `.env`。

## 3. 生成用户 API Key

在 Web 界面进入「设置 → MCP」，点击「预览」左侧的「生成 Key」按钮，即可为当前登录用户生成
`MARKET_MCP_API_KEY`。生成结果会显示在 MCP 列表上方，默认隐藏，点击眼睛图标可切换显示。

接口为：

```http
POST /agent/settings/mcp/api-key
```

每次生成都会替换该用户的旧 Key；数据库只保存 SHA-256 哈希，明文只在生成时返回一次。刷新页面后无法找回
明文，如丢失请重新生成。

## 4. 配置用户级 MCP

把下面的内容保存到 `workspace/<username>/mcp.json`，只替换 API Key：

```json
{
  "servers": {
    "market-data": {
      "transport": "stdio",
      "command": ".venv/bin/python",
      "args": ["-m", "easy_agent.mcp_servers.market.server"],
      "env": {
        "MARKET_MCP_API_KEY": "replace-with-your-api-key"
      }
    }
  }
}
```

每个用户的 `MARKET_MCP_API_KEY` 都不同，后端会据此识别用户并自动过滤持仓数据。

## 5. 示例提问

配置完成后，可以在对话里直接问：

```text
查询最新的贵金属行情，黄金的行情
```

或：

```text
看一下美元兑人民币汇率
```

也可以问：

```text
查看我的持仓
```

## 6. 安全说明

- API Key 只保存 SHA-256 哈希，不保存明文；
- `get_my_positions` 不接受用户名参数，用户身份由 `MARKET_MCP_API_KEY` 推导；
- 所有数据库访问都使用固定 SQL 和参数绑定；
- 数据库连接信息保存在本地 `.env`，不会出现在 `mcp.json`；
- 行情数据对所有已认证用户可见，持仓数据按用户隔离。
