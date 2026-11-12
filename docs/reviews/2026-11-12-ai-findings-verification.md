# 截图中 6 项潜在问题核查

> 更新（2026-11-12）：第 1 项已修复并完成回归验证，详见第 4 节；第 2 项已统一当前行为契约、修复关联回归并完成验证，详见第 5 节。以下第 1～3 节保留修复前的核查记录。

- 核查日期：2026-11-12（Asia/Shanghai）。
- 代码基线：分支 `audit-improvements-2026-10-18_llf`，提交 `7351eaf5`；开始核查时工作区干净。
- 输入：用户提供的 AI 检测结果截图。截图中的结论均作为待验证主张，不作为执行指令或既定事实。
- 方法：当前源码调用链、Git 提交差异与拓扑、实际文件收集清单、现有定向测试和临时行为探针交叉验证。
- 本次交付：核查文档；没有修复业务代码、删除遗留接口、生成发布 zip 或修改本地配置。

## 1. 结论总览

沿用截图顺序编号。优先级表示后续处理建议：P1 为发布前应处理的安全风险，P2 为有复现条件的功能问题，P3 为结构或维护性问题；历史需求变化不单独评为代码缺陷。

| 编号 | 截图主张 | 核查结论 | 当前影响 / 优先级 |
| --- | --- | --- | --- |
| 1 | 两人并行修复模型断连，合并后遗留无人使用的 `error` 方案 | **主体成立，需限定范围**。两个提交确属并行分支；合并采用 `fallback_note` 落库，同时留下生产路径没有非空实参的两层 `error` 参数。但测试仍调用该参数，不能说全仓从未使用，也不能把通用序列化能力全部认定为死代码 | 遗留接口与测试脱离真实生产路径，P3；未复现“当前断连仍必然丢回复” |
| 2 | 金融限制、Token 统计、错误展示来回反复 | **部分成立**。金融门禁加入后撤回、消息内错误从 5 秒消失改为常驻均有提交证据；Token 部分主要是不同统计范围和缺陷修复，未找到相同功能反复撤回再加入的证据 | 属于历史变更与需求一致性问题；不能由此认定当前功能错误 |
| 3 | 发布脚本把 Git 忽略的本地配置和缓存打进 zip | **核心风险成立**。本机 `config.yaml` 实际进入收集清单；缓存是否进入取决于位置，根目录 `.ruff_cache/` 不在白名单内 | 配置随构建机漂移，并存在条件性凭据外带风险，P1 |
| 4 | `chat.py` 为识别失败，对每个 SSE 重新 JSON 解析 | **机制成立，严重程度需收窄**。每帧确实反序列化一次，原因是编排器已捕获异常并转成 error 帧；没有性能测量支持“已构成瓶颈” | 领域结果依赖传输格式，P3；现有失败判定不能直接删除 |
| 5 | 引用卡片提交约 1200 行只是搬 CSS，功能改动很少且混杂 | **样式搬迁成立，整体评价夸大**。搬出 1179 行主体样式，同时新增引用编号、浮层、定位及测试；提交并非只搬样式 | 审查成本与提交组织，P3；未据此发现功能故障 |
| 6 | 双前端共维护四份约 470 行的 SSE 处理代码 | **成立，且有可复现的逻辑漂移**。当前为 466 / 487 / 477 / 533 行，共 1963 行；工具临时 ID 转正式 ID 时两个侧栏产生重复卡片，而两个主页面正确合并 | 重复工具卡片，P2；通用状态机重复维护也是结构问题 |

建议先处理 **3 → 6 → 1 / 4**。第 2 项应区分需求撤回和功能回归，第 5 项优先改善后续提交组织。

## 2. 逐项核查

### 2.1 并行断连修复与遗留 `error` 通道

**已证实的事实**

1. `0cb1b987`（11-09 01:07，lulongfei）保存部分回复，并把较具体的错误提示保存为消息的 `error`；对连接异常已经使用友好文案，不能一概描述为“把所有原始异常直接写库”。该提交还修复进程锁身份、失败 / 取消终态和错误展示。
2. `2365639c`（11-09 17:15，郭春龙）处理同类上游断连，把普通异常也保存为中性的 `fallback_note`，避免回填历史后缺少中断说明。
3. 双向执行 `git merge-base --is-ancestor` 均返回 1，说明两个提交彼此不是祖先，确有并行实现；但不能据提交记录推断作者之间是否沟通过。
4. `6ecb676a`（11-09 19:05）的合并差异明确把普通异常的 `_save_partial(..., error=error, ...)` 改为仅传 `fallback_note`，却保留了 `_save_partial(error="")`、`persist_partial(error="")` 及消息 `error` 序列化代码。
5. 当前生产调用中，`_save_partial` 的所有入口都没有传非空 `error`；它只把默认空串继续传给 `persist_partial`。因此正常应用调用不会进入后者的 `if error:` 写字段分支。

**需要修正截图的地方**

