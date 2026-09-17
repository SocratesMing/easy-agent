# frontend-vue2 改动说明

> 分支：`feat/frontend-vue2-new`
> 涉及目录：`frontend-vue2/`（Vue 2.6 + Vue CLI 前端）
> 状态：全部改动已提交，工作区干净
> 校验方式：`npm run lint:vue2`（扫描文件 37 → 35）+ `npm run build`，均通过

---

## 一、Vue 2.6 语法规范改造（Vue3 → Vue2.6）

> 背景：`frontend-vue2` 由 Vue3 版 `frontend/` 迁移而来，需把 Vue3 / `<script setup>` 写法改为 Vue 2.6 选项式 API。

### 1.1 Vue3 与 Vue2.6 写法对照

| 类别 | Vue3 写法（迁移前） | Vue 2.6 写法（迁移后） | 原因 |
|---|---|---|---|
| 组件写法 | `<script setup>` + 顶层 `const` | 选项式 `data()` / `computed` / `methods` | Vue2 模板编译在独立模块，只能访问组件实例属性 |
| 模板取值 | `import` 常量直接在模板用（`{{ APP_TITLE }}`） | 先挂到 `data()` 再使用 | 同上，否则渲染为空 |
| 响应式 | `ref` / `shallowRef` / `reactive` | `data()` / `Vue.observable()` | 模块级普通变量没有响应式，异步就绪后不会触发重渲染 |
| 生命周期 | `onMounted` / `onBeforeUnmount` | `mounted()` / `beforeDestroy()` | Vue2 选项名不同 |
| 样式深选择器 | `:deep(.x)` / `::v-deep(.x)` | `::v-deep .x`（空格写法） | 括号写法在 vue-loader 15 下编译成非法选择器，整条规则被丢弃 |
| 事件名 | `@toggleTheme` | `@toggle-theme` | Vue2 中事件名必须 kebab-case |
| 模板约束 | `v-for` 无 `:key`、`v-if` 与 `v-for` 同元素、`data` 写成对象 | `v-for` 必须带 `:key`、两者拆分、`data` 必须是函数 | Vue2 编译/响应式限制 |
| 其它 | `v-model:title="x"` 多绑定、`<Teleport>`、多根节点 Fragment | 单一根节点、普通 `v-model` | Vue 2.6 不支持 |

### 1.2 本次会话中修复的 Vue3 遗留

1. **深选择器（对应第三节 #4）**：132 处 `::v-deep(...)` → `::v-deep ...`（`ChatMessage` 96、`FilePreview` 34、`SettingsPanel` 1、`DocxPreview` 1）。
2. **模板作用域（对应第四节 #10）**：`SettingsPanel` 的模块级 `navItems`、`SessionList` 的 `APP_TITLE` 挂到 `data()`，修复「设置面板标签页消失」「侧边栏标题空白」。
3. **响应式（对应第三节 #6）**：shiki highlighter 由模块级普通变量改为「全局单例 + `Vue.observable({ ready: false })`」，渲染时读取该标记建立依赖，加载完成后自动重渲染，代码块不再停留在无高亮的兜底样式。
4. **生命周期与事件名**：组件统一使用 `mounted` / `beforeDestroy`；事件名统一 kebab-case（如 `@toggle-theme`、`@show-settings`、`@show-scheduled-tasks`）。

### 1.3 语法守卫脚本

`scripts/check-vue2-syntax.mjs`（`npm run lint:vue2`）持续校验：

- 用 `vue-template-compiler` 编译每个 SFC 的模板
- 扫描 Vue3 专属语法：`<script setup>`、`defineXxx`、`<Teleport>`、`v-model` 多绑定、`setup()`、`import { ref/computed } from 'vue'`
- 规则检查：`data` 必须是函数、`v-for` 必须绑定 `:key`、`v-if` 与 `v-for` 不得同元素、事件名必须 kebab-case

当前状态：扫描 35 个文件，全部通过。

---

## 二、登录 / 注册与登录态（免密登录）

| # | 改动 | 涉及文件 |
|---|---|---|
| 1 | 删除登录注册页 `Welcome.vue`，`App.vue` 移除登录页分支与 `showWelcome` 状态 | `components/Welcome.vue`（删除）、`App.vue` |
| 2 | 打开页面自动免密登录 `autoLogin()`；移除退出登录 / 注销入口与空闲自动登出逻辑；`api/auth.js` 仅保留 `authFetch`、`passwordlessLogin` | `App.vue`、`api/auth.js`、`SessionList.vue` |
| 3 | 登录账号改为代码内固定值：`FIXED_LOGIN_USERNAME = 'demo'`、`FIXED_LOGIN_USER_ID = '1001'`，不再读取 URL 参数 / localStorage | `App.vue` |

