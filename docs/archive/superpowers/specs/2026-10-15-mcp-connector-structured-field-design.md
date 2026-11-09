# MCP 连接器改为结构化字段传输（消息文本不再带「使用连接器[X]」）设计

- 日期：2026-10-15
- 状态：已评审（用户确认方案 A + 存量数据不动）
- 范围：连接器（MCP）选择信息改为请求体结构化字段；用户消息文本不再拼接 `使用连接器[名称]`；输入框胶囊移除
- 选定方案：
  - 传输：**请求体新增 `connectors: Optional[List[str]]`**（连接器展示名），后端优先用字段、并与消息文本解析结果合并去重
  - 存量数据：**不动 DB**（老会话气泡与老标题里已有的暗号原样保留），运行时代码零字符串清洗
  - 输入框：**删除连接器胶囊**，保留「+」面板内的行内「已选」标与行点击切换（取消选择的唯一入口）
  - 前端范围：`frontend-vue2/`（8080 托管基线）与 `frontend/`（Vue3 开发线）两份同步

## 背景

输入框选中连接器后，前端把已选连接器拼成指令前缀写进消息文本（`frontend-vue2/src/utils/skillSlash.js` 的 `buildConnectorPayload`，Vue3 同名函数），后端用正则从**用户本轮消息文本**里解析这段暗号来决定本轮注入哪些 MCP 工具（`easy_agent/services/connectors.py:205` 的 `CONNECTOR_DIRECTIVE_RE`，调用点 `easy_agent/api/chat.py:429`、`easy_agent/api/expert.py:680`）。

同一条文本还被当成「用户消息内容」用于三处展示/存储：

1. 前端把它直接当作用户气泡内容（`frontend-vue2/src/App.vue:1444-1459`、`frontend-vue2/src/mixins/chatSession.js:1189-1206`）；
2. 后端原样落库（`easy_agent/api/chat.py:402-412`），历史接口原样返回（`easy_agent/api/sessions.py:197-206`），于是刷新/重进会话气泡里还是这句暗号；
3. 会话标题就是首条消息的前 15 个字（`easy_agent/api/chat.py:44-52` `generate_session_title`，调用点 `chat.py:64`、`chat.py:341`、`expert.py:595`），于是侧边栏标题也变成 `使用连接器[甲] 你好`。

## 现状问题

1. **展示层被迫承载传输协议**：`使用连接器[甲]` 是给后端看的技术暗号，却出现在输入框胶囊、消息气泡、会话标题里，用户看到的是「不友好」的机器语言。
2. **改一处要清多处**：任何「展示文本 ≠ 传输文本」的诉求，都得在前端气泡（2 份重复实现）、后端落库、历史返回、标题生成里各清一遍字符串。
3. **模型输入也被污染**：发给模型的 `parsed_content` 直接来自 `request.message`（`chat.py:345→455`），暗号对模型是噪音。

## 目标与验收

- 请求体带 `connectors` 时，后端按它注入 MCP 工具；字段与文本暗号**同时存在时合并去重**，字段缺省时行为与改造前**逐字一致**（老前端、手打暗号、脚本调用都继续有效）。
- 新前端发出的 `message` 不含 `使用连接器[...]`：消息气泡只显示用户原话，会话标题是用户原话前 15 字，模型输入无暗号。
- 输入框不再有连接器胶囊；「+」→ 连接器面板里选中行仍有「已选」标，再点一次可取消，选完照旧自动收起菜单。
- 后端 `parse_connector_directives` / `resolve_connector_servers` 保留并继续被复用（不做文本清洗）。
- 验收：`pytest` 全量回归 + 新增用例；两份前端 `npm run build` 通过；无头浏览器断言请求体（`message` 干净 + `connectors` 正确）+ 气泡与标题渲染；用户重启 8080 后抽查真实链路。

## 非目标

- 不迁移存量数据：老会话消息 content 与老会话标题里的 `使用连接器[...]` 保持原样。
- 不动技能前缀：`/技能目录名` 由模型用来认技能（`buildSkillPayload` 不变），本次不动其展示。
- 不为输入框保留任何「已选连接器」的替代提示（角标/悬浮层等）。**（已被取代：2026-10-15 当日改为在输入框内显示胶囊，见 `2026-10-15-mcp-pill-composing-phase-design.md`）**
- 不改 MCP 工具加载链路与连接器管理页。
- 不删后端的文本解析能力（`parse_message_connectors` 等）。

## 设计

### ① 协议