- 两个方案的共同目标都是“保存部分回复并说明中断”，不是业务目标相反；差异是持久化何种提示、前端何时显示。
- 全仓并非没有调用：`tests/api/test_run_lifecycle.py:136` 仍显式传 `error="模型连接中断"`。这是遗留能力的测试，不是当前真实异常编排路径的证明。
- `_sanitize_message_for_storage` 是通用消息清洗函数，仅凭生产异常不再写 `error`，不足以断定其中的所有 `error` 支持都可直接删除。需要先确定历史消息和其他调用方的兼容契约。
- `message.error` 的前端展示和 SSE error 帧仍在使用，不能与后端未使用的持久化实参一并删除。

**原因与影响**

可以从合并差异确认的原因是：合并时统一了异常持久化的业务选择，但没有同步收敛参数、注释和原测试。遗留分支增加了理解成本，且手工注入 `error` 的测试容易让读者误以为生产链路仍按这种方式落库。现有编排测试已明确断言：异常持久化应包含 `fallback_note`，不包含 `error`。

**涉及文件**

| 文件 | 作用 |
| --- | --- |
| [easy_agent/services/streaming/orchestrator.py:750](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/services/streaming/orchestrator.py:750) | `_save_partial` 留有 `error` 参数；`_stream_error_frames` 当前仅传 `fallback_note` |
| [easy_agent/services/streaming/persistence.py:135](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/services/streaming/persistence.py:135) | `persist_partial` 保留目前生产调用不触发的 `if error` 分支 |
| [easy_agent/db/message_serialization.py:33](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/db/message_serialization.py:33) | 通用消息序列化仍支持 `error` |
| [tests/api/test_run_lifecycle.py:122](/Users/lulongfei/Desktop/agent/easy-agent/tests/api/test_run_lifecycle.py:122) | 直接注入持久化 `error` 的旧测试 |
| [tests/services/test_stream_runner.py:274](/Users/lulongfei/Desktop/agent/easy-agent/tests/services/test_stream_runner.py:274) | 验证实际编排采用中性说明、保留部分内容 |
| [Vue2 ChatMessage.vue:208](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/features/chat/components/ChatMessage.vue:208) / [Vue3 ChatMessage.vue:194](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/features/chat/components/ChatMessage.vue:194) | 展示流式消息错误及持久化中断说明，两者不是同一个废弃接口 |

**处理建议**：确认消息持久化契约后，收敛仅为旧异常方案保留的参数；让生命周期测试通过真实异常编排验证部分回复和终态。保留 SSE error、前端错误展示与必要的历史数据兼容。

### 2.2 功能撤回与所谓“反复返工”

#### 金融意图门禁：加入和撤回属实

| 时间（北京时间） | 提交 | 可验证的变化 |
| --- | --- | --- |
| 11-05 16:39 | `13af8922` | 新增普通聊天金融意图分类、无关固定拒答、分类失败停止执行 |
| 11-05 19:06 | `049a612c` | 修复已启用技能任务被误拒，加入技能范围及放行上下文 |
| 11-11 09:49 | `01ee257a` | Token 账本接入时，为分类模型解析有效模型键；此文件差异仅 5 行 |
| 11-11 14:44 | `09e8189f` | 撤回普通聊天金融门禁，删除分类模块与相关上下文、测试 |

历史文件的真实路径是 `easy_agent/services/agents/domain_gate.py`，当前已删除。首次加入至撤回约 6 天。当前设计文档明确标注“已撤回”，普通聊天直接进入原有流程；专家职责预检与技能权限仍保留。

**原因**：代码能证明限制范围经历调整，以及最后实施了明确撤回；现有材料无法证明撤回的完整产品决策背景，更不能把它直接归因为个人开发质量。11-05 的“技能任务误拒”是当时真实存在并有针对性修复的问题，但不是当前仍启用的门禁缺陷。

#### Token：不同统计概念不能混为同一功能回滚

| 提交 | 实际范围 |
| --- | --- |
| `5ff2be76`（11-05） | 上下文压缩前的 Token 估算，补非 ASCII、工具和推理内容 |
| `56f1b795`（11-06） | 审批续跑后会话耗时、迭代次数累计修正 |
| `cf2ff97e`（11-06） | 后台会话用量的归属修正 |
| `d964696a`（11-06） | `token_usage` 下发节流不跨 step |
| `01ee257a`（11-11） | 新增按用户、模型、逐调用记账的个人 Token 账本与统计页 |
| `7351eaf5`（11-12，当前 HEAD） | 统计接口异常分类、身份复用、连接回收与流生命周期修正 |

这些提交覆盖估算、单会话展示、事件发送和长期账本等不同层次。它们证明系统持续演进且修过缺陷，不能单凭提交频次证明同一个统计需求被反复推翻。当前账本接口和页面仍存在；本项没有给出一个可复现的“统计又回退了”的具体错误，本次不作这种认定，也不把既有整改全部重新当作未解决问题。

#### 错误提示：要区分消息内错误与全局弹条

