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
——它要等进行中的请求跑完，比如一次还没结束的 HBase scan。所以 `app.py` 里
`stack.aclose()` 自己兜了一层 `MCP_SHUTDOWN_TIMEOUT`（缺省 5 秒），超时只告警不阻塞退出。

已经卡住时的应急手段：**再按一次 Ctrl+C**。uvicorn 的 `handle_exit` 在
`should_exit` 已为真时收到第二次 SIGINT 会置 `force_exit=True`，直接跳过等待退出。

## 业务目录约定（新增业务零主代码改动）

`easy_mcp_server/businesses/<name>/` 下的每个包是一个业务，包内暴露 `build() -> FastMCP`，
启动时自动发现并挂载到 **`/mcp/<name>/`**（包名即 URL 后缀）。

已挂载业务：

| 业务 | 路径 | 说明 |
| --- | --- | --- |
| `market` | `/mcp/market/` | 行情查询与持仓，固定参数化语句 |
| `dataqa` | `/mcp/dataqa/` | 问数：XBOND 债券行情领域工具 + 通用只读 SQL，复用主库（`MYSQL_*`） |
| `hbaseqa` | `/mcp/hbaseqa/` | 问数：外汇行情（HBase Thrift 只读 scan），表按 渠道/产品类型/数据类型 划分 |

### dataqa：XBOND 本币债券行情

基表 `fmut_mkt_bonddpanyhis` 是 58 列的 5 档宽表（`BID_PRICE1..5` / `OFFER_YIELD1..5`），
直接让模型写 SQL 极易选错档位，且一行 58 列很快撑爆返回上限。因此加了一层领域封装：

- **长表形态**：把 5 档 UNPIVOT 成行，一行 = 一只债券 × 一个时刻 × 一档的双边报价
  （已过滤该档无价格的行）。`get_bond_quotes` / `list_bonds` 走 `long_format_sql()`，
  **把时间与债券条件下推到每个档位分支**，命中基表索引
  `IDX_FMUT_MKT_BONDDPANYHIS(UPDATE_TIME2, BOND_CODE, PRICE_ID)`；指定 `level` 时
  只生成该档分支（少扫 4/5 数据）
- **视图 `v_xbond_depth`**：同样形态，供探索使用。注意 MySQL 对含 `UNION ALL` 的视图
  强制 TEMPTABLE 算法，外层 `WHERE` 不下推索引，所以**只用于 `describe_table` /
  `sample_rows` / 小范围即席 `query`**，不要拿它做月度取数
- **领域工具**：`describe_xbond_schema`（口径字典）、`list_bonds`（近 N 天有哪些债券）、
  `get_bond_quotes`（近 N 天行情，默认 `days=30 / granularity=daily / level=1`）
- **通用兜底**：`list_tables` / `describe_table` / `sample_rows` / `query`

"Xbond 近一个月各债券行情"直接走 `get_bond_quotes`，生成的是带 JOIN 的每日最后一条
快照，30 天 × N 只债券也只有几百行；`granularity` 支持 `daily`（默认，每债每天最后
一条）/ `latest`（每债最新一条）/ `raw`（明细）。

```bash
uv run python -m easy_mcp_server.businesses.dataqa.seed          # 建/重建长表视图
uv run python -m easy_mcp_server.businesses.dataqa.seed --check  # 只检查基表是否存在
```

### dataqa：安全与可见性

SQL 网关（`businesses/dataqa/guard.py`）强制：单条语句、`SELECT/WITH` 开头、
屏蔽写操作与系统库、自动补 `LIMIT`（`DATAQA_MAX_ROWS`，缺省 500）；
连接额外叠加 `SET SESSION TRANSACTION READ ONLY` 与 `MAX_EXECUTION_TIME` 兜底。
表级可见性用 `DATAQA_ALLOWED_TABLES` / `DATAQA_DENY_TABLES` 控制，
默认屏蔽 `mcp_api_keys` 与下划线开头的内部表（建议只放开
`v_xbond_depth,fmut_mkt_bonddpanyhis`）。当前阶段不做行级权限隔离，
但每次查询都会记录调用者 username。

### hbaseqa：外汇行情（HBase Thrift 只读 scan）

HBase 里四张行情宽表按 `HSDC_HDS2_{渠道}_{产品类型}_{数据类型}` 命名，
全部数据落在列族 `CF`，rowkey 是 **Bar 起始毫秒时间戳**：

| 表 | 渠道 | 产品类型 | 数据类型 |
| --- | --- | --- | --- |
| `HSDC_HDS2_UBS_FXSPOT_BAR_DEPTH` | UBS | FXSPOT | BAR_DEPTH |
| `HSDC_HDS2_JPMC_FXSPOT_BAR_DEPTH` | JPMC | FXSPOT | BAR_DEPTH |
| `HSDC_HDS2_UBS_FXFWD_BAR_DEPTH` | UBS | FXFWD | BAR_DEPTH |
| `HSDC_HDS2_BEST_HO_FXSPOT_BAR_BEST` | BEST_HO | FXSPOT | BAR_BEST |