`POST /agent/chat/stream` 与专家链路请求体新增一个字段，形状照既有的 `page_context` 先例（`easy_agent/models/api.py:78-83`）：

```python
connectors: Optional[List[str]] = Field(
    default=None,
    description="本轮选中的连接器展示名（输入框「+」面板选中项），据此只注入这些 MCP 工具；"
                "为空/缺省时回退解析消息文本里的「使用连接器[名称]」",
)
```

- 值语义：连接器**展示名**列表，与今天写进文本的名字完全同源；名称 → mcp.json server 名的映射仍由 `resolve_connector_servers` 负责（`services/connectors.py:236-286`），未识别名字仍进 `unknown` 并记日志。
- 缺省（老客户端）= `None`；新前端一律显式传（可能为空数组）。

### ② 后端

1. `easy_agent/models/api.py`：`ChatRequest` 加 `connectors` 字段。
2. `easy_agent/api/chat.py:429-431`：解析入口改为

```python
names = list(request.connectors or []) + parse_connector_directives(request.message)
connector_servers, unknown_connectors = resolve_connector_servers(names, username)
```

   字段优先并与文本合并去重（顺序 = 字段中的用户选择顺序 + 文本中出现顺序）→ 手打 `使用连接器[X]` 的习惯与老前端都不受影响；新前端文本里没有暗号，等于纯字段驱动。文本侧的 `MAX_CONNECTOR_DIRECTIVES`（32 条）上限只约束文本解析，字段侧由前端选择行为天然受限。
3. `easy_agent/api/expert.py:680`：同样处理。
4. 落库、`parsed_content`、标题生成、HITL 恢复（`connector_servers=None` 跟随缓存）全部不动 —— 文本干净之后它们自然干净。

### ③ 前端（Vue2 与 Vue3 各一份）

emit 链路（两份形态一致）：`ChatInput.vue` → `Chat.vue` 的 `onSend`/`handleSend` → `App.vue` 的 `handleSendMessage` → `api/chat.js` 的 `sendMessage` → 请求体。

- `ChatInput.vue`：`buildPayload()` 回到只拼技能前缀（`buildSkillPayload`）；`emit('send', payload, files, signal, enableDeepThink, enableWebSearch, connectorNames)`，`connectorNames = selectedConnectors.map(c => c.name)`。
- `Chat.vue`：`onSend` / `handleSend` 各加尾参并转发 `send-message`。
- `App.vue`：`handleSendMessage(..., connectors = [])` → 传给 `sendMessage`（Vue2 的 `mixins/chatSession.js:1179` 重复实现同步改；Vue3 只有 `App.vue` 一份）。
- `api/chat.js`：`sendMessage(..., connectors = [])`，`if (connectors && connectors.length) payload.connectors = connectors`（尾参带默认值 → 知识面板、HITL 恢复等既有调用点零改动）。
- `utils/skillSlash.js`：删除 `buildConnectorPayload`（它存在的唯一理由就是拼暗号），`buildSkillPayload` / `parseSlashQuery` 不动。

### ④ 输入框胶囊移除

> **已被取代（2026-10-15 当日）：** 用户实际使用后要求「提交前能在输入框里确认已选 MCP」，胶囊已按**仅「用户输入阶段」可见**重新加回，见 `2026-10-15-mcp-pill-composing-phase-design.md`。本节保留为当时的决策记录；其中 `selectedConnectors` 状态、`toggleConnector`、`onPlusListItemClick`、「+」面板行内「已选」标的保留策略**仍然有效**，只有"模板/CSS 删除"这一条被推翻。

- 模板删除 `selected-connectors` 整块；CSS 删除 `.selected-connectors` / `.selected-connector` / `.selected-connector-icon` / `.selected-connector-name` / `.selected-connector-remove`（技能胶囊的 `.selected-skill*` 规则恢复为独立选择器）。
- 保留：`selectedConnectors` 状态、`isConnectorSelected`、`toggleConnector`、`onPlusListItemClick`、`hidePlusMenu`、面板行内「已选」标与 `.plus-list-state` 样式。
- 直接结果：关闭「+」面板后输入框完全看不出选了什么（用户已确认接受）；将来若要补一个不显眼的提示，单独立项。

## 兼容与边界