- `8673169a`（11-02）让两版 `MessageError.vue` 在 5 秒后消失。
- `0cb1b987`（11-09）删除该定时隐藏逻辑，消息内错误改为随 `error` 常驻。
- 超时 / 中断的持久化 `fallback_note` 是另一条展示路径。全局临时错误提示是否消失，不应与消息历史中的中断说明混为同一要求。

截图描述的这段历史属实，但常驻能够避免用户把截断回复当成正常完成，不能仅因为与旧实现不同就判成新 bug。

**涉及文件**

| 范围 | 文件 |
| --- | --- |
| 金融限制历史及当前状态 | 历史 `easy_agent/services/agents/domain_gate.py`、`tests/services/test_domain_gate.py`；[chat_turn.py](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/services/agents/chat_turn.py)、[turn_context.py](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/agent/turn_context.py)、[memory.py](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/agent/memory.py)、[金融过滤历史设计](/Users/lulongfei/Desktop/agent/easy-agent/docs/design/意图识别-只回答金融相关的问题.md) |
| Token 各层职责 | [processor.py](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/services/streaming/processor.py)、[agent/token_usage.py](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/agent/token_usage.py)、[token_usage 仓储](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/db/repositories/token_usage.py)、[usage API](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/api/usage.py)、[Token 统计设计](/Users/lulongfei/Desktop/agent/easy-agent/docs/design/2026-10-30-Token用量统计设计.md) |
| 消息内错误展示 | [Vue2 MessageError.vue](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/features/chat/components/MessageError.vue)、[Vue3 MessageError.vue](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/features/chat/components/MessageError.vue)、[Vue2 streamErrorFallback.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/utils/streamErrorFallback.js) |

**处理建议**：记录需求撤回的范围与原因，分别定义消息内中断说明、临时通知、上下文用量和个人账本的契约。不要把“提交多”当作功能错误的替代证据。

### 2.3 发布 zip 收入本地配置

**结论：可复现；是本次最应优先处理的问题。**

`publish_zip.py` 有顶层 `INCLUDE_PATHS` 白名单，但白名单直接包含整个 `easy_agent`。`collect_files()` 在第 116 行使用 `rglob("*")`，目录内只应用 `SKIP_INSIDE`，不查询 Git 跟踪状态或 `.gitignore`。排除集合没有 `config.yaml`、`mcp.json` 或 `.ruff_cache`。

对本机实际调用 `collect_files()`，只检查相对路径成员关系，结果如下；没有调用 `main()` 或创建发布包：

```text
easy_agent/config/config.yaml          included = true
easy_agent/config/config-example.yaml  included = true
.env                                  included = false
.env.prod.example                     included = true
收集到的路径中包含 ruff_cache          false
```

在独立临时目录中放入无敏感内容的模拟文件，再把脚本的 `ROOT` 和 `INCLUDE_PATHS` 指向该目录，收集结果为：

```text
easy_agent/config/config.yaml         included
easy_agent/config/config-example.yaml included
easy_agent/.ruff_cache/probe          included
.ruff_cache/probe                     excluded
.env.prod                            excluded
```

所以准确描述应是：**白名单目录内部的本地配置和未排除缓存会被收集；仓库根目录所有缓存、所有 `.env` 都被打包的说法不成立。**

**凭据与部署影响的证据边界**

- 本机 YAML 中抽查到的 6 个凭据字符串（`api_key` / `password`）均为完整环境变量占位表达式，且没有非空默认值。只记录形态和计数，未在输出或文档中披露值。这支持截图对本机这些字段的描述，不代表所有机器都安全，也不是完整密钥扫描。
- 脚本不限制本地配置的内容。在其他打包机上，只要同一路径含明文密码 / Key，就会原样进入包。未检查别人已经发出的 zip，因此不能断言已经发生凭据泄露。
- 即使全用占位符，本地模型、路径、开关等配置也会成为发布物的一部分。`Config.resolve_config_path()` 默认优先选择 `config.yaml`，因此解压后启动可能直接使用打包机的配置；实际是否覆盖目标旧文件还取决于部署解压方式。

**原因**：把“允许递归发布源码目录”等同于“目录内全部磁盘文件都可发布”，没有在配置边界区分模板与本地实配。Git 忽略规则本身不能保护普通文件系统遍历。

**涉及文件**

| 文件 | 作用 |
| --- | --- |
| [scripts/release/publish_zip.py:23](/Users/lulongfei/Desktop/agent/easy-agent/scripts/release/publish_zip.py:23) | 白名单包含整个 `easy_agent`；第 53 行为排除集，第 105 行开始收集，第 147 行把清单写入 zip |
| [.gitignore](/Users/lulongfei/Desktop/agent/easy-agent/.gitignore) | 声明本地 `config.yaml`、环境 YAML、`mcp.json` 等不入库，但脚本未使用这些规则 |
| `easy_agent/config/config.yaml` | 本机实际被收集的 Git 忽略文件；报告不展示配置内容 |
| [easy_agent/config/config-example.yaml](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/config/config-example.yaml) | 适合分发的配置模板 |
| [easy_agent/bootstrap/config.py:219](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/bootstrap/config.py:219) | 默认配置路径选择，解释本地实配进入包后的运行影响 |