```js
// App.vue
// 免密登录固定账号（已移除登录页，直接在此模拟固定值，便于联调/演示）
const FIXED_LOGIN_USERNAME = 'demo'
const FIXED_LOGIN_USER_ID = '1001'
```

---

## 三、代码展示（流式对话 + 文件预览）

| # | 改动 | 涉及文件 |
|---|---|---|
| 4 | 修复代码块样式失效：Vue2 不支持 Vue3 的 `::v-deep(...)` 括号写法（编译成非法选择器被丢弃），批量改为 `::v-deep 选择器`，共 132 处 | `ChatMessage.vue`(96)、`FilePreview.vue`(34)、`SettingsPanel.vue`(1)、`DocxPreview.vue`(1) |
| 5 | 流式过程中代码被误判为数学公式：围栏保护正则改为 ```` ```[\s\S]*?(?:```\|$) ````，覆盖流式未闭合的代码块 | `markdownSetup.js` |
| 6 | 高亮器改造：shiki 改为全局单例 + `Vue.observable` 就绪标记（恢复响应式重渲染）+ 高亮结果缓存 + 兜底 `<pre class="shiki">` | `ChatMessage.vue` |
| 7 | 代码块统一「亮黑」配色；shiki 主题 `github-light → github-dark`；highlight.js 换 `github-dark.css`；Monaco 新增自定义主题 `easy-agent-dark` | `ChatMessage.vue`、`FilePreview.vue`、`CodePreview.vue` |

### 亮黑配色表

| 部位 | 颜色 |
|---|---|
| 代码区背景 | `#0d1117` |
| 头部 / 行号槽 | `#161b22` |
| 边框 | `#21262d`（深色场景 `#30363d`） |
| 代码正文 | `#c9d1d9` |
| 语言标签 / 行号 / 复制按钮 | `#8b949e` |
| 复制按钮底 / 悬停 | `#21262d` → `#30363d` |

### 关键修复点（4）说明

```css
/* 修复前（Vue2 编译后选择器非法，整条规则被浏览器丢弃） */
.message-text[data-v-xxxx] (.code-block-wrapper) { ... }

/* 修复后 */
.message-text[data-v-xxxx] .code-block-wrapper { ... }
```

---

## 四、主题与交互

| # | 改动 | 涉及文件 |
|---|---|---|
| 8 | 移除颜色主题功能，仅保留浅色：删除 398 条 `data-theme="dark"` 规则与 `SessionList` 的 `html.dark` 规则；浅色变量收进 `:root`；删除 `isDarkTheme` 状态、`toggleTheme()`、`mounted` 中设置 `data-theme`、设置面板「外观」页签及主题样式 | `App.vue`(331)、`ChatMessage.vue`(29)、`SettingsPanel.vue`(27)、`ChatInput.vue`(10)、`ScheduledTasksPanel.vue`(1)、`SessionList.vue`、`Chat.vue` |
| 9 | 停止对话后执行计划仍显示「执行中」：`TodoListPanel` 新增 `active` 属性，非流式时把 `in_progress` 归一为 `interrupted`（静态「已中断」标记 + 标题「已中断」标签）；`ChatMessage` 中无结果的工具显示「已中断，未返回结果」 | `TodoListPanel.vue`、`Chat.vue`、`ChatMessage.vue` |
| 10 | 设置面板记忆 / 提示词 / MCP 消失：修复 Vue2 模板作用域——模块级 `navItems` 挂到 `data()`；同类修复 `SessionList` 的 `APP_TITLE` | `SettingsPanel.vue`、`SessionList.vue` |

### Vue2 / Vue3 模板作用域差异（问题 10 根因）

Vue3 `<script setup>` 的顶层绑定会自动暴露给模板；Vue2 普通 `<script>` 的模块级常量不会（模板编译在独立模块，只能访问组件实例属性），必须挂到 `data()`：

```js
data() {
  return {
    navItems,        // 修复：暴露给模板
    activeTab: 'memory',
    ...
  }
}
```

---

## 五、API 层重构

| # | 改动 | 涉及文件 |
|---|---|---|
| 11 | 新建 `src/utils/request.js`（axios 统一封装） | 新增 |
| 12 | 删除 `src/api/request.js`；6 个 API 文件全部改为 `import request from '@/utils/request'`，统一多行配置对象写法并补齐 JSDoc；6 个组件的旧导入同步切换 | `api/*.js`、`AssetsPanel.vue`、`FilePreview.vue`、`PdfPreview.vue`、`DocxPreview.vue`、`ExcelPreview.vue`、`GeneratedFilesModal.vue` |