| 场景 | 行为 |
|---|---|
| 新前端 + 新后端 | 气泡/标题/历史/模型输入全无暗号；按字段过滤工具 |
| 新前端 + 旧后端（未重启） | 文本里没有暗号 → 连接器过滤**静默不生效**（退回全部启用连接器的工具）。A 方案的唯一代价，靠一次后端重启消除 |
| 旧前端（旧 dist）+ 新后端 | 文本解析回退，行为与今天完全一致 |
| 用户手打 `使用连接器[甲]` | 文本解析仍生效（与字段合并去重） |
| HITL 审批恢复 / 定时任务 / 知识面板 | 不传字段 → `None` → 沿用现有语义，零改动 |

## 验证

1. `pytest tests/ -q` 全量回归；`tests/api/test_chat.py` 增补：字段生效并透传 Agent 创建、字段 + 文本合并去重、字段缺省回退。
2. 两份前端 `npm run build`；Vue2 侧 `npm run lint:vue2`。
3. 无头浏览器验收：
   - 拦截 `/agent/chat/stream` 断言请求体 `message` 不含 `使用连接器` 且 `connectors` 为选中项；
   - 断言气泡只显示用户原话、输入框无胶囊、面板「已选」标与自动收起行为保持；
   - 真实链路抽查（临时后端 + 一次性用户）确认后端日志 `🔌 连接器指令 | 命中: ...` 来自字段且工具被过滤。
4. 用户重启 8080 的 `main.py` 后，在 8080 上抽查一次真实会话（标题与气泡干净、连接器生效）。

---

## 技能（2026-10-15 追加：\"技能的前端显示与后端交互对齐连接器\"）

连接器这套做完后，技能仍是老办法：输入框把已选技能拼成 `/目录名 正文` 发出去，展示层
再靠字符串清洗把它扒回来 —— 气泡标签（`parseLeadingSkillTokens`）、会话标题
（`split_leading_directives`）、会话列表（`skillFreeTitle`）三处都在解析同一段机器语言。
本次把技能也改成结构化字段，与连接器同构。

### ① 协议

`ChatRequest` / `ExpertRunRequest` 新增：

```python
skills: Optional[List[str]] = None   # 技能**目录名**（输入框胶囊的 dir_name），非展示名
```

选目录名而不是展示名：目录名是技能的唯一稳定标识（Agent 按 `/目录名` 认技能），
展示名可能重复或改名。落库到用户消息的也是同一份目录名列表。

### ② 后端

- `skill_library.parse_skill_directives()`：解析消息开头的 `/目录名`（限字母数字连字符下划线，
  且后面必须跟空白或结束 —— `/usr/bin`、`/workspace` 这类路径不会被误判）。
- `skill_library.resolve_skill_dirs()`：目录必须真实存在于该用户技能库（`skills/` +
  技能停用目录），未识别的只记日志、不阻断对话。
- `skill_library.resolve_turn_skills()`：字段优先 + 文本指令合并去重（旧前端/手打指令照旧生效）。
- `skill_library.inject_skill_directives()` + `pending_skill_directives()`：只把**文本里还没出现**
  的目录名拼成 `/目录名` 前缀注入 `parsed_content`（模型输入）；消息文本一字不改。
- `chat.py` / `expert.py`：命中时日志 `🧩 技能指令 | 命中: [...] | 未识别: [...]`；
  用户消息新增 `skills` 字段（与 `files` / `page_context` 同级），随历史接口原样返回。
- 标题兜底：只挂技能/连接器、没写正文时，`generate_session_title(..., fallback_name=...)`
  用技能展示名（其次连接器名）当标题，不再只剩「未命名会话」。
- 空正文：`message` 放宽为可空，`message`/`files`/`skills`/`connectors` 全空才 400
  （此前只挂连接器不写正文会 422）。

### ③ 前端（Vue2 与 Vue3 各一份）

| 环节 | 改动 |
|---|---|
| 提交 | `buildSkillPayload(skills, text)` → `buildMessageText(text)`：正文只归一化空白，不再拼 `/目录名` |
| 透传 | `ChatInput emit('send', …, connectors, skills)` → `Chat.vue` → `App.vue` / `chatSession.js` → `api/chat.js` / `api/expert.js` 的 `payload.skills` |
| 气泡 | 优先按 `message.skills`（目录名）渲染技能标签，展示名/图标按目录名反查技能目录；无该字段的老数据仍走文本解析，不依赖目录接口是否加载完成 |
| 复制 | 复制气泡里真正展示的正文（`cleanUserContent(displayContent)`），不再单独做一次前缀剥离 |
| 会话标题 | 新会话标题天然是用户原话；老会话标题仍按 `skillFreeTitle` 反查剥离 |

技能目录共享状态 `store/skillDirectoryStore.js` 与纯逻辑 `utils/skillTokens.js` 本就在
Vue2 侧，本次整份移植进 Vue3（store 用 `reactive`）。