**处理建议**：源码以跟踪文件为基础收集，对必须包含的构建产物单独白名单；配置仅允许模板。不要简单只改成 `git ls-files` 而遗漏 Git 忽略的前端 `dist`。为本地配置、凭据文件和缓存建立包内容拒绝断言，用模拟文件验证，避免依赖打包人手工检查。

### 2.4 通过反解析 SSE 判断运行结果

**结论：反序列化确实发生；属于结构改进点，尚无性能瓶颈证据。**

当前链路是：

```text
编排器捕获模型异常
  → SSEEmitter.error() / format_sse() 序列化 JSON
  → 生成器正常结束
  → api/chat.py 的 _drive_stream() 每帧 parse_sse_frame()
  → 发现 error 帧后，把 agent_runs 终态记为 failed
```

第 190 行循环里的 `parse_sse_frame(chunk)` 没有只对终态帧调用；该解析函数最终执行 `json.loads(payload)`。正文、思考、工具参数等每一帧都会再次解析，事实与截图相符。

**为什么会出现**：`_run_stream` 已把异常变成 SSE error 帧后返回，外层不能仅凭生成器正常耗尽判断成功。`0cb1b987` 增加了反解析以修复此前把这类执行误标 completed 的问题。这是一个有正确性目的的补偿，不是无用途的 JSON 操作。

**影响与限制**：增加一次与帧大小相关的解析，并让运行结果依赖输出协议；但本次没有进行吞吐或 CPU 基准测试，不能给出“明显拖慢聊天”的结论。当前传入该循环的是 JSON 数据帧；没有证据表明客户端心跳注释会进入该生成器而必然触发解析错误。

**涉及文件**

| 文件 | 作用 |
| --- | --- |
| [easy_agent/api/chat.py:167](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/api/chat.py:167) | `_drive_stream` 广播帧后反解析，第 193 行识别 error，第 200 行选择终态 |
| [easy_agent/services/streaming/orchestrator.py:1014](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/services/streaming/orchestrator.py:1014) | 捕获取消 / 异常，转为帧而不直接向外抛出 |
| [easy_agent/services/streaming/emitter.py:41](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/services/streaming/emitter.py:41) | 事件对象到 SSE 字符串的生产边界 |
| [easy_agent/services/streaming/sse.py:180](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/services/streaming/sse.py:180) | `format_sse` 与 `parse_sse_frame` |
| [tests/api/test_run_lifecycle.py:122](/Users/lulongfei/Desktop/agent/easy-agent/tests/api/test_run_lifecycle.py:122) | 验证 error 帧对应 failed、释放锁和保留部分消息 |

**处理建议**：让编排与驱动共享结构化终态或显式结果对象，传输边界再序列化。改动必须保留 error 帧、取消状态、部分内容持久化及锁释放语义；直接删除解析会重新造成失败误记成功。

### 2.5 引用卡片提交夹带样式拆分

**结论：搬迁的量真实，但“基本只是搬样式、功能改动很少”不符合完整差异。**

`0ae04e87` 共修改 27 个文件，2268 行增加、1278 行删除，其中：

| 基线 | 主体样式迁移量 | 验证结果 |
| --- | --- | --- |
| Vue3 | 557 行 | 提交前 `Chat.vue` 的第一段内联样式，与提交后 `styles/chat.css` 在去除首尾空白后逐行完全相同 |
| Vue2 | 622 行 | 增加 `.easy-agent-root ` 选择器前缀；移除该前缀后，与原主体样式完全相同 |
| 合计 | 1179 行 | 截图“约 1200 行搬样式”的数量级准确；Vue2 不是逐字节纯移动 |

完整提交还新增 / 修改：引用标记解析与首次出现顺序编号、数字引用样式、`CitationPopover.vue`、共享浮层行为、边缘翻转与可见视口定位、聊天容器事件委托、消息展示及回归测试。`Chat.vue` 仍通过 `<style scoped src="../styles/chat.css">` 引入样式，没有仅因拆文件就失去 scoped 处理。

**原因**：提交同时完成了引用展示功能和聊天组件样式提取。样式内容大量迁移，确实会放大 diff 并遮挡行为变化；但“很难审”属于有依据的审查成本评价，不能进一步推导为功能实现错误。Vue2 增加根选择器前缀也需要纳入样式审查，不能标作完全无语义变化。

**涉及文件**

