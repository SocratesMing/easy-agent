# Token 用量统计实施验收记录

日期：2026-11-10。依据：[设计](design/2026-10-30-Token用量统计设计.md)。接口与运维规则：[Token 用量统计](token-usage-api.md)。

## 实现范围

- Schema 17 核心账本，逐模型调用幂等写入，按上海观测完成日期入账；会话归档/删除和服务重启不删除累计。
- 原始 usage 的完整性校验，非流式、流式尾块及 SDK 统一采集。只使用显式的合法输入/输出计数；重复累计快照不叠加，思考/缓存不额外相加。
- 真实用户 scope 覆盖预检、聊天/HITL、自动/手动摘要、专家生成、知识问答、补全、提示词增强、定时任务及记忆线程。有效模型配置键进入编译图缓存键，用户归属不写进共享模型。
- 鉴权 `GET /agent/usage?days=7|30`，同连接显式一致性事务内返回固定四卡及补零趋势，错误返回 401/422/503，禁止缓存。
- 双前端设置入口、原生 SVG、图例显隐、精确提示与表格、加载/空态/失败重试、快速切换与换账号隔离。Vue2 沿用受管 runtime URL/鉴权及 Options API。

## 专项验证

| 验证 | 实际结果 |
| --- | --- |
| 最终采集/图/DB/API专项（设计要求的三个文件及真实图契约） | 57 passed，1 skipped |
| 采集、真实 DeepAgents、编译图缓存、数据库/API、迁移/表清单、静态架构门禁联合回归 | 125 passed，1 skipped |
| 领域判定、scope、手动压缩、鉴权增强及增强服务联合回归 | 88 passed |
| 修复重复压缩请求竞态后的压缩/上下文API/scope/采集/真实图回归 | 61 passed |
| Vue2 unit | 370 passed |
| Vue2 lint:vue2 / check:easy-agent | 通过 |
| 双前端 build | 均通过；仅既有产物体积提示 |
| Vue3 unit（Node 20.19，直接执行 npm run test:unit） | 181 passed，0 failed |
| 后端完整回归（排除真实供应商文件，另行验证） | 2847 passed，17 skipped，0 failed |
| 真实 MySQL 8.4.10 账本专项 | 15 passed，0 skipped |
| 真实供应商流式/工具/状态快照及用量入账 | 原有3项、新增2项均通过 |
| Ruff、导入 SCC/分层、双写冻结、async 阻塞门禁、共享源码同步、diff 检查 | 通过 |

固定时刻 `2026-10-30T04:00:00.000Z` 下，Alice 四卡为 **1200 / 7000 / 17300 / 32000**，Bob 均为 **100000**。7/30日点数、逐模型日合计、零点包含/as_of排除和用户隔离符合设计。

使用真实 OpenAI SDK、LangChain callback 分派和 DeepAgents 图，供应商 HTTP 响应由 MockTransport 固定返回：工具审批恢复只增加后续真实调用，子代理按各次调用记账，共享图并发用户不串账；流在完整尾块之前关闭不记账，取得完整证据后关闭只记一条。SSE 和 checkpoint 不包含内部采集字段。

鉴权增强 API 验证两个真实用户、SDK 与 LangChain 共用账本、JSON 校验失败仍保留消耗、数据库等待不进入业务 timeout。手动压缩验证二次摘要各自入账、提交失败不回滚消耗、失败重试实际新调用新增记录、已提交 request_id 不再调用模型。排队记忆线程使用各自提交时的身份快照。

SQLite 验证原有库升级、幂等写入、失败读事务回滚、重启和会话生命周期，以及两次 SELECT 间由另一连接提交数据时响应仍保持同一快照。

## 浏览器验证

使用 Playwright 和隔离 fixture 页面挂载真实 Vue2/Vue3 组件、模板、样式及 API 传输代码，不使用真实业务账号或生产服务。Vue2 以嵌入模式配置 `/host-gateway`、自定义凭据存储键和宿主请求头，Vue3 使用原有 `authFetch`。

两套页面均验证：固定四卡、7/30日点数、键盘聚焦/Enter精确提示、逐日表格、图例显隐、切换 Bob 和退出后清除旧数据、503错误重试、全零空态、30日旧响应晚于7日新响应时不覆盖结果。375px窄屏深色主题两列卡片、页面无横向溢出；Vue2额外验证桌面尺寸。样式与组件单元测试也执行真实 DOM 编译/挂载，无组件渲染警告。

另外用两套真实构建产物、独立浏览器会话和本地假凭据/API，验证“应用用户菜单 → 设置 → Token统计”。Vue2 确认运行于 standalone 模式。切入页签后两端均只发起 `/agent/usage?days=7`，没有误请求记忆/系统提示词；选中状态、tabpanel 标签关联及四卡固定值均正确。Vue3 实际导航中从提示词页签按 ArrowRight 也正确切换和聚焦 Token统计，只触发统计请求。该检查与 Vue2 嵌入模式的组件传输验证共同覆盖两个运行入口。