### ④ 输入框胶囊与高度

- 胶囊与正文**同行**：`.input-row` 由 `align-items: flex-end` 改 `flex-start`（多行输入时胶囊
  不再被压到最后一行），`.selected-skills` / `.selected-connectors` 去掉 `flex-shrink: 0`
  改为可收缩 + 内部换行（胶囊多时不再把输入区挤到下一行）。
- 输入框**最多 5 行**（行高 24px → 120px，原为 150px），再多就在框内滚动：`overflow-y: auto`，
  配合全局 6px 滚动条样式在右侧出轨道；`autoResize()` 的硬编码上限抽成
  `TEXTAREA_MAX_ROWS × TEXTAREA_LINE_HEIGHT` 常量，与 CSS 的 `max-height` 同步修改。
- Vue3 的「+」→ 技能子面板是**独立面板**（`.skill-sub-item`，带搜索/本地导入/管理入口），
  与 Vue2 走共享列表（`.plus-list-item`）不同实现；本次只补上 Vue2 已有的约束：
  停用技能不可挂载（`onSkillSubItemClick` 守卫 + `.disabled` 样式）、行内悬浮提示。

### 兼容与边界

| 场景 | 行为 |
|---|---|
| 新前端 + 新后端 | 气泡/标题/历史/落库正文全无 `/技能名`；模型输入仍是 `/目录名` 前缀，技能照常生效 |
| 新前端 + 旧后端（未重启） | 正文没有 `/目录名` → 模型不知道要挂哪个技能（技能**静默不生效**），需一次后端重启消除 |
| 旧前端（旧 dist）+ 新后端 | 文本解析回退，且落库时补 `skills` 字段，行为和改造前一致 |
| 用户手打 `/技能名` | 文本解析仍生效（与字段合并去重，不重复拼） |
| HITL 审批恢复 | 不重建用户消息 → 沿用同一 thread 的原始输入，零改动 |
| 会话「重新发送」 | 与连接器同源的老问题：重发只带文本，结构化字段会丢（本次未做，与连接器保持同一水位） |

### 验证（2026-10-15 已完成）

1. `pytest tests/api/test_chat.py tests/api/test_expert.py tests/api/test_session_title.py tests/services -q` → 334 passed；
   全量 `pytest tests/ -q` → 752 passed，9 failed / 12 errors **全部集中在
   `tests/integration/test_auth_session_expiry.py`**（该文件引用了已不存在的
   `auth_middleware._user_activity_cache`，与本次改动无关）。
2. 两份前端 `npm run build`；Vue2 `npm run lint:vue2` + `npm run test:unit`（43 passed）。
3. 真机渲染验收（临时后端 8090 + 一次性用户 preset_test / zhangsan）：
   - Vue2（后端托管的 `frontend-vue2/dist`）：`+` → 技能 → 点行挂上 `pptx` 胶囊；胶囊与正文首行
     顶对齐（两者 `top` 均为 492）；输入框 1/3/5 行分别 24/72/120px，12 行时 120px + 可滚动
     （`scrollHeight 168 > clientHeight 120`，滚轮 `scrollTop 48`）。
   - 发送后气泡：`pptx` 标签与正文同行（同一 flex 行，`top` 113/111），正文无 `/pptx`；
     后端日志 `🧩 技能指令 | 命中: ['pptx']`；`session_messages.extra_data` 为
     `{"skills": ["pptx"]}`，`content` 是用户原话；模型据此读取了 pptx 的 SKILL.md 并作答。
   - 老数据回退：zhangsan 的 `/docx 写一个简单的智能体介绍的文档`（无 `skills` 字段）仍渲染
     `docx` 标签 + 干净正文；老标题 `/docx 写一个简单的智能体...` 在侧边栏显示为
     `写一个简单的智能体...`。
   - Vue3（`frontend/dist` + 覆盖 `dist/runtime-config.js` 指向 8090）：`+` → 技能子面板点行挂上
     `docx`（行变 `active`、行尾对勾），胶囊与正文同行，输入框高度/滚动同上，发送后
     `extra_data = {"skills": ["docx"]}`。
   - 暗色：按 `theme.js` 的真实换肤机制（`--accent-color` / `--text-primary` 等内联令牌）套暗色，
     技能标签边框/底色跟随 accent、文字跟随 text-primary，无硬编码颜色。
4. 待用户重启 8080 的 `main.py`（新代码生效）后，在 8080 上抽查一次真实会话。