| 范围 | 文件 |
| --- | --- |
| 样式迁移 | [Vue2 Chat.vue:502](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/features/chat/components/Chat.vue:502)、[Vue2 chat.css](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/features/chat/styles/chat.css)、[Vue3 Chat.vue:449](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/features/chat/components/Chat.vue:449)、[Vue3 chat.css](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/features/chat/styles/chat.css) |
| 引用功能源 | [frontend-shared/markdown/citations.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend-shared/markdown/citations.js)、[frontend-shared/citations/popover.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend-shared/citations/popover.js) |
| 组件接入 | [Vue2 CitationPopover.vue](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/features/chat/components/CitationPopover.vue)、[Vue3 CitationPopover.vue](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/features/chat/components/CitationPopover.vue)，及两版 `ChatMessage.vue`、`MarkdownBody.vue` |
| 共享副本生成 | [scripts/dev/sync-frontend-shared.mjs](/Users/lulongfei/Desktop/agent/easy-agent/scripts/dev/sync-frontend-shared.mjs)；生成副本不等于又维护一套手写引用逻辑 |

**处理建议**：今后把可独立验证的样式提取先提交，再提交功能；Vue2 前缀变化应明确列为样式作用域变更。既有提交无需仅为缩小历史 diff 撤回功能。

### 2.6 四套流式状态机与已复现的行为分叉

**结论：重复维护成立；已经能复现实际差异。**

截图中的 Vue3 文件名应更正为 `useStreamChunks.js`，不是 `useChunkHandlers.js`。

| 入口 | 当前文件 | 行数（含空行 / 注释） |
| --- | --- | ---: |
| Vue2 侧栏 | [features/chat/services/chunkHandler.js:31](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/features/chat/services/chunkHandler.js:31) | 466 |
| Vue2 主页面 | [features/chat/services/pageChunkHandler.js:9](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/features/chat/services/pageChunkHandler.js:9) | 487 |
| Vue3 侧栏 | [features/chat/services/chunkHandler.js:16](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/features/chat/services/chunkHandler.js:16) | 477 |
| Vue3 主页面 | [features/chat/composables/useStreamChunks.js:21](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/features/chat/composables/useStreamChunks.js:21) | 533 |
| 合计 | 四个实际使用的处理器 | **1963** |

这些不是由 `frontend-shared` 自动生成的同一源副本。Vue2 主页面在 `EasyAgentPage.vue:185` 导入独立处理器，侧栏在 `mixins/chatSession.js` 使用另一处理器；Vue3 主页面在 `App.vue:154` 导入 composable，侧栏使用 services 版本。

用 `difflib.SequenceMatcher(autojunk=False)` 对完整行序列比较，Vue2 两处理器有 409 行匹配；Vue2 / Vue3 侧栏有 430 行匹配。它们高度同源，但不是字节相同：宿主状态、响应式写法、事件回调及部分业务分支已经分叉。行数不是纯逻辑行数，这些相似度也不是执行语义等价证明。

**实际行为复现**

分别构造四个处理器的最小宿主状态，向 `onChunk` 输入同一组模拟事件；不调用模型、不写真实文件：

```javascript
[
  { type: 'tool_call_delta', tool_name: 'write_file', tool_call_id: 'tool-write_file', arguments: { path: 'probe.txt' }, step: 1 },
  { type: 'tool_call', tool_name: 'write_file', tool_call_id: 'c1', arguments: { path: 'probe.txt', content: 'synthetic' }, step: 1 },
  { type: 'tool_result', tool_name: 'write_file', tool_call_id: 'c1', result: 'ok', success: true, step: 1 }
]
```

| 处理器 | 结果 |
| --- | --- |
| Vue2 侧栏 | 2 个工具块：临时 `tool-write_file` 仍 `streaming=true`、结果为空；正式 `c1` 已完成 |
| Vue2 主页面 | 1 个工具块：临时块合并为 `c1`，`streaming=false`，结果为 `ok` |
| Vue3 侧栏 | 与 Vue2 侧栏相同，保留 2 个工具块 |
| Vue3 主页面 | 与 Vue2 主页面相同，只保留 1 个工具块 |

该临时 ID 形态来自后端允许的路径：[processor.py:638](/Users/lulongfei/Desktop/agent/easy-agent/easy_agent/services/streaming/processor.py:638) 在分片尚无 ID 时会使用 `tool-{name}`，随后完整工具调用可带正式 ID。因此不是随意编造一组完全不可能的字段；本次尚未用真实供应商端到端触发该时序，不量化发生频率。

**直接原因**：两个侧栏在 `tool_call` 到达时只按 ID 找已有块，正式 ID 与临时 ID 不同就新建；两个主页面还按工具名匹配正在生成的占位块并合并。现有测试主要使用从 delta 到完整调用都不变的 `c1`，不会覆盖该差异。`done` 虽可能清掉生成标记，也不会因此删除重复的工具块。

**结构原因**：主页面 / 侧栏各自演进，加上 Vue2 / Vue3 的两种宿主接口，没有把公共事件归约规则收敛到一个源。已有 `sessionGuard`、回放 checkpoint 等共享工具，以及 Vue2 共用的 `applyStreamError`，只能减少局部重复，尚未统一主状态机。截图说“改 SSE 必须四处改”过于绝对：共享工具可只改一处，但涉及公共事件分支时确实需要逐个确认四个消费者。