渠道名可以自带下划线（`BEST_HO`），命名段数与数据类型是复合词（`BAR_BEST`），
所以维度反解是**以产品类型为锚点**切分——锚点左侧全归渠道、右侧全归数据类型，
不依赖固定位次，也不需要维护完整的数据类型词表。工具分工：

- `describe_fx_schema`：口径字典（表命名规则、字段中文语义、时间与 rowkey 的坑）
- `list_contracts`：发现时间窗内的合约代码（库里存的是带后缀的代码，如 `EURUSDSP`）
- `get_fx_bars`：主力工具，OHLCV K 线，支持 `frequency` / `buysell` / `order` 过滤
- `list_tables` / `describe_table` / `sample_rows`：通用探索兜底

字段口径：`FREQUENCY` 的 `1N` 表示 1 分钟（`1m` / `1min` / `M1` 会被归一化成 `1N`）；
`BUYSELL` 取值为 `BID` / `ASK` / `MID`，同一时刻三个方向可能各有一根 K 线，
只取中间价要显式传 `buysell='MID'`；取值写错会直接报"只能是 BID/ASK/MID"，
不会静默返回空结果。

**没有通用查询入口**：所有访问都是参数化 scan，不允许外部拼任何条件表达式。

限流与取数策略：

- **rowkey 范围优先**：rowkey 是等宽毫秒字符串，字典序 == 数值序，可直接当 scan
  边界（`row_stop` 开区间，故上界 +1），这是最省服务端资源的方式
- **兜底校验**：取回的每一行再用 `CF:TIME` 卡一遍区间，越界的丢弃并计入
  `skipped_out_of_range`——防止 rowkey 形态与预期不符时静默给错数据
- **行数上限**：`TScan.limit` 由服务端执行（取 `cap + 1` 是为了知道"后面还有没有"），
  返回行数再由 `HBASEQA_MAX_ROWS` 裁剪
- **正向 vs 反向**：`order='desc'` 时用 `TScan.reversed` 让**服务端**反向扫描。
  注意正向扫描时 `limit` 保住的是**最早**的 N 条，所以"看最新行情"务必显式传 `order='desc'`

### 取数粒度：为什么默认不给明细

一个月 × 3 合约 × 1 分钟 ≈ **13 万行**。塞进模型上下文既慢又没用（模型只看前几十行
就开始总结），所以默认降采样：

| granularity | 含义 | 一个月的数据量 |
| --- | --- | --- |
| `auto`（缺省） | 窗口 > 48h 自动降为 `daily`，**并在 notes 里说明** | 90 行 |
| `daily` / `hourly` | 每合约每（天/小时）最后一根 | 90 / 2160 行 |
| `latest` | 每合约最新一根 | 3 行 |
| `raw` | 明细，受 `MAX_ROWS` 约束 | 500 行（截断） |

两个实现要点：

1. **`raw` 也会把行数上限下推到服务端**（`TScan.limit`）。否则 HBase 会读满整个
   时间窗，而返回给模型的只有 500 行——纯浪费。这是"取数又慢又大"的主因
2. **降采样不能"读完再筛"**：那会被 `MAX_SCANNED_ROWS` 截断，导致日线**静默少一半**。
   实际做法是**按桶反向探查**——反向扫桶尾拿各合约最后一根，集齐即停；
   合约未指定时先用一个中等规模探测建立合约集合

实测（stub，1 天 × 3 合约 × 1 分钟 = 4320 行。绝对耗时含纯 Python stub 的编解码开销，
**看"实际读取"这一列**）：

| 场景 | 返回行数 | 实际读取 | 耗时 |
| --- | --- | --- | --- |
| `raw` | 500 | 501 | 1.1 s |
| `latest` | 3 | 512 | 1.1 s |
| `hourly` | 75 | 888 | 2.1 s |
| `daily` | 6 | 544 | 1.3 s |
| `daily`（指定合约） | 2 | **64** | 0.2 s |

复现：`uv run python tests/_bench_granularity.py [--days N] [--naive]`

> 语义提醒：降采样后 `high`/`low` 只是**采样点**的区间，不是真实极值（日线会丢掉
> 日内极值）。响应里用 `summary_scope: "downsampled"` 标注，并在 `notes` 里说明；
> 要精确极值请用 `granularity='raw'` 并缩小时间窗。
>
> 合约在 rowkey 中段，无法下推到服务端过滤，所以"按合约过滤 + 触到扫描上限"时
> 会在 `notes` 里明确说明结果不完整，不会静默少给。

```bash
# 最小配置（Thrift2 直连，不需要 ZooKeeper 端口）
HBASEQA_THRIFT_HOST=<thrift-server>
HBASEQA_THRIFT_PORT=9090
uv sync                       # 安装 thriftpy2
```

服务端启动（**必须是 Thrift2**）：

```bash
hbase thrift2 start                 # 默认 9090、buffered 传输
```

> rowkey 若不是纯毫秒时间戳，把 `HBASEQA_ROWKEY_LAYOUT` 改成 `unknown`，
> 服务端会自动改用 `CF:TIME` 列过滤。先用 `sample_rows` 看一眼真实 rowkey 再定。

