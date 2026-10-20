# MCP 胶囊回归输入框（仅"用户输入阶段"可见）设计

- 日期：2026-10-15
- 状态：已评审（用户确认方案 A + 胶囊带 × 可移除 + 预防性严格分区）
- 范围：`frontend-vue2/src/components/ChatInput.vue`（8080 托管基线）与 `frontend/src/components/ChatInput.vue`（Vue3 开发线）
- 取代：`2026-10-15-mcp-connector-structured-field-design.md` 的 §④「输入框胶囊移除」与"非目标"中"不为输入框保留任何已选连接器提示"一条

## 背景

`2026-10-15-mcp-connector-structured-field-design.md`（已实施）把连接器选择从消息文本暗号改为请求体结构化字段 `connectors: List[str]`，并**删除了输入框里的连接器胶囊**，理由是"关闭「+」面板后看不出选了什么"被用户接受。

用户体验后的新诉求：**提交前需要在输入框里看见胶囊**，以便确认本轮到底选中了哪些 MCP；但不能让胶囊出现在"会话展示阶段"（查历史 / 实时渲染），避免选择状态混进历史记录或渲染内容的视觉里。

## 现状事实（2026-10-15 实测）

| 事实 | `frontend-vue2` | `frontend`（Vue3） |
|---|---|---|
| 选择状态 | `data.selectedConnectors`（:640） | `ref([])`（:678） |
| 提交清空 | `send()` 内（:1469） | `send()` 内（:1686） |
| 会话切换清空 | **无**（`sessionId` watcher 只清 `uploadedFiles`，:792） | 有（:1401，清 `selectedSkills` + `selectedConnectors`） |
| 移除方法 | `removeSelectedConnector`（:1207） | `removeSelectedConnector`（:1396） |
| 胶囊模板 / CSS | `HEAD` 有（`.selected-connectors`），本次改造已删 | `HEAD` 无 |
| `ChatInput` 挂载 | `Chat.vue` 中**无 `:key`** → 跨会话复用同一实例 | 同 |

`ChatInput` 实例跨会话复用，是"选择状态残留到历史会话"的物理原因。

## 方案选择

- **A. 状态即真相 + 会话守卫（采用）**：可见性 = `selectedConnectors.length > 0 && !isStreaming`；`selectedConnectors` 严格只代表"本轮未提交的选择"，在提交与真实会话切换两个入口清空。不新增状态。
- B. 显式阶段标志 / 派生 `pillVisible`：否定。引入一份可与 `selectedConnectors` 不同步的派生状态，Vue2/Vue3 各一份，正是"状态混淆"的新来源。
- C. 选择状态按会话提升到父级（`composerDraft[sessionId]`）：否定。改动面横跨 `ChatInput`/`Chat`/`App`/mixin，且"按会话存档"会让切回旧会话时胶囊重新出现，与需求相反。

选择 **A**，并把"会话切换不清空"这个唯一漏点当根因修掉，而不是只加 `v-if` 兜底。

## 可见性真值表

| 阶段 / 事件 | 胶囊 |
|---|---|
| 「+」面板选中连接器（未提交） | **显示** |
| 点胶囊 `×`，或面板行再点一次 | 立即隐藏该项 |
| 回车 / 点发送（`send()`） | **立即隐藏**（先在 `emit` 里取走 `connectors`，再清空状态） |
| 本轮流式回答中（`isStreaming`） | 隐藏（第二道闸，防御性） |
| 切换会话 / 新建对话（`oldVal` 为真值的 `sessionId` 变更） | 清空 → 不显示 |
| **首次建会话**（`sessionId`: `null → "abc"`，含"选连接器 + 加文件"触发的建会话） | **不清空**，胶囊保留 |
| 重进页面 / 从历史列表打开旧会话（组件新挂载） | 不显示（初始为空） |
| HITL 待审批恢复 / 定时任务 / 知识面板（不经 `ChatInput`） | 不涉及 |

"首次建会话不清空"是关键守卫：vue2 的 `uploadedFiles` watcher 已有同款注释（"oldVal 为 null（新对话加文件时触发的建会话）不清，否则会清掉刚选的文件"），连接器复用同一守卫；否则"选了连接器 + 上传文件"会在建会话瞬间把胶囊清掉。

## 实现

### vue2 `frontend-vue2/src/components/ChatInput.vue`