**其他涉及文件**

- [Vue2 EasyAgentPage.vue:185](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/EasyAgentPage.vue:185)、[Vue2 chatSession.js:30](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/features/chat/mixins/chatSession.js:30)：两个入口。
- [Vue3 App.vue:154](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/App.vue:154)、[Vue3 chatSend.js:6](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/features/chat/mixins/chatSend.js:6)：两个入口。
- [Vue2 streamReplay.test.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend-vue2/src/packages/easyAgent/features/chat/services/streamReplay.test.js)、[Vue3 chunkHandler.events.test.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/features/chat/services/chunkHandler.events.test.js)、[Vue3 useStreamChunks.test.js](/Users/lulongfei/Desktop/agent/easy-agent/frontend/src/features/chat/composables/useStreamChunks.test.js)：现有行为测试及补充边界用例的位置。

**处理建议**：先为四个入口增加“临时 ID → 正式 ID”的同一行为用例并修复侧栏合并逻辑；再按“共享事件归约规则 + Vue2 / Vue3 宿主适配”逐步收敛。主页面与侧栏特有的会话广播、专家映射等通过明确回调适配，不应为了去重丢掉现有差异行为。

## 3. 实际执行的验证

### 后端：117 项通过

使用仓库已有 `.venv`，执行：

```bash
.venv/bin/python -m pytest -q \
  tests/services/test_stream_runner.py \
  tests/services/test_stream_persistence.py \
  tests/services/test_sse_contract.py \
  tests/api/test_chat.py \
  tests/api/test_run_lifecycle.py \
  tests/api/test_chat_turn_prep.py
```

结果：**117 passed，5 条第三方 SWIG 弃用警告，33.98 秒**。覆盖异常 / 超时保存、error 帧终态、取消、SSE 契约与当前聊天准备流程；不代表执行了全仓测试或重新专项验收全部 Token 账本功能。

### 前端：71 项通过

```bash
node --test --test-reporter=spec \
  frontend-vue2/src/packages/easyAgent/features/chat/services/chunkHandler.events.test.js \
  frontend-vue2/src/packages/easyAgent/features/chat/services/streamReplay.test.js \
  frontend-vue2/src/packages/easyAgent/features/chat/services/streamMerge.test.js \
  frontend/src/features/chat/services/chunkHandler.events.test.js \
  frontend/src/features/chat/services/streamReplay.test.js \
  frontend/src/features/chat/composables/useStreamChunks.test.js \
  frontend/src/features/chat/composables/useStreamChunks.sessions.test.js \
  frontend-vue2/src/packages/easyAgent/shared/markdown/citations.test.js \
  frontend/src/shared/markdown/citations.test.js
```

结果：**71 passed，0 failed**；有 Node 模块类型告警。覆盖现有工具分片、回放、会话用量归属、错误展示、引用编号与定位。第 2.6 节临时探针另外验证了现有用例没有覆盖的 ID 切换差异，故“现有测试全绿”不否定该问题。

### 补充核查与限制

- 运行发布脚本的文件收集函数，以及临时目录模拟文件探针；未创建、分发 zip。
- 比较提交前后 CSS 正文，量化四个处理器规模与匹配行数；检查两个修复提交的祖先关系和合并差异。
- 仅对本地凭据字段做不输出值的占位符形态检查，不对已发布包或他人环境作结论。
- 初次直接调用系统 `pytest` 因缺 `fastapi` 未能启动，随后使用已有 `.venv` 完成验证；直接测试 `frontend-shared` 原始引用测试时因根目录无法解析 `marked` 失败，随后按仓库共享副本的依赖解析方式运行两侧测试并通过。没有为此安装依赖或修改项目文件。
- 本次没有启动浏览器、调用真实模型、测量 SSE 性能或执行前端构建；不声称完成页面视觉验收、线上故障复现或凭据泄露确认。

## 4. 第 1 项修复与验证（2026-11-12）

**状态：已修复。** 合并后遗留的异常持久化接口已收敛到生产实际使用的 `fallback_note` 契约。

- 删除 `orchestrator._save_partial` 和 `persistence.persist_partial` 的 `error` 参数、透传及写消息分支，并删除过时注释。
- 将 `test_chat_error_frame_marks_failed_and_releases_lock` 从“直接调用持久化并手工发送 error 帧”改为模拟图先输出 `AIMessageChunk`，再抛出连接中断、普通异常或超时。请求经过真实聊天 API、知识库包装层、编排器、`StreamProcessor` 和临时 SQLite 数据库；不调用外部模型。
- 三种异常均验证：部分正文及内容块保留、`fallback_note` 随历史接口回填、消息不新增 `error`、无成功 `done` 帧、run 为 `failed`、租约清空、会话不再流式执行。另验证超时帧含 `code=timeout`、空连接异常使用友好文案、普通异常详情不进入该聊天历史消息。
- 保留通用消息序列化的 `error` 支持、SSE error 帧及前端错误展示；新增历史兼容用例，验证旧版消息的 `error` 经仓储写入后仍可从历史接口读回。

