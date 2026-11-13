# Token 用量统计

设置中的“Token统计”显示当前登录用户已记录的真实模型用量。统计按 `Asia/Shanghai` 自然日计算，仅包含功能启用后采集到的调用，不扫描旧聊天消息，不估算供应商漏报，也不折算金额。

聊天中的 Token / 上下文展示与个人账本采用不同口径：前者是最近一次模型调用的快照，后者按调用记录累计消耗。一轮发生多次调用时，两者不要求相等。金融限制、Token 和错误提示的当前行为及回归入口见 [聊天行为契约](chat-behavior-contract.md)。

## 查询接口

`GET /agent/usage?days=7`，使用现有 Bearer JWT 鉴权。`days` 默认 7，只支持 7、30，仅控制趋势长度，四卡的统计窗口始终固定。客户端不能指定统计用户。

```json
{
  "timezone": "Asia/Shanghai",
  "as_of": "2026-10-30T04:00:00.000Z",
  "days": 7,
  "summary": {
    "today": 1200,
    "last_7_days": 7000,
    "last_30_days": 17300,
    "all_time": 32000
  },
  "daily": {
    "dates": ["2026-10-24", "2026-10-25", "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29", "2026-10-30"],
    "totals": [3500, 0, 0, 0, 0, 2300, 1200],
    "series": [
      {"model_key": "default", "model_name": "deepseek-flash", "values": [3500, 0, 0, 0, 0, 0, 1200]},
      {"model_key": "secondary", "model_name": "model-b", "values": [0, 0, 0, 0, 0, 2300, 0]}
    ]
  }
}
```

- 今天：上海今天零点至 `as_of`；七天、一个月分别含今天在内的 7、30 个自然日；总共为启用以来、`as_of` 之前的全部记录。
- 查询区间左闭右开，总量为 `input_tokens + output_tokens`。思考、缓存计数不额外相加。
- 趋势补齐所有日期，模型按 `(model_key, model_name)` 区分，大小写敏感；按窗口总量降序、键与名称升序返回。无正用量模型不产生曲线，空库仍返回四卡 0 和完整日期数组。
- 响应使用 `Cache-Control: no-store`。无有效登录/用户记录返回 401，非法 `days` 返回 422，鉴权或统计查询的 SQLite/PyMySQL OperationalError、InterfaceError 及连接池耗尽返回 503：`Token统计暂不可用，请重试`；编程错误与输出校验失败返回通用 500，服务端保留完整 traceback。200/401/422/503/500 均由统计路由局部策略设置 `no-store`。
- summary 和 daily 使用同连接显式读事务。SQLite 使用 `BEGIN`；MySQL 对当前事务设置 `REPEATABLE READ` 并建立只读一致性快照，不依赖服务器默认隔离级别。MySQL 池连接由 `Database.get_connection()` 统一提交/回滚并归还；SQLite 保留共享连接。

## 采集与存储

所有 OpenAI 兼容 LangChain 模型由工厂安装一次 `TokenUsageCallback`。只有供应商显式上报合法的非负整数 `prompt_tokens` 和 `completion_tokens` 才采集，完整的 0 有效，缺失、null、bool 和负数均跳过。不能用 LangChain 补零后的 `usage_metadata` 修补原始数据。

非流式在适配层创建结果之前提取完整 usage；流式使用完整尾块或完整 finish 块的最终快照，重复累计快照取最后一份，不相加。在 `generation_info.easy_agent_usage` 中以无 index 的列表传递计数和观测完成时间，结束时移除最终消息中的内部字段。`lc:{run_id}` 是模型调用 ID，一轮聊天可以产生多条账目。SDK 增强使用独立的 `sdk:{uuid}` 事件，在结果校验前捕获用量，在业务 timeout 之外确认写库。

真实用户 ID 由服务端用户查询或知识库鉴权主体产生。专家预检、后台聊天/HITL、摘要、专家生成、知识问答、公共补全、增强、定时任务及记忆更新均在各自实际执行处建立身份 scope。普通聊天在准备阶段只解析一次 `UsageIdentity`，经知识适配层传入实际流；普通聊天金融领域分类已撤回，不再产生分类调用。显式 `None` 禁用采集，省略参数的独立流入口仍自行解析。chat/resume 在消费期建立 scope，并在关闭内部生成器后复位。线程队列捕获自身的不可变快照，禁止继承上一个任务的用户。启动探测和无用户维护操作不记个人用量。

`token_usage` 核心表随 schema 17 自动创建，按事件 ID 幂等写入，无会话删除级联；重启、归档或删除聊天均保留累计。写入失败只记录事件/模型及异常类型，保留原业务结果。供应商漏报、进程中断、无法取得最终证据的取消或写库失败可能产生缺口，不承诺与供应商账单相同。

适配契约对应锁定的 langchain-core 1.6.12、langchain-openai 1.2.2、OpenAI SDK 2.41.0；升级必须重跑 `tests/agent/test_token_usage.py` 和图调用契约测试。验证模拟供应商响应，不需要真实 API 密钥。

## 验证入口

```bash
uv run pytest tests/agent/test_token_usage.py tests/agent/test_token_usage_graph.py tests/db/test_token_usage.py tests/api/test_usage.py tests/services/test_usage_scopes.py -q
node scripts/dev/sync-frontend-shared.mjs
npm --prefix frontend-vue2 run test:unit
npm --prefix frontend-vue2 run lint:vue2
npm --prefix frontend-vue2 run check:easy-agent
npm --prefix frontend-vue2 run build
npm --prefix frontend run test:unit
npm --prefix frontend run build
```

真实 MySQL 可选验证使用独立临时数据库：设置 `EASY_TEST_MYSQL_HOST`，以及测试所需的 `EASY_TEST_MYSQL_PORT`、`EASY_TEST_MYSQL_USER`、`EASY_TEST_MYSQL_PASSWORD`。测试账户须有创建/删除临时库权限；未配置时对应测试显式跳过。

真实供应商记账验证需显式设置 `EASY_TEST_USAGE_PROVIDER_CONFIG` 为本地模型配置路径，再执行 `uv run pytest tests/integration/test_token_usage_provider.py -q`。它会发送两次短请求、产生少量真实消费，使用 pytest 临时账本记录假身份，不写入业务账本；未配置时显式跳过。