本地浏览器产物在忽略目录 `output/playwright/token-usage/`，包含 fixture 构建脚本和 Vue2/Vue3 窄屏截图。浏览器测试均隔离使用本地假账号和固定数据，未将生产账号登录或真实模型消费计入验收。

## 失败修复与最终回归

首次全量运行发现新表清单断言、分类模型测试 fixture 必填配置以及方法名引起的静态门禁误报，均已修正并专项复验。账本方法采用 `record_usage`，避免与纯内存 metrics 的 `record` 同名导致静态闭包误判。

前次全量复测命令 `uv run pytest tests/ -q --ignore=tests/integration/test_v3_streaming.py` 得到 **2839 passed、5 failed、17 skipped**。其中4项为下述原基线失败，另1项是新增身份查询导致的重复压缩请求竞态，已修复：先登记共享后台任务，再在任务内解析身份并传入实际执行体，避免首个等待者断开时重复启动压缩。原失败用例、新增“身份查询尚未完成时重复请求仍共享同一任务”的确定性用例及相关回归 **61项全部通过**。

原始 HEAD 导出到独立临时目录，执行沙箱与文档测试得到 **4 failed、20 passed、6 skipped**，确认以下既有失败；本轮已逐项修复：

- `tests/integration/test_sandbox.py` 的三个启动报告用例模拟 Linux/bwrap 却未模拟平台。测试现在显式模拟 Linux，并新增 darwin / win32 不探测 bwrap、保持 fail-closed 的回归；保留沙箱实现和原断言。
- `tests/test_docs_facts.py::test_config_example_documents_knowledge_runtime_keys` 发现模板分页大小为0，与 `LimitsConfig.max_documents_per_query` 的 `gt=0` 校验冲突。模板恢复为500；独立的知识库文档总量上限 `max_documents_per_base=0` 继续表示不限。

Vue3 原基线 `Chat.vue` 1018行超过 structure 测试预算986。将其原有两块 scoped 样式按原顺序移入 `features/chat/styles/chat.css`，组件缩至443行（按测试的换行统计口径），预算未放宽。用 Vue 编译器确认模板、脚本和编译后的 scoped CSS 保持一致，生产构建通过。新增 `scripts/run-unit-tests.mjs` 显式收集测试文件，解决 Node 20.19 不支持带引号 glob 的入口问题；零测试文件时直接失败。`npm --prefix frontend run test:unit -- --test-reporter=spec` 收集30个文件，**181 passed、0 failed**。

修复后重新执行完整命令 `uv run pytest tests/ -q --ignore=tests/integration/test_v3_streaming.py`，结果为 **2847 passed、17 skipped、0 failed**，耗时508.85秒。该轮采集后新增的真实供应商记账文件另行执行，默认未配置时2项显式跳过，不计入上述完整回归数量。沙箱、文档和配置定向回归为 **57 passed、6 skipped**；Ruff 与 `git diff --check` 通过。

## 真实 MySQL 与供应商联调

此前供应商连接失败、MySQL 未配置的限制，本轮已在隔离测试范围内补验：

- 使用本机 MySQL **8.4.10**，在临时数据目录和随机 loopback 端口启动独立实例，通过 `EASY_TEST_MYSQL_HOST/PORT/USER/PASSWORD` 执行 `uv run pytest tests/db/test_token_usage.py -q`，得到 **15 passed、0 skipped**。真实临时库覆盖DDL、幂等写入、7/30日聚合、大小写敏感身份及连接默认 READ COMMITTED 下的跨查询一致性快照；测试库自动删除，临时实例已关闭。该结果验证本机 MySQL 8.4，不代表生产库已验收。
- 使用现有本地模型配置连接真实 DeepSeek，执行 `uv run pytest tests/integration/test_v3_streaming.py -q --tb=short`，得到 **3 passed**，覆盖基础流式、工具调用和状态快照，之前的 APIConnectionError 本轮未重现。
- 新增 `tests/integration/test_token_usage_provider.py`，显式设置 `EASY_TEST_USAGE_PROVIDER_CONFIG=easy_agent/config/config.yaml` 后执行，得到 **2 passed**。真实非流式和流式尾块各写一条账目；输入/输出计数与返回 usage 一致，用户、会话和配置模型身份准确。全部使用 pytest 临时 SQLite 账本及假身份，未写入业务用户账本；该检查会产生少量真实供应商消费。默认未配置时显式跳过。

以上不等于所有供应商及生产环境联调完成；其他模型供应商、生产 MySQL 和实际业务账号仍需按部署环境验收。固定 HTTP 响应契约测试继续覆盖漏报、取消、重复尾块及并发等边界。

本轮日志：`/tmp/easy-token-usage-pytest-final.log`、`/tmp/easy-token-usage-vue3-unit.log`、`/tmp/easy-token-usage-vue3-build.log`、`/tmp/easy-token-provider-all.log`、`/tmp/easy-token-usage-provider-ledger.log`。