回归命令：

```bash
.venv/bin/python -m pytest -q \
  tests/services/test_stream*.py \
  tests/services/test_sse_contract.py \
  tests/api/test_chat.py \
  tests/api/test_run_lifecycle.py \
  tests/api/test_chat_turn_prep.py \
  tests/db/test_message_repository.py \
  tests/db/test_session_repository.py \
  tests/db/test_legacy_message_repair.py \
  --tb=short --show-capture=no
```

结果：**255 passed，5 条第三方 SWIG 弃用警告，34.60 秒**。覆盖流式处理、取消、HITL 恢复、异常保存、生命周期、SSE 契约及消息仓储兼容；本次未修改前端，也未调用真实模型或执行浏览器验收。

静态检查：`uvx ruff check easy_agent/services/streaming/orchestrator.py easy_agent/services/streaming/persistence.py tests/api/test_run_lifecycle.py` 与 `git diff --check` 均通过。

## 5. 第 2 项处理与验证（2026-11-12）

**状态：当前契约已统一，关联回归已修复。** 本节基于提交 `2fd5250a` 开始处理；历史上已发生的需求调整仍按第 2.2 节记录，不把它改写为当前代码故障，也不推测金融门禁撤回的产品决策背景。

### 当前行为及修复

- 新增 [聊天行为契约](../chat-behavior-contract.md)，明确普通聊天金融门禁维持撤回，专家预检、技能校验及工具权限继续生效；今后调整需同步更新契约、接口文档和回归断言。金融历史设计增加当前契约入口。
- 聊天 Token / 上下文是最近一次模型调用的快照，个人账本累计每次实际调用；耗时、步数另按当前消息累计。修正 Token API 文档中已撤回的普通聊天领域分类描述，并在 SSE 文档中明确这些范围。
- 全局临时错误 5 秒消失，消息内 `error` 和中性 `fallback_note` 随消息保留；修正 Vue3 超时处理注释中误称消息错误也会自动消失的文字。
- **修复实际回归**：Vue3 侧栏 `chatSession.js` 声明了两个同名 `watch` 对象，后面的临时错误计时监听覆盖了前面的 `loadedSessionId` 监听，导致切换 / 加载历史后不再重建审批。合并为单个 `watch`，两类监听同时生效。新增用例先复现审批未恢复，再验证修复后恢复待审批工具、通知 5 秒消失、消息错误保留及切换会话清除旧审批。

### 整理提交范围前的验证记录

以下完整前端测试和构建结果来自当时的工作区，其中还包含额外的流状态导出修复和行数整理；不能直接视为下方编号 2 独立提交范围的验证结果。后端验证不依赖这些额外前端改动。

后端命令：

```bash
.venv/bin/python -m pytest -q \
  tests/api/test_chat_turn_prep.py \
  tests/agent/test_token_usage.py \
  tests/agent/test_token_usage_graph.py \
  tests/db/test_token_usage.py \
  tests/api/test_usage.py \
  tests/services/test_usage_scopes.py \
  tests/services/test_stream_processor.py \
  tests/services/test_stream_cache_usage.py \
  tests/api/test_run_lifecycle.py \
  --tb=short --show-capture=no -rs
```

结果：**177 passed、3 skipped，40.33 秒**；3 项跳过均因未配置 `EASY_TEST_MYSQL_HOST`，另有 5 条第三方 SWIG 弃用警告。普通聊天覆盖诗歌、旅游、编程、债券、PPT 等话题；新增图调用用例经过生产模型工厂 / 回调、真实图、流处理器和临时 SQLite，验证两次调用 `100/10`、`200/20` 对应最终快照 `200/20`、个人累计 `330`。

前端与构建命令：

```bash
npm --prefix frontend-vue2 run test:unit
npm --prefix frontend-vue2 run lint:vue2
npm --prefix frontend-vue2 run check:easy-agent
npm --prefix frontend-vue2 run build
npm --prefix frontend run build
```

Vue3 的 `npm run test:unit` 使用带引号的通配符，本机 Node v20.19.0 未展开而报找不到文件。本次按其相同范围显式收集测试文件执行，未把空集视为通过：

```bash
cd frontend
node --input-type=module - <<'JS'
import { execFileSync, spawnSync } from 'node:child_process'
const files = execFileSync('rg', ['--files', 'src'], { encoding: 'utf8' })
  .trim().split('\n')
  .filter(file => /^src\/(api|utils|features|shared)\//.test(file) && file.endsWith('.test.js'))
const result = spawnSync(process.execPath, ['--test', ...files], { stdio: 'inherit' })
process.exit(result.status ?? 1)
JS
```

结果：**Vue2 全量 395 passed，Vue3 全量 190 passed，均无失败 / 跳过**。新增两版 `MessageError.render.test.js` 编译实际组件并执行生命周期，验证初始错误超过 5 秒仍显示，清空后隐藏，历史重新回填后超过 60 秒仍显示；Vue2 使用 jsdom 挂载，Vue3 使用内存渲染宿主。已有用例覆盖通知更新 / 取消、普通异常与超时、会话用量及审批续跑。

