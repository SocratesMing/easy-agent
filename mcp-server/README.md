# Easy MCP Server

Easy Agent 的独立 MCP（Model Context Protocol）服务子项目，使用 [uv](https://docs.astral.sh/uv/) 管理依赖，
与主应用 `easy-agent` 解耦，可独立部署。

```
主应用 easy-agent          mcp-server 子项目
────────────────          ─────────────────
设置页签发 API Key  ──写入──▶  mcp_api_keys 表（共享 MySQL，本侧只读）
设置页读业务清单    ◀──写入──  mcp_businesses 表（本侧登记，主应用只读）
用户 mcp.json        ──HTTP──▶  /mcp/<业务>/  (Bearer key)
```

边界：主应用不 import 本子项目；本子项目不 import 主应用。

## 模块边界与对外契约

本子项目是**独立模块**，对外承诺的全部内容集中在
`easy_mcp_server/contract.py`（唯一事实来源）：

| 契约 | 内容 |
| --- | --- |
| 业务清单 | `businesses/` 下的包名即业务名（`contract.business_names()`） |
| URL 形态 | `{base}/mcp/{business}/`（**必须有尾斜杠**） |
| 鉴权 | `Authorization: Bearer mcp_<token>`，库中只存 sha256 |
| 数据 | 共享库 `mcp_api_keys`（主应用写、本侧读）、`mcp_businesses`（本侧写、主应用读） |

依赖方向单向：**主应用读契约 → 本子项目**，本子项目从不反向依赖。

因此：**新增/删除业务只改 `businesses/` 一个目录**，重启本服务即生效，
主应用（设置页业务下拉、Key 签发白名单）自动感知，无需任何改动。
这是通过启动时把清单登记进 `mcp_businesses` 实现的（`registry.py`）：

```bash
# 加一个业务包，重启，主应用立刻能看到
mkdir easy_mcp_server/businesses/forex   # 内含 build() -> FastMCP
uv run uvicorn easy_mcp_server.main:app --port 8100
# 日志：业务注册表已更新: strategyqa
```

外部还可以直接拉 `GET /manifest`（免鉴权）拿到机器可读的业务清单与 URL，
与读 `mcp_businesses` 表等价，任选其一。

> 主应用侧留了一份内置兜底清单（`easy_agent/services/mcp_api_keys.py`），
> 仅用于本服务尚未部署/从未启动时保证设置页可用，不代表"当前可用业务"。

## 数据库配置（独立于主应用）

本子项目**不复用主应用的配置文件**，自己读 `mcp-server/.env`：

| | 主应用 easy-agent | mcp-server |
| --- | --- | --- |
| 配置位置 | `easy_agent/config/config.yaml` → `database.mysql` | `mcp-server/.env` |
| 读取代码 | `easy_agent/db/database.py` | `easy_mcp_server/db.py::mysql_config()` |
| 入 git | 否 | 否 |

两边唯一的约定是**指向同一个库**：主应用往 `database.mysql.database` 写
`mcp_api_keys`，本子项目从 `MYSQL_DATABASE` 读它。这个约定不由代码保证，
不一致时的症状是「主应用签发了 key，本子项目一律 401」，没有任何报错指向配置。

所以启动时会打印一行连接目标，便于第一时间发现配错：

```
[mcp-server] MySQL 目标: 127.0.0.1:3306/market
```

改库名/账号要同时改两处。`QUERYKIT_*` / `STRATEGY_*` / `MCP_*` 只属于本子项目。

## 快速开始

```bash
cd mcp-server
uv sync                                   # 安装依赖
cp .env.example .env                      # 填写 MySQL 连接等配置
uv run python -m pytest                   # 运行测试
uv run uvicorn easy_mcp_server.main:app --host 0.0.0.0 --port 8100 \
    --timeout-graceful-shutdown 5         # 启动
```

启动后 `GET /health` 可用，`GET /health` 返回已挂载的业务清单。

### 关闭（Ctrl+C 卡在 "Shutting down"）

uvicorn 默认 `timeout_graceful_shutdown=None`，含义是**无限等待**现有连接与后台任务
结束，只会打印 `Shutting down` 然后一直卡住。本项目长连接多（SSE 流式输出、终端
WebSocket），所以启动时要显式给上限：

```bash
--timeout-graceful-shutdown 5
```

另外 lifespan 的收尾（退出每个业务的 `session_manager.run()`）**不在这个超时覆盖范围内**
——它要等进行中的请求跑完，比如一次还没结束的慢查询。所以 `app.py` 里
`stack.aclose()` 自己兜了一层 `MCP_SHUTDOWN_TIMEOUT`（缺省 5 秒），超时只告警不阻塞退出。

已经卡住时的应急手段：**再按一次 Ctrl+C**。uvicorn 的 `handle_exit` 在
`should_exit` 已为真时收到第二次 SIGINT 会置 `force_exit=True`，直接跳过等待退出。

## 业务目录约定（新增业务零主代码改动）

`easy_mcp_server/businesses/<name>/` 下的每个包是一个业务，包内暴露 `build() -> FastMCP`，
启动时自动发现并挂载到 **`/mcp/<name>/`**（包名即 URL 后缀），同时登记到
`mcp_businesses` 让主应用感知——**加业务不需要动主应用任何代码**。

发现入口只有一个：`contract.business_names()`（只扫目录、不 import），
`app.discover_businesses()` 与 `scripts/init_keys.py` 都复用它，避免出现第二份清单。

已挂载业务：

| 业务 | 路径 | 说明 |
| --- | --- | --- |
| `strategyqa` | `/mcp/strategyqa/` | 问数：策略表（`fmut2_strategy_manage`）领域工具 + 通用只读 SQL |

**新增**业务只删/加这个目录即可（重启服务后主应用自动感知）；
**删除**业务除了删目录，还要清共享库里的残留记录，见下文「删除业务」。

### querykit：跨业务共享的问数底座

各问数业务都需要同一套底层能力，统一放在 `easy_mcp_server/querykit/`，避免各包复制：

| 模块 | 职责 |
| --- | --- |
| `guard.py` | SQL 只读网关：单条语句、只允许 `SELECT/WITH`、屏蔽写操作与系统库、自动补 `LIMIT` |
| `pool.py` | 进程级 MySQL 连接池 + 会话加固（`TRANSACTION READ ONLY` / `MAX_EXECUTION_TIME`） |
| `explore.py` | 表/字段探索（读 `information_schema`），可见性由 `ExplorePolicy` 控制 |
| `config.py` | 统一读 `QUERYKIT_*` 环境变量 |

业务包只写"领域层"（查什么、怎么查、口径是什么）；新增问数场景不必再复制网关与连接池。

### strategyqa：策略表问数

表 `fmut2_strategy_manage`（策略清单：名称、标签、类别、作者、收益率、报告等）。领域工具：

**权限过滤**（两层，都不可绕过）：

1. `AUTHOR = 当前登录账号` —— 只看自己创建的策略
2. `AUTH_VIEW = 1` —— 源库标记为 0（不可查看）的记录**对任何人都不返回，包括作者本人**

因此主应用用户名必须与表里的 `AUTHOR` 一致（统一账号体系）；不一致时结果为空，
`summary` 会明确写出「账号 xxx 名下没有策略」便于排查。

领域工具：

- `describe_strategy_schema`（口径字典，先看这个）
- `list_strategies`（我的策略清单；支持 keyword / strategy_type / source / report_type
  筛选，以及 order_by / descending 排序）
- `get_strategy`（按 `STRATEGY_ID` 查我的策略详情）
- **结构探索**：`list_tables` / `describe_table`（只读 `information_schema`，不返回业务数据）

为了权限闭环，**刻意不提供**两个工具：

- `query`（任意 SQL）—— 否则模型能自己拼 SQL 绕开 `AUTHOR` 过滤
- `sample_rows`（整表采样）—— 否则能采样出他人策略的任意行

几个刻意处理的坑：

1. `CREATE_TIME` / `UPDATE_TIME` 是**毫秒时间戳**（`1789952000000`），不是
   `YYYYMMDDHHMMSS`；结果里额外给出 `create_time_text` / `update_time_text` 便于引用
2. `PROFIT_LOSS_CHART` 是 text 大字段——**列表与详情都不返回**
3. `TOTAL_YIELD` 是比例（`0.155` = 15.5%），不是百分数
4. `STRATEGY_TAGS` 是逗号分隔字符串，只能 LIKE 模糊匹配
5. `AUTH_VIEW=0` 的记录谁都不能看（含作者），已在 SQL 里固定过滤
6. `ORDER BY` 走字段白名单映射，模型无法借此注入

### 安全与可见性（由 querykit 提供）

SQL 网关强制：单条语句、`SELECT/WITH` 开头、屏蔽写操作与系统库、自动补 `LIMIT`
（`QUERYKIT_MAX_ROWS`，缺省 500）；连接叠加 `SET SESSION TRANSACTION READ ONLY`
与 `MAX_EXECUTION_TIME` 兜底。

权限分两层：

- **行级**：`strategyqa` 强制 `AUTHOR = 当前账号`，且不暴露任何能绕开它的工具
  （没有 `query` / `sample_rows`）。实现上 AUTHOR 是 SQL 里固定拼接的第一个条件，
  调用方无法通过任何参数放松
- **表级**：业务构造 `ExplorePolicy` 控制结构探索范围；`strategyqa` 读
  `STRATEGY_ALLOWED_TABLES` / `STRATEGY_DENY_TABLES`，默认屏蔽 `mcp_api_keys`
  与下划线开头的内部表

每次查询都会记录调用者 username。

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
    "strategyqa": {
      "transport": "streamable_http",
      "url": "http://<mcp-server 地址>:8100/mcp/strategyqa/",
      "headers": { "Authorization": "Bearer <API Key>" }
    }
  }
}
```

注意：

- **URL 必须带尾斜杠**。MCP 客户端不跟随 307 重定向，`/mcp/strategyqa` 会被 307 到 `/mcp/strategyqa/` 而失败
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

| 表 | 建表方 | 读 | 写 |
| --- | --- | --- | --- |
| `mcp_api_keys` | 主应用 `init_tables`（本子项目 `scripts/init_keys.py` 可自举） | 本子项目 | 主应用 |
| `mcp_businesses` | 本子项目启动时自愈建表，主应用 `init_tables` 亦建 | 主应用 | 本子项目 |

> 两表的 DDL 在两侧各有一份（`easy_mcp_server/contract.py` 与
> `easy_agent/db/database.py::_create_misc_tables`），**改结构必须同步**。
> 之所以不抽公共文件：两个模块可能分机器部署，跨目录读文件比这一小段
> 稳定 DDL 更脆弱。

业务数据表各自维护：策略表 `fmut2_strategy_manage` 属于业务系统，本子项目只读。

> 2026-09 先后移除了 `dataqa`（XBOND 债券）、`hbaseqa`（外汇）与 `market`（行情/持仓）
> 三个业务，库内对象已清理；如需重新启用，可从 git 历史恢复对应业务包。

### 删除业务（必须配合下线脚本）

`registry.publish()` 只做 upsert、**从不删除**，所以删掉 `businesses/<name>/` 之后，
共享库里还会留下两类残留，都不会自动消失：

- `mcp_businesses` 的登记记录 —— 会让主应用设置页一直显示已下线的业务，
  用户还能对它签发一把永远用不了的 Key
- `mcp_api_keys` 的密钥记录 —— 业务已不存在，这些 Key 无法通过任何 URL 使用，
  但会一直留在库里，且看起来是"正常未吊销"状态

```bash
uv run python -m scripts.retire_business oldbiz                      # 预览（缺省不删）
uv run python -m scripts.retire_business oldbiz --apply              # 执行
uv run python -m scripts.retire_business oldbiz --apply --keep-keys  # 只清注册表，留 Key
```

顺序：删业务包目录 → 重启服务确认清单已更新 → 跑本脚本。

脚本自带两道保护：缺省只预览不删；业务包仍在 `businesses/` 下时直接拒绝
（否则会出现"服务仍在提供该业务，但设置页看不到、无法签发 Key"的错位状态）。

> **为什么不做成 `publish()` 的自动行为**：多实例部署（多 pod / 主备中心）滚动更新时
> 版本不一致，"某实例发现业务少了就删"会误删其他版本实例刚登记的业务。
> 删业务是低频动作，用一条显式命令换掉这个风险是划算的。

## 测试

```bash
uv run python -m pytest          # 契约、挂载、鉴权、身份注入、传输安全、SQL 网关、策略问数
```

`tests/test_contract.py` 专门钉住对外契约（业务清单 / URL 形态 / 哈希算法 / 表结构）——
契约漂移不会报错，只会让主应用侧静默 401，所以必须有测试兜住。
（用 `python -m pytest` 而不是 `pytest`：后者可能解析到 PATH 上的全局 pytest，
在项目包不可见的环境里会以 `ModuleNotFoundError: easy_mcp_server` 失败。）