### 为什么是 Thrift2（而不是 happybase）

HBase 有**两个互不兼容**的 Thrift 服务，方法名完全不同：

| Thrift1（`Hbase`，happybase 用的） | Thrift2（`THBaseService`，本业务用的） |
| --- | --- |
| `getTableNames()` | `getTableNamesByPattern(regex, includeSysTables)` |
| `scannerOpenWithScan(table, tscan)` | `openScanner(table, tscan)` |
| `scannerGetList(id, nbRows)` | `getScannerRows(id, numRows)` |
| `scannerClose(id)` | `closeScanner(id)` |
| — | `TScan.reversed` / `TScan.limit` |

混用的后果是一句毫无提示性的 `TApplicationException: Invalid method name: 'getTableNames'`
（`thrift2` 服务端收不到 `getTableNames` 这个名字；反向也一样）。
工具会按方法名反推该用哪个服务，把中文提示一起返回给模型
（见 `client.thrift_version_hint`）。

实现上**不用 happybase**，而是用 thriftpy2 在运行时加载一份最小 IDL：

```
businesses/hbaseqa/idl/hbase_thrift2.thrift     # 字段 ID 逐字取自 HBase 上游
```

只声明用到的结构体与方法（约 130 行），好处是不需要 thrift 编译器、也不用把
几千行生成代码 vendor 进仓库。HBase 两侧的兼容规则保证只声明子集是安全的：
请求按**方法名 + 字段 ID**读参数，响应里多出来的字段被自动跳过——所以像
`TScan.readType` / `TScan.consistency` 这类枚举字段刻意不声明，避免取值与服务端
版本不一致时解码失败。

> 日志里出现 `Invalid method name` 就是连错了 Thrift 版本；出现
> `TIOError ... TableNotFoundException` 则是表名/命名空间不对（注意表名可能带
> `ns:` 前缀），跟协议无关。

#### 排查：`WinError 10053 / 10054`

Windows 上的 10053（本机中止）/ 10054（对端重置）都表示 **Thrift 通道断了**，
与查询语句无关。两类原因：

1. **连接被服务端回收**：进程内复用长连接单例，空闲一段时间的第一次调用会打在
   已死的 socket 上。`client.run_with_retry()` 会自动重置连接并重试一次，现在能自愈
2. **端口 / 传输 / 协议不匹配**：`HBASEQA_THRIFT_PORT` 必须指向 Thrift Server
   （**不是** ZooKeeper 2181 或 HMaster）；`HBASEQA_THRIFT_TRANSPORT` 必须与服务端一致，
   拿不准就把 `buffered` 换成 `framed` 再试

绕开 MCP 直接验证连通性（报错文案已内置排查提示）：

```bash
cd mcp-server
uv run python -c "from easy_mcp_server.businesses.hbaseqa import client; print(client.table_names())"
```

连接类错误一律在工具层被转成 `{"ok": false, "error": ...}`，不会以异常形式冒到
MCP 客户端（否则模型只会看到一句 `Error executing tool xxx`）。

#### 排查：`__str__ returned non-string (type bytes)`

thriftpy2 的 `TApplicationException` / `TProtocolException` 把 `__str__` 实现成
"直接返回 message"，而 message 可能是 bytes。于是**任何对这类异常的 f-string 插值
（包括日志）都会抛 `TypeError`，把真实错误彻底盖掉** —— 用户只看到一句
`Error executing tool xxx: __str__ returned non-string (type bytes)`，完全不知道
服务端说了什么。

所有异常文本统一走 `guard.exception_text()`（`__str__` → `.message` → `.args`
逐级降级，永不抛异常），工具现在会把服务端原话原样返回，例如：

```json
{"ok": false, "error": "TApplicationException: org.apache.hadoop.hbase.security.AccessDeniedException: denied"}
```

拿到这类消息说明 **Thrift 已经连通、是服务端拒绝了这次调用**，按 message 里的
Java 类名排查即可；若 message 是协议类异常（`TProtocolException`），
优先怀疑 `HBASEQA_THRIFT_TRANSPORT` / `HBASEQA_THRIFT_PROTOCOL` 与服务端不匹配。

> 已知脆弱点：鉴权中间件用的是 `BaseHTTPMiddleware`，它包着一个**流式响应**的 MCP 应用。
> 客户端在响应过程中中断连接时，Starlette 会抛 `RuntimeError("No response returned.")`
> 并把该次请求记为 500（服务端日志可见，工具本身无异常）。当前不影响功能，
> 但若后续遇到"偶发 500"，先从这条链路查。

#### Kerberos

当前集群未启用。配置口径已预留（`HBASEQA_KERBEROS_*`），启用时开关一打开会
**显式报错而不是静默降级**（fail closed）。接入点只有一处：
`businesses/hbaseqa/client.py::_open_connection`，把 Thrift 传输换成
SASL 传输（`thrift_sasl.TSaslClientTransport` + keytab 登录）即可，上层无需改动。

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