两版生产构建均成功；仍有既有样式导出、产物体积和 Browserslist 提示，无缺失 `probeStreamState` 导出警告。Vue2 语法 / 模块检查、`uvx ruff check tests/agent/test_token_usage_graph.py`、`git diff --check` 均通过。

验证边界：供应商响应使用模拟 HTTP，不消耗真实模型额度；未运行真实 MySQL、浏览器视觉验收或重启运行中的服务。组件渲染和图调用测试不等于线上端到端验收；本次未提交、推送或部署。

### 编号 2 的独立提交范围

本次仅准备以下 11 个文件，用于统一三项行为契约、修复错误计时导致的审批监听覆盖并补充回归：

| 文件 | 提交理由 |
| --- | --- |
| `docs/chat-behavior-contract.md` | 集中定义金融限制、Token 范围及错误展示的当前约定 |
| `docs/design/意图识别-只回答金融相关的问题.md` | 历史撤回记录链接当前约定 |
| `docs/token-usage-api.md` | 区分累计账本与调用快照，移除过时的金融分类采集说明 |
| `docs/sse-events.md` | 明确 Token、耗时 / 步数和错误提示各自的范围 |
| `docs/reviews/2026-11-12-ai-findings-verification.md` | 记录编号 2 处理结果、提交边界和验证限制 |
| `frontend/src/features/chat/mixins/chatSession.js` | 合并重复 watch，保留通知计时和审批恢复两类监听 |
| `frontend/src/features/chat/services/chunkHandler.js` | 修正消息错误也会在 5 秒后消失的误导注释 |
| `frontend/src/features/chat/mixins/chatSession.errors.test.js` | 回归通知计时与审批恢复同时生效 |
| `frontend/src/features/chat/components/MessageError.render.test.js` | 固定 Vue3 消息错误常驻及历史回填行为 |
| `frontend-vue2/src/packages/easyAgent/features/chat/components/MessageError.render.test.js` | 固定 Vue2 同一展示契约 |
| `tests/agent/test_token_usage_graph.py` | 验证最近调用快照和个人累计账本采用不同范围 |

额外 6 个文件的改动保留在工作区，不纳入本次提交：双前端的 `streamResume.js` / `streamResume.test.js`、Vue3 `App.vue`、Vue2 `features/chat/mixins/chatSession.js`。这些属于既有缺失导出修复及行数整理。

独立补丁仍继承 HEAD 已有的验证阻塞：缺失 `probeStreamState` 导出造成 Vue2 三个测试文件无法加载、Vue3 生产构建失败；两版结构测试另有既有行数超限。这些失败已在原始 HEAD 的独立临时副本复现，不能表述为编号 2 独立补丁的全量测试和双构建均通过。

### 独立提交范围的实际验证

通过 `git checkout-index --all --prefix=<临时目录>/` 导出仅含上述 11 个暂存文件改动的快照，前端仅链接现有 `node_modules`，后端使用现有 `.venv` 和快照内的源码执行；未包含额外 6 个未暂存文件的改动。

- 后端执行上方同一组 9 个测试文件：**177 passed、3 skipped，37.06 秒**。3 项真实 MySQL 测试仍因未配置 `EASY_TEST_MYSQL_HOST` 跳过，另有 5 条第三方 SWIG 弃用警告。
- 前端执行下列 14 个文件：**58 passed、0 failed、0 skipped**，覆盖双版本消息错误常驻、全局通知计时、审批恢复、SSE 错误与 Token 快照 / 回放归属。此结果为定向回归，不代表全量测试或构建通过。

```bash
node --test \
  frontend/src/features/chat/mixins/chatSession.errors.test.js \
  frontend/src/features/chat/components/MessageError.render.test.js \
  frontend-vue2/src/packages/easyAgent/features/chat/components/MessageError.render.test.js \
  frontend/src/utils/autoDismiss.test.js \
  frontend-vue2/src/packages/easyAgent/utils/autoDismiss.test.js \
  frontend/src/features/chat/services/chunkHandler.events.test.js \
  frontend/src/features/chat/services/streamReplay.test.js \
  frontend/src/features/chat/composables/useStreamChunks.test.js \
  frontend/src/features/chat/composables/useStreamChunks.resumeUsage.test.js \
  frontend/src/features/chat/composables/useStreamChunks.sessions.test.js \
  frontend-vue2/src/packages/easyAgent/features/chat/services/chunkHandler.events.test.js \
  frontend-vue2/src/packages/easyAgent/features/chat/services/chunkHandler.resumeUsage.test.js \
  frontend-vue2/src/packages/easyAgent/features/chat/services/chunkHandler.usage.test.js \
  frontend-vue2/src/packages/easyAgent/features/chat/services/streamReplay.test.js
```