1. 模板：`.selected-skills` 块之后插入 `.selected-connectors` 块（结构照 `HEAD`：图标 + 名称 + `×`，`:title="connector.desc || connector.name"`），`v-if="showConnectorPills"`。
2. `computed.showConnectorPills()` = `this.selectedConnectors.length > 0 && !this.isStreaming`。
3. `sessionId` watcher 的既有守卫块内追加 `this.selectedConnectors = []`；**不动 `selectedSkills`**（技能存在"待选技能"跨会话写入链路，清掉会伤现有功能）。
4. CSS：新增独立的 `.selected-connectors` / `.selected-connector` / `.selected-connector-icon` / `.selected-connector-name` / `.selected-connector-remove` 块（**不合并进 `.selected-skill` 选择器**），沿用既有令牌（`--accent-color` + `color-mix` + `999px` 圆角），不写死浅色色值。

### vue3 `frontend/src/components/ChatInput.vue`

1. 模板同位置插入同结构块，`v-if="showConnectorPills"`。
2. `computed(() => selectedConnectors.value.length > 0 && !props.isStreaming)`。
3. `watch(() => props.sessionId, ...)`（:1401）：给 `selectedConnectors`（连带 `selectedSkills`）加上 `oldVal` 守卫，与 vue2 语义对齐。
4. 同款 CSS 块（与 vue2 逐字同源，仅换行差异）。

### 不改动

- 请求体协议（`connectors` 字段已就绪）。
- 消息气泡 / 历史渲染（那里本就没有胶囊元素，胶囊也不再从会话数据派生）。
- `canSend` 语义、`+` 面板行内「已选」标与自动收起。

## 技能胶囊（2026-10-15 追加："技能的显示改造参考连接器"）

同一套可见性规则也应用到技能胶囊（`.selected-skills` / `.selected-skill`）：

| 环节 | 改动 |
|---|---|
| 可见性 | `v-if="selectedSkills.length > 0"` → `v-if="showSkillPills"`（已选中 且 `!isStreaming`） |
| 会话切换 | vue2 watcher 追加 `this.selectedSkills = []`；vue3 原本就是任何变化都清空 |
| 上传触发的建会话 | 与连接器同用"先存后补"（两份都补 `keepSkills`） |
| 提交 | ~~不变：`send()` 内已清空，技能照旧以 `/目录名` 拼进消息文本~~ → **已改为结构化 `skills` 字段**（技能目录名），正文不再拼前缀，见 `2026-10-15-mcp-connector-structured-field-design.md` 的「技能」章节 |

**不影响"待选技能"链路**：技能中心「创建技能」把技能写进 sessionStorage，由本组件在**挂载时**消费（`restorePendingSkill()` 在 `mounted`/`onMounted` 里，读后即清除），不经 watcher，所以"切会话复位"不会把它清掉。

**CSS 不动**：`.selected-skill*` 与 `.selected-connector*` 的 5 条规则（主规则 / name / remove / hover / svg）本来就逐字一致，仅图标来源不同（`SkillIcon` 组件 vs 内联 MCP svg）。

**已知差异 → 已消解（2026-10-15 追补）**：vue3 的「+」面板技能行**本来就点得动**，但走的是
**专属面板**（`subpanelCategory === 'skills'` → `.skill-subitem` 列表 + 搜索 + 本地导入/管理入口，
点击 `toggleSkillToken`，选中态是行尾对勾），而 vue2 走共享列表（`.plus-list-item`，
`toggleSkillFromPlus` + 行尾「已选」标）。两条链路此前只在"停用技能"上不一致：vue2 禁止挂载，
vue3 点得动。本次给 vue3 补上同款守卫（`onSkillSubItemClick` 判 `item.enabled`）与
`.skill-sub-item.disabled` 样式、行内悬浮提示，其余保持各自实现。

## 非目标

- 不为"已用连接器"在气泡或历史里留任何痕迹。
- 不改 `selectedSkills` 的既有语义（vue2 完全不动；vue3 仅补 `oldVal` 守卫）。
- 不做跨会话的选择草稿/记忆。

## 验证

1. `frontend-vue2`：`npm run build`、`npm run lint:vue2`、`npm run test:unit`。
2. `frontend`：`npm run build`（结尾 `sh scripts/generate-runtime-config.sh` 的 CRLF 报错是 `819a58e` 引入的既有问题，与本次无关）。
3. 真机渲染验收（本机 8080 托管 `frontend-vue2/dist`，先 `md5sum` 比对 `curl http://127.0.0.1:8080/js/app.js` 确认是新产物）：
   - 选中 → 胶囊出现，`getComputedStyle` 断言圆角 / 边框 / 底色；
   - 点 `×` → 该项消失；
   - 回车提交 → 胶囊消失，且请求体 `message` 干净、`connectors` 为选中项；
   - 切换会话 → 胶囊不残留；
   - 明亮 / 暗色两种主题各断言一次。
4. Vue3 侧按既有做法（项目自带 vite dev + 临时验收页 + playwright 桩掉后端接口）跑同样断言。
