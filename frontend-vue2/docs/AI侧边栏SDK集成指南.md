# Easy Agent AI 侧边栏 SDK 集成指南

`@easy-agent/ai-sidebar` 把 Easy Agent 的 AI 侧边栏封装成一个独立的 JS 文件，第三方平台引入后调用一次 `init()`，就能在任意页面右侧获得一个与 Easy Agent 主站功能一致的 AI 助手：

- 流式对话、思考过程、工具调用卡片、停止 / 重试；
- 历史会话（与 Easy Agent 主站是**同一份会话**，可互相打开继续）；
- 输入 `/` 调用技能，MCP 连接器与技能均使用 Easy Agent 后端的配置；
- 自动附带宿主页面上下文（页面名称、业务数据），同一页面再次唤起时优先回到该页面的会话；
- 悬浮 / 固定两种模式、拖动左边缘调整宽度、折叠为窄条、划词「问 AI」、`Ctrl/⌘ + J` 快捷键。

SDK 自带 Vue 2 运行时，**宿主页面不限技术栈**（原生 HTML、jQuery、Vue 2/3、React、Angular 均可）；界面渲染在 Shadow DOM 中，宿主页面的 CSS 与侧边栏样式**互不影响**。

---

## 目录

1. [前置条件](#1-前置条件)
2. [获取 SDK](#2-获取-sdk)
3. [快速开始（script 标签）](#3-快速开始script-标签)
4. [通过 npm 引入（各框架示例）](#4-通过-npm-引入各框架示例)
5. [登录鉴权](#5-登录鉴权)
6. [页面上下文](#6-页面上下文)
7. [配置项](#7-配置项)
8. [实例方法](#8-实例方法)
9. [常见场景](#9-常见场景)
10. [样式、层级与布局](#10-样式层级与布局)
11. [常见问题](#11-常见问题)
12. [构建与发布（SDK 维护者）](#12-构建与发布sdk-维护者)

---

## 1. 前置条件

| 项目 | 说明 |
| --- | --- |
| Easy Agent 后端 | 已部署并可从用户浏览器访问，下文以 `https://agent.example.com` 为例 |
| 跨域（CORS） | 后端默认放行所有来源。生产环境如设置了 `EASY_CORS_ALLOW_ORIGINS`，需把宿主平台域名加进去，例如 `EASY_CORS_ALLOW_ORIGINS=https://erp.example.com` |
| 浏览器 | 支持 Shadow DOM 的现代浏览器（Chrome / Edge / Firefox / Safari 近两年版本） |
| 登录方式 | 三选一：宿主后端换取 token（推荐）、免密登录、与主站同域共享登录态，见 [登录鉴权](#5-登录鉴权) |

> 若宿主平台与 Easy Agent 后端不同域，且宿主页面启用了 CSP，需要在 `connect-src` 中加入后端地址。

---

## 2. 获取 SDK

产物是单个 JS 文件，CSS 已内联，无需再引入样式文件。

| 文件 | 用途 |
| --- | --- |
| `easy-agent-sidebar.umd.min.js` | `<script>` 直接引入，挂载全局变量 `EasyAgentSidebar`（约 1.1 MB，gzip 后约 350 KB） |
| `easy-agent-sidebar.common.js` | 打包器（webpack / Vite / Rollup）通过 `import` / `require` 引入 |
| `fonts/` | KaTeX 数学公式字体，按需加载 |
| `index.d.ts` | TypeScript 类型声明 |

三种获取方式：

```bash
# ① 从 npm 私服安装（发布方式见第 12 节）
npm install @easy-agent/ai-sidebar

# ② 直接拿构建产物：在 Easy Agent 仓库中构建，产物位于 frontend-vue2/dist-sdk/
cd frontend-vue2 && npm run build:sdk

# ③ 把 dist-sdk/ 整个目录放到静态服务器 / CDN，例如：
#    https://cdn.example.com/easy-agent-sidebar/1.0.1/easy-agent-sidebar.umd.min.js
```

---

## 3. 快速开始（script 标签）

在宿主页面 `</body>` 之前加入：

```html
<script src="https://cdn.example.com/easy-agent-sidebar/1.0.1/easy-agent-sidebar.umd.min.js"></script>
<script>
  const sidebar = EasyAgentSidebar.init({
    // Easy Agent 后端地址
    baseURL: 'https://agent.example.com',
    // 登录：使用宿主后端换取的 token（见第 5 节）
    auth: {
      getToken: () => fetch('/api/easy-agent/token').then(r => r.json()).then(d => d.token),
    },
    // 页面上下文：每次打开侧边栏时调用，随消息提供给 AI
    pageContext: () => ({
      title: '订单管理',
      description: '用户正在查看订单列表',
      suggestions: ['哪些订单需要优先处理？', '汇总本页订单金额'],
      content: document.querySelector('#order-table').innerText.slice(0, 3000),
    }),
    // 可选：「在主站打开」按钮跳转的 Easy Agent 前端地址
    mainAppUrl: 'https://agent.example.com/',
  })
</script>
```

完成后页面右侧出现「问 AI」按钮，点击或按 `Ctrl/⌘ + J` 打开侧边栏。

完整可运行示例见产物中的 `demo.html`：

```bash
cd dist-sdk && python3 -m http.server 5181
# 浏览器打开（免密用户 sdk_demo，需后端可免密登录）：
# http://localhost:5181/demo.html?baseURL=http://localhost:8080&username=sdk_demo
```

---

## 4. 通过 npm 引入（各框架示例）

```bash
npm install @easy-agent/ai-sidebar
```

SDK 是**单例**：同一页面只初始化一次，建议放在应用根组件或入口文件中，在用户登录宿主平台后调用。

### 4.1 原生 JS / jQuery（打包器）

```js
import { init } from '@easy-agent/ai-sidebar'

const sidebar = init({
  baseURL: 'https://agent.example.com',
  auth: { token: window.__EASY_AGENT_TOKEN__ },
})

document.querySelector('#ask-ai').addEventListener('click', () => {
  sidebar.open({ prompt: '帮我解读当前报表' })
})
```

### 4.2 Vue 3

```vue
<!-- App.vue -->
<script setup>
import { onMounted, onBeforeUnmount, watch } from 'vue'
import { useRoute } from 'vue-router'
import { init } from '@easy-agent/ai-sidebar'

const route = useRoute()
let sidebar = null

onMounted(() => {
  sidebar = init({
    baseURL: import.meta.env.VITE_EASY_AGENT_URL,
    auth: { getToken: fetchEasyAgentToken },
    pageContext: () => ({
      key: route.name,
      title: route.meta.title,
      description: route.meta.description,
    }),
  })
})

// SPA 路由切换后刷新上下文（让「当前页面会话」跟随路由）
watch(() => route.fullPath, () => {
  sidebar && sidebar.setPageContext(() => ({ key: route.name, title: route.meta.title }))
})

onBeforeUnmount(() => sidebar && sidebar.destroy())

async function fetchEasyAgentToken() {
  const res = await fetch('/api/easy-agent/token')
  return (await res.json()).token
}
</script>
```

> 宿主即使是 Vue 3 也没有冲突：SDK 内置的是独立的 Vue 2 运行时，渲染在 Shadow DOM 中。

### 4.3 Vue 2

```js
// main.js
import Vue from 'vue'
import App from './App.vue'
import router from './router'
import { init } from '@easy-agent/ai-sidebar'

new Vue({ router, render: h => h(App) }).$mount('#app')

const sidebar = init({
  baseURL: process.env.VUE_APP_EASY_AGENT_URL,
  auth: { getToken: () => store.dispatch('easyAgent/fetchToken') },
  pageContext: () => ({
    key: router.currentRoute.name,
    title: router.currentRoute.meta.title,
  }),
})

router.afterEach(to => {
  sidebar.setPageContext({ key: to.name, title: to.meta.title })
})

// 在任意组件中使用：Vue.prototype.$aiSidebar = sidebar → this.$aiSidebar.open()
Vue.prototype.$aiSidebar = sidebar
```

### 4.4 React

```jsx
// AiSidebarProvider.jsx
import { createContext, useContext, useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'
import { init } from '@easy-agent/ai-sidebar'

const SidebarContext = createContext(null)
export const useAiSidebar = () => useContext(SidebarContext)

export function AiSidebarProvider({ token, children }) {
  const ref = useRef(null)
  const location = useLocation()

  useEffect(() => {
    if (!token) return
    ref.current = init({
      baseURL: process.env.REACT_APP_EASY_AGENT_URL,
      auth: { token },
    })
    return () => ref.current && ref.current.destroy()
  }, [token])

  useEffect(() => {
    ref.current && ref.current.setPageContext({ key: location.pathname, title: document.title })
  }, [location.pathname])

  return <SidebarContext.Provider value={ref}>{children}</SidebarContext.Provider>
}

// 任意组件中：
// const sidebar = useAiSidebar()
// <button onClick={() => sidebar.current.send('总结这份合同的风险条款')}>AI 审阅</button>
```

### 4.5 Angular

```ts
// ai-sidebar.service.ts
import { Injectable, OnDestroy } from '@angular/core'
import { Router, NavigationEnd } from '@angular/router'
import { filter } from 'rxjs/operators'
import { init, EasyAgentSidebarInstance } from '@easy-agent/ai-sidebar'

@Injectable({ providedIn: 'root' })
export class AiSidebarService implements OnDestroy {
  private sidebar?: EasyAgentSidebarInstance

  constructor(private router: Router) {}

  start(token: string) {
    this.sidebar = init({ baseURL: 'https://agent.example.com', auth: { token } })
    this.router.events
      .pipe(filter(e => e instanceof NavigationEnd))
      .subscribe(() => this.sidebar?.setPageContext({ key: this.router.url, title: document.title }))
  }

  ask(question: string) {
    return this.sidebar?.send(question)
  }

  ngOnDestroy() {
    this.sidebar?.destroy()
  }
}
```

---

## 5. 登录鉴权

侧边栏以 Easy Agent 用户身份调用后端，会话、技能、记忆均按该用户隔离。通过 `auth` 选择一种方式：

| 方式 | 配置 | 适用场景 |
| --- | --- | --- |
| **宿主后端换取 token（推荐）** | `auth: { getToken }` 或 `auth: { token }` | 生产环境，身份由宿主平台担保 |
| 免密登录 | `auth: { username, userId }` | 内网 / 测试环境，后端允许免密登录 |
| 共享主站登录态 | `auth: { shareMainAppLogin: true }` | 宿主页面与 Easy Agent 主站**同域**部署 |

### 5.1 宿主后端换取 token（推荐）

浏览器不直接接触任何凭证，由宿主平台后端以当前登录用户的身份向 Easy Agent 换取 token，再交给前端：

```js
// 宿主平台后端（Node.js / Express 示例，其他语言同理）
app.get('/api/easy-agent/token', requireLogin, async (req, res) => {
  const resp = await fetch('https://agent.example.com/agent/auth/login-passwordless', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // username：宿主平台中的用户唯一标识；user_id：工号等，新用户传 '0' 由后端生成
    body: JSON.stringify({ username: req.user.account, user_id: req.user.employeeNo || '0' }),
  })
  if (!resp.ok) return res.status(502).json({ message: '获取 AI 助手凭证失败' })
  const data = await resp.json()
  res.json({ token: data.access_token })
})
```

```js
// 宿主平台前端
EasyAgentSidebar.init({
  baseURL: 'https://agent.example.com',
  auth: {
    // token 过期时 SDK 会自动再次调用 getToken 续期
    getToken: () => fetch('/api/easy-agent/token').then(r => r.json()).then(d => d.token),
  },
})
```

> 建议在 Easy Agent 后端前置网关，仅允许宿主平台服务器调用 `/agent/auth/login-passwordless`，避免任何人凭用户名直接登录。

### 5.2 免密登录

```js
EasyAgentSidebar.init({
  baseURL: 'https://agent.example.com',
  auth: { username: 'zhangsan', userId: 'E10086' },
})
```

- 用户不存在时由后端自动注册；`admin` 不支持免密登录。
- 后端登录是**单点登录**：每次登录都会让该用户此前的 token 失效。SDK 会把 token 保存在 `localStorage`，同一用户再次打开页面时直接复用，不会重复登录；但若该用户同时在 Easy Agent 主站登录，两边会互相挤下线——生产环境请使用 5.1 的方式，或与主站同域共享登录态。

### 5.3 与主站同域共享登录态

宿主页面与 Easy Agent 主站同域（例如主站在 `https://portal.example.com/agent/`，宿主在 `https://portal.example.com/erp/`）时，可直接复用用户在主站的登录：

```js
EasyAgentSidebar.init({
  baseURL: '',               // 同域，使用相对路径
  auth: { shareMainAppLogin: true },
})
```

### 5.4 token 过期与登出

```js
const sidebar = EasyAgentSidebar.init({
  baseURL: 'https://agent.example.com',
  auth: { token: initialToken },
  // 使用静态 token 时，过期后 SDK 无法自行续期，会回调这里
  onAuthExpired: async () => {
    const { token } = await fetch('/api/easy-agent/token').then(r => r.json())
    await sidebar.setToken(token)
  },
})

// 宿主平台退出登录时：清除 SDK 保存的登录态并移除侧边栏
function onHostLogout() {
  sidebar.logout()
}
```

---

## 6. 页面上下文

页面上下文让 AI 知道「用户在哪个页面、看着什么数据」，会显示为输入框上方的标签（用户可移除），并在发送时附带给 AI；同一会话中上下文未变化时不重复发送。

```js
EasyAgentSidebar.init({
  // ...
  pageContext: {
    key: 'contract-detail',               // 页面标识：同一 key 再次唤起时回到该页面最近的会话
    title: '合同详情',                     // 显示在标签与欢迎语中
    description: '用户正在审阅一份采购合同',  // 页面用途
    suggestions: ['列出合同中的风险条款', '对比标准模板的差异'], // 欢迎页快捷提问（最多 4 条）
    content: '合同编号：HT-2026-0012\n甲方：……\n金额：……', // 业务数据（纯文本）
  },
})
```

`pageContext` 支持三种形式：

| 形式 | 说明 |
| --- | --- |
| 对象 | 如上，字段均可选 |
| 字符串 | 等同于 `{ content: '…' }` |
| 函数 | 返回对象或字符串；SDK 在每次打开侧边栏、调用 `open()` / `send()`、浏览器前进后退时重新求值，适合内容随页面变化的场景 |

- 默认 `key` 为 `location.pathname`，默认 `title` 为 `document.title`。
- SPA 路由切换后调用 `sidebar.setPageContext(...)` 更新（见第 4 节各框架示例）。
- `content` 建议控制在 4000 字以内（后端单条上下文上限 8000 字），只放与提问相关的关键数据，**不要放密码、身份证号等敏感信息**。
- 只想为某一次提问补充信息时，用 `sidebar.open({ context: '…' })`。

---

## 7. 配置项

| 配置项 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `baseURL` | `string` | `''` | Easy Agent 后端地址；为空时以相对路径请求当前域名 |
| `auth` | `object` | — | 登录方式，见第 5 节：`token` / `getToken` / `username` + `userId` / `shareMainAppLogin` |
| `pageContext` | `object \| string \| function` | — | 页面上下文，见第 6 节 |
| `mainAppUrl` | `string` | — | Easy Agent 主站地址；配置后显示「在主站打开」，新标签页打开 `mainAppUrl?session=<会话ID>` |
| `features` | `object` | 全部开启 | 功能开关：`launcher` 唤起按钮、`selectionAsk` 划词提问、`shortcut` 快捷键、`dock` 固定模式、`openInMain` 在主站打开 |
| `defaultMode` | `'float' \| 'dock'` | `'float'` | 初始模式（用户切换后以用户选择为准） |
| `launcherLabel` | `string` | `'问 AI'` | 唤起按钮文字 |
| `welcomeTitle` | `string` | `'有什么可以帮你？'` | 欢迎页标题 |
| `theme` | `object` | — | 颜色令牌，见第 10 节 |
| `zIndex` | `number` | `3000` | 侧边栏层级 |
| `container` | `string \| HTMLElement` | `document.body` | 挂载容器 |
| `dockTarget` | `string \| HTMLElement \| false` | `document.body` | 固定模式下设置 `margin-right` 让出空间的元素；`false` 表示不处理，由 `onLayoutChange` 自行布局 |
| `assetsBaseURL` | `string` | 脚本所在目录 | 字体资源目录。通过打包器引入时如需数学公式字体，指向 `dist-sdk/` 的部署地址 |
| `onReady` | `(sidebar) => void` | — | 登录成功、会话加载完成 |
| `onError` | `(error) => void` | — | 初始化失败（如登录失败、后端不可达） |
| `onOpen` / `onClose` | `() => void` | — | 侧边栏打开 / 关闭 |
| `onAuthExpired` | `(error) => void` | — | 登录过期且无法自动续期 |
| `onOpenInMain` | `(sessionId) => void` | — | 自定义「在主站打开」的行为（配置后优先于 `mainAppUrl`） |
| `onLayoutChange` | `({ dockWidth }) => void` | — | 固定模式占用宽度变化（关闭或悬浮时为 0） |

---

## 8. 实例方法

`init()` 立即返回实例；登录和会话加载在后台完成。初始化完成前调用的方法会自动排队，完成后再执行。

| 方法 | 说明 |
| --- | --- |
| `open(options?)` | 打开侧边栏。`options.prompt` 预填输入框（不发送）、`options.context` 本次额外上下文、`options.newSession` 新建会话 |
| `close()` | 关闭侧边栏 |
| `toggle()` | 打开 / 关闭 |
| `isOpen()` | 是否已打开 |
| `send(text)` | 打开侧边栏并立即发送一条消息（正在生成时改为预填输入框） |
| `setPageContext(ctx)` | 更新页面上下文 |
| `setToken(token)` | 更新 token（宿主自行续期后调用） |
| `logout()` | 清除 SDK 保存的登录态并销毁侧边栏 |
| `destroy()` | 销毁侧边栏、移除 DOM、恢复 `dockTarget` 样式 |
| `ready` | `Promise`，初始化完成后 resolve，失败时 reject |
| `version` | SDK 版本号 |

```js
const sidebar = EasyAgentSidebar.init({ /* ... */ })

await sidebar.ready                                   // 等待就绪（可选）
sidebar.open({ prompt: '帮我写一份周报', newSession: true })
sidebar.send('解释一下这个报错：' + errorMessage)
sidebar.setPageContext({ title: '客户详情', content: customerSummary })
```

---

## 9. 常见场景

### 9.1 用业务按钮代替右侧悬浮按钮

```js
const sidebar = EasyAgentSidebar.init({
  baseURL: 'https://agent.example.com',
  auth: { getToken },
  features: { launcher: false },     // 隐藏右侧「问 AI」按钮
})

document.querySelector('#toolbar-ai').onclick = () => sidebar.toggle()
```

### 9.2 表格行「AI 分析」

```js
function analyzeRow(row) {
  sidebar.open({
    newSession: true,
    context: `订单号：${row.no}\n客户：${row.customer}\n金额：${row.amount}\n状态：${row.status}`,
    prompt: '分析这笔订单的风险，并给出处理建议',
  })
}
```

### 9.3 报错一键求助

```js
window.addEventListener('error', (e) => {
  showToast('页面出错了', {
    action: '问 AI',
    onAction: () => sidebar.send(`页面报错：${e.message}\n位置：${e.filename}:${e.lineno}`),
  })
})
```

### 9.4 统一品牌色与文案

```js
EasyAgentSidebar.init({
  // ...
  launcherLabel: '智能助手',
  welcomeTitle: '我是 ERP 智能助手，有什么可以帮你？',
  theme: {
    '--accent-color': '#7c3aed',
    '--primary-color': '#7c3aed',
  },
})
```

### 9.5 宿主自己处理固定模式布局

页面主体不是 `body` 直接撑开（例如布局容器是 `#layout-main`），或使用了 `right: 0` 的固定顶栏时：

```js
EasyAgentSidebar.init({
  // ...
  dockTarget: false,
  onLayoutChange: ({ dockWidth }) => {
    document.querySelector('#layout-main').style.marginRight = dockWidth + 'px'
    document.querySelector('.fixed-header').style.right = dockWidth + 'px'
  },
})
```

---

## 10. 样式、层级与布局

- **样式隔离**：侧边栏渲染在 Shadow DOM（`<div data-easy-agent-sidebar>` 的影子根）中，宿主的全局 CSS（如 `* { box-sizing }`、Tailwind / Bootstrap 重置）不会影响侧边栏，侧边栏样式也不会泄漏到宿主页面。唯一注入宿主 `<head>` 的是 KaTeX 字体的 `@font-face` 声明（浏览器限制，影子根内声明的字体不生效）和按钮悬浮提示的样式（类名前缀 `ea-tip`）。
- **颜色令牌**：通过 `theme` 覆盖，可用令牌如下：

  | 令牌 | 默认值 | 用途 |
  | --- | --- | --- |
  | `--accent-color` | `#0ea5e9` | 强调色（按钮、标签、高亮） |
  | `--primary-color` | `#0ea5e9` | 主色 |
  | `--bg-primary` | `#f8fafc` | 页面底色 |
  | `--bg-secondary` | `#ffffff` | 面板底色 |
  | `--bg-tertiary` | `#f1f5f9` | 次级底色 |
  | `--bg-surface` | `#ffffff` | 卡片底色 |
  | `--text-primary` | `#1e293b` | 主文字 |
  | `--text-secondary` | `#64748b` | 次要文字 |
  | `--border-color` | `#e2e8f0` | 边框 |

- **层级**：默认 `zIndex: 3000`。若宿主弹窗需要盖住侧边栏，把 `zIndex` 调低；若侧边栏被宿主固定顶栏遮挡，调高即可。
- **两种模式**：
  - 悬浮：浮在页面右侧，不改变宿主布局；
  - 固定：贴在视口右侧，并给 `dockTarget`（默认 `body`）设置 `margin-right` 让出空间。
- **小屏**：视口宽度小于 768px 时自动全屏悬浮显示。
- **偏好记忆**：模式、宽度、唤起按钮位置、每个页面最近的会话保存在宿主域名的 `localStorage`（键名前缀 `easy_agent_`）。

---

## 11. 常见问题

**Q：控制台报 CORS 错误？**
后端设置了 `EASY_CORS_ALLOW_ORIGINS` 但没有包含宿主域名。把宿主域名（含协议和端口）加进去并重启后端。

**Q：初始化失败，提示「未登录」？**
没有配置 `auth`，且本地没有可复用的 token。按第 5 节选择一种登录方式。

**Q：用户在 Easy Agent 主站被挤下线？**
使用了免密登录（`auth.username`），而后端是单点登录。改用宿主后端换取 token（第 5.1 节），或与主站同域共享登录态（第 5.3 节）。

**Q：在侧边栏里看不到主站的技能 / 历史会话？**
确认两边是同一个 Easy Agent 用户：免密登录的 `username`、宿主后端换 token 时用的 `username`，要与用户在主站登录的用户名一致。

**Q：同一页面能初始化多个侧边栏吗？**
不能。SDK 为单例，重复调用 `init()` 会返回已有实例；需要重建时先 `destroy()`。

**Q：数学公式显示为普通字体？**
通过打包器引入时 SDK 无法推断字体位置。把 `dist-sdk/fonts/` 部署到静态服务器，并设置 `assetsBaseURL: 'https://cdn.example.com/easy-agent-sidebar/1.0.1/'`。

**Q：固定模式下宿主顶栏没有让出空间？**
`position: fixed; right: 0` 的元素不受 `body` 的 `margin-right` 影响，参照第 9.5 节自行处理。

**Q：页面切换后侧边栏仍显示上一个页面的上下文？**
SPA 路由切换不会触发页面刷新，需在路由变化时调用 `sidebar.setPageContext(...)`，或把 `pageContext` 配置为函数。

---

## 12. 构建与发布（SDK 维护者）

源码位于 Easy Agent 仓库 `frontend-vue2/`：

| 路径 | 说明 |
| --- | --- |
| `src/sdk/index.js` | SDK 入口：Shadow DOM 挂载、鉴权、页面上下文、实例 API |
| `src/components/AiSidebar.vue` | 侧边栏组件（主站与 SDK 共用，SDK 以 `embedded` 模式使用） |
| `sdk/` | npm 包模板：`package.json`（版本号在此维护）、`index.d.ts`、`demo.html` |
| `scripts/build-sdk.mjs` | 构建脚本 |
| `docs/AI侧边栏SDK集成指南.md` | 本文档，构建时复制为包内 `README.md` |

```bash
cd frontend-vue2

# 1. 修改 sdk/package.json 中的 version
# 2. 构建：产物输出到 dist-sdk/（已在 .gitignore 中）
npm run build:sdk

# 3. 本地验证：dist-sdk/demo.html
cd dist-sdk && python3 -m http.server 5181
# http://localhost:5181/demo.html?baseURL=http://localhost:8080&username=sdk_demo

# 4. 发布到 npm 私服
npm publish --registry https://npm.example.com/
```

构建要点：

- vue-cli lib 模式打包，内联 Vue 2 运行时（`--inline-vue`），宿主无需安装 Vue；
- 构建后把抽取出的 CSS 以字符串回填进 JS（SDK 运行时注入影子根），因此产物是单文件；
- SDK 构建中 `highlight.js` 使用官方常用语言子集 `highlight.js/lib/common`，主站不受影响；
- 主站 `App.vue` 支持 `?session=<会话ID>` 打开指定会话，供「在主站打开」使用。

### Node 14 兼容

SDK 的构建与使用都支持 Node 14（`sdk/package.json` 中 `engines.node` 为 `>=14`）：

```bash
# 用 Node 14 构建（nvm 示例）
nvm use 14
cd frontend-vue2 && npm run build:sdk
```

| 方面 | 处理方式 |
| --- | --- |
| 构建脚本 | `scripts/build-sdk.mjs` 不使用 `import.meta.dirname`（Node 20.11+）与 `node:` 前缀导入 |
| 文件图标 | `unplugin-icons` 23 与 `@iconify/utils` 为纯 ESM，Node 14 无法加载；SDK 构建改用 `scripts/iconify-raw-webpack.cjs`，从 `@iconify/json` 生成与原来一致的 SVG 字符串（主站构建不变） |
| 产物语法 | 依赖全部经 Babel 转译（`transpileDependencies: true`），目标 Chrome 64 / Edge 79 / Firefox 67 / Safari 12，terser 压缩限制为 `ecma: 2018`，产物不含 `?.`、`??` 等 ES2020 语法 |

因此 Node 14 时代的宿主工程（webpack 4 / vue-cli 4，其解析器 acorn 6 最高只识别 ES2019）可以直接 `import EasyAgentSidebar from '@easy-agent/ai-sidebar'` 打包，无需为 SDK 额外配置 Babel。

> 主站（`npm run dev` / `npm run build`）仍需 Node 20：`scripts/run-vite.mjs`、`unplugin-icons` 等依赖要求较新的 Node。