### `utils/request.js` 能力

- axios 实例：`baseURL` 取自 `config.js`、超时 30s；请求拦截器注入 `Authorization` 并派发用户活动事件；响应拦截器统一处理 401（清理登录态 + 派发 `AUTH_EXPIRED_EVENT`）。
- **默认导出 `request`**：返回响应体 `data`，可选第二参数自定义兜底错误提示。
- 具名导出：`requestRaw`、`requestText`、`requestBlob`、`requestArrayBuffer`、`streamUrl`、`streamHeaders`、`handleStreamResponse`，以及 `storeAuth` / `clearAuth` / `getStoredToken` / `getStoredUsername` / `getAuthHeaders` / `dispatchAuthExpired` 与两个事件常量。

### API 文件书写规范

```js
/**
 * 获取会话列表
 *
 * @param {string|null} [username=null] 用户名，不传时按当前登录用户
 * @returns {Promise<Array>} 会话列表
 * @example
 * listSessions('demo')
 */
export async function listSessions(username = null) {
  return request(
    {
      url: '/agent/sessions',
      method: 'get',
      params: username ? { username } : undefined,
    },
    '获取会话列表失败'
  )
}
```

---

## 六、功能裁剪

| # | 改动 | 涉及文件 |
|---|---|---|
| 13 | 去掉重置 / 修改密码：删除 `resetUserPassword()`；`UserManagementPanel` 移除重置按钮、`resetPassword()`、相关状态与样式 | `api/auth.js`、`UserManagementPanel.vue` |
| 14 | 删除「个人资料」与「用户管理」两个页面；清理 App 的模板 / 状态 / 方法 / 面板互斥条件，SessionList 下拉菜单移除对应入口；删除因此失效的 `listUsers()`、`updateUserProfile()` 与未使用的 `email` prop | 删除 `UserProfile.vue`、`UserManagementPanel.vue`；改 `App.vue`、`SessionList.vue`、`api/auth.js`、`api/files.js` |
| 15 | 移除 Tailwind：`style.css` 的 3 条 `@tailwind` 指令替换为等价的最小全局重置（约 130 行，保证 UI 不变）；删除 `tailwind.config.cjs`、`postcss.config.cjs` 中的 tailwind 插件、`package.json` 与 `package-lock.json` 中的依赖 | `style.css`、`postcss.config.cjs`、`tailwind.config.cjs`（删除）、`package.json`、`package-lock.json` |

### Tailwind 移除依据

- 模板中**没有**使用任何 Tailwind 原子类；实测生成 13.8 KB / 81 条规则，其中 31 条（`.flex` / `.absolute` / `.hidden` …）是扫描器把组件 CSS / JS 中的单词误判成候选产生的**死规则**；`@tailwind components` 因无插件输出为空。
- `@tailwind base`（Preflight）是唯一有实际影响的部分，故以等价精简重置替代，保留：盒模型与边框默认值、`margin` 归零、`h1~h6` 字号继承、`ol/ul` 无项目符号、表单控件字体继承、`button` 背景透明、`a` 颜色继承、`table` 边框合并、`placeholder` 颜色等。

---

## 七、校验记录

| 项目 | 结果 |
|---|---|
| `npm run lint:vue2` | 通过，扫描文件数 37 → 35（删除 2 个组件） |
| `npm run build` | 成功，无 `Module not found` / 编译错误 |
| 构建产物校验 | 代码块深选择器合法；无 `--tw-*` 变量（Tailwind 已彻底移除）；全局重置规则已打包进 `dist/js/app.js` |
| 残留引用检查 | `data-theme`、`Welcome`、`UserProfile`、`UserManagementPanel`、`resetUserPassword`、`listUsers`、`updateUserProfile`、`api/request.js`、`tailwind` 均无残留 |

---

## 八、尚未清理（范围外，按需处理）

- 后端：`easy_agent/api/auth.py` 中的 `reset-password` 路由与 `ResetPasswordRequest` 模型；`tests/api/test_auth.py` 中的重置密码用例。
- Vue3 版前端：`frontend/src/api/auth.js`、`frontend/src/components/UserManagementPanel.vue` 中仍有密码重置相关代码（本次改动仅覆盖 `frontend-vue2`）。
- `frontend-vue2/scripts/run-vite.mjs` 命名有误导性（实际调用 `@vue/cli-service`，非 Vite），`env-mode.mjs` 注释中亦有「Vite」残留描述。
