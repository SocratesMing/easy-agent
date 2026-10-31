# Easy Agent 业务模块：接入与同步指南

适用：外网 `frontend-vue2/`（Vue 2.7 + Vue CLI 5）维护的 Easy Agent 前端，以路由页面形式接入内网 Vue 2 宿主。
本文随迁移包分发（迁移包里的 `integration/README.md`）。适配合同版本：**1**。

## 0. 一页速览

**以后怎么同步**（首次接入完成后）：

1. 外网：`npm run check:easy-agent` → `npm run export:easy-agent -- --baseline <上一版 manifest.json 或宿主的 .easy-agent-sync.json>`
2. 把生成的迁移包目录（或 `--archive` 打的 `.tar.gz`）带到内网
3. 内网：`node <迁移包>/integration/tools/sync-easy-agent.cjs --package <迁移包> --target <宿主项目根>`（预览）
   → 确认后加 `--apply` → 宿主构建 + 冒烟 → 提交 `src/packages/easyAgent/`（含 `.easy-agent-sync.json`）
4. 预览里出现「依赖有变化」或「适配合同版本变化」时，先按 `CHANGES.md` 处理宿主 `package.json` / 适配器

**每次实际拷贝的只有迁移包一个目录**；同步工具只写 `src/packages/easyAgent/`，不碰其他任何文件。

**首次接入还要做**（只做一次，见第 3 节）：核对版本前提、按依赖清单补包、复制 `host-template/` 里的
适配器与页面包装、加路由、合并 `vue.config.js` 增量、给页面父容器确定高度、配置反向代理、处理旧目录。

## 1. 结构与归属边界

```text
外网 frontend-vue2/
  src/packages/easyAgent/        受管业务模块（外网权威，整体同步到宿主同一路径）
    index.js / sidebar.js        稳定公开入口（路由页面 / SDK）
    EasyAgentPage.vue            业务根组件（原 App.vue）
    runtime/                     适配合同：config / events / navigation / theme
    api/ features/ shared/ utils/ directives/ styles/ assets/
    dependencies.json README.md
  src/App.vue  src/main.js       外网独立应用的薄启动壳
  src/standalone/                独立应用适配器 adapter.js 与页面级样式 global.css
  src/sdk/index.js               AI 侧边栏 SDK 构建入口（复用 sidebar.js）
  integration/                   接入模板、同步工具、验证样例与本文（不属于运行时）
  scripts/easy-agent/            check / export / 图标、样式、依赖清单生成
```

| 宿主路径 | 归属 | 日常同步 |
| --- | --- | --- |
| `src/packages/easyAgent/**` | 外网 | 同步工具更新；本地改动按冲突处理，默认不覆盖 |
| `src/packages/easyAgent/.easy-agent-sync.json` | 同步工具写入（请提交到宿主仓库） | 记录当前已应用的迁移包 |
| `src/integrations/easyAgent.js` | 宿主（首次从 `host-template/` 复制） | 不覆盖 |
| `src/pages/easyAgent/index.vue` | 宿主（首次从 `host-template/` 复制） | 不覆盖 |
| 路由、`main.js`、`App.vue`、Vuex、公共 request、全局样式、`package.json`、锁文件、构建配置 | 宿主 | 不覆盖；首次接入按本文做增量修改 |

模块内部只用相对路径，只引用 `dependencies.json` 里声明的 npm 包，不依赖宿主的 `@/` alias、全局注册、
`main.js` 初始化或任何特殊 loader（`npm run check:easy-agent` 强制检查）。

## 2. 适配合同（`configureEasyAgent`）

在 `EasyAgentPage` 首次渲染前调用（宿主模板里由页面包装先 `import '@/integrations/easyAgent'`）。
可重复调用，按段浅合并；模块在**使用时**读取配置，不在 import 时固化 `window` / `env`。

| 字段 | 默认（embedded） | 说明 |
| --- | --- | --- |
| `mode` | — | `'embedded'`（宿主路由页面）/ `'standalone'`（外网独立应用）；决定下面几项的默认值 |
| `api.baseURL` | `''` | 拼在所有模块请求前：`/agent/...`、`/api/knowledge/v1/...`、`/api/personnel/...`、SSE、下载 / 预览链接、XHR 上传 |
| `api.resolveUrl(path)` | — | 需要按前缀分别改写时使用（优先于 `baseURL`）；绝对地址原样返回 |
| `api.headers()` | — | 每个模块请求附加的头（网关头）；其中的 `Authorization` 会被忽略 |
| `api.withCredentials` | `false` | 跨域直连且后端用 Cookie 时开启 |
| `auth.tokenKey` / `usernameKey` | `mini_agent_token` / `mini_agent_username` | Agent token 的 localStorage 键（与宿主自己的 token 分开） |
| `auth.loginPage` | `false` | 无登录态时是否显示模块自带的登录 / 注册页；关闭时显示「未授权访问」 |
| `auth.urlLogin` | `false` | 地址栏 `?username=&user_id=` 免密直登（仅外网开发联调） |
| `auth.signIn(helpers)` | — | 用宿主身份换 Agent token：`helpers.passwordlessLogin(username, userId)` / `helpers.setToken(token, username)`；返回 `false`=未登录，`true`=已拿 token（模块调 `/agent/auth/profile`），`{ profile }`=已拿 token 并提供展示资料 |
| `auth.refresh()` | 同 `signIn` | 401 时的续期；并发 401 合并为一次，原请求最多重发一次；登录类接口与「被顶下线」不续期 |
| `auth.onExpired({ reason })` | — | Agent 登录态失效通知（`expired` / `kicked`）；**只代表 Agent**，不要据此登出宿主 |
| `app.title` / `welcomeTitle` | `Easy Agent` / `Easy Agent，让工作化繁为简` | 侧栏标题、首页欢迎语（后端 `app_welcome_title` 仍可覆盖） |
| `app.setDocumentTitle` | `false` | 是否写 `document.title` |
| `navigation.getQuery(vm)` / `replaceQuery(vm, patch)` | 不读写地址 | 模块只读写 `session` / `panel` 两个 query 键；`createVueRouterNavigation()` 给出 vue-router 3 实现（保留 path、hash、其他 query，`replace` 不加历史） |
| `theme.mode` / `theme.colors` | `light` / `{}` | 颜色令牌写在模块根节点上，不写宿主 `<html>` |
| `features.aiSidebar` | `true` | 页面内的 AI 侧边栏（悬浮按钮贴视口右缘） |
| `features.exposeGlobal` | `false` | 是否挂 `window.EasyAgentAI` |

公开入口（`@/packages/easyAgent`）：`EasyAgentPage`、`configureEasyAgent`、`getEasyAgentConfig`、
`createVueRouterNavigation`、`EASY_AGENT_CONTRACT_VERSION`、`EASY_AGENT_API_PREFIXES`、`DEFAULT_COLORS`、
`readStoredThemeColors`、`setThemeColors`。

**生命周期与后台流策略**

- 离开路由（组件销毁）：断开本页的 SSE 连接，**不**调用 `/agent/chat/cancel`；后端照常生成并落库；
  再次进入时按 `sessionStorage` 里的流式状态查询后端并重新挂载（已在浏览器验证）。
- `keep-alive` 缓存：实例保留，流照常接收；`Ctrl+J`、划词提问等全局监听在 `deactivated` 时卸下，
  `activated` 时挂回；悬浮提示收起。
- 用户点「停止」：与原行为一致，断开并通知后端取消。
- 同一页面（一个打包产物）同时只支持**一个**活动的 `EasyAgentPage` 实例：第二个实例只显示提示、不初始化
  （会话列表、流式状态是模块级单例）。页面与页面内 AI 侧边栏共享会话状态；独立构建的 SDK 是另一份副本，
  配置与 token 互不影响（已在同一页面验证）。

## 3. 首次接入步骤

1. **版本前提**：Vue 2.7.x（模块用 2.7 内置 Composition API，2.6 不行）；webpack 5；Vue CLI 5（vue-loader ≥ 15.10）。
   已用 Vue 2.7.14 + webpack 5.96.1 + Vue CLI 5.0.8 + Element UI 2.15.13 在 Node 14.20.0 / npm 6.14.17 下实测，见第 8 节。
2. **依赖**：按第 6 节表格补齐宿主 `package.json`（工具不会装包）。axios 需要 1.x。
3. **同步模块**：
   ```bash
   node <迁移包>/integration/tools/sync-easy-agent.cjs --package <迁移包> --target <宿主根>
   node <迁移包>/integration/tools/sync-easy-agent.cjs --package <迁移包> --target <宿主根> --apply
   ```
   宿主若已有 `src/packages/easyAgent/` 下的同名文件（例如之前手工拷过），预览会列为冲突；确认后 `--force`（会先备份）。
4. **复制模板**（之后归宿主维护）：
   - `host-template/src/integrations/easyAgent.js` → `src/integrations/easyAgent.js`（按内网改身份来源、后端地址、标题）
   - `host-template/src/pages/easyAgent/index.vue` → `src/pages/easyAgent/index.vue`
   - `host-template/src/router/easyAgent.routes.js` 的路由并入宿主路由表（懒加载）
   - `host-template/vue.config.easyAgent.js` 的 `devServer` 代理并入宿主 `vue.config.js`
5. **ESLint**：宿主若开了 `lintOnSave`，把 `src/packages/easyAgent/` 加入 `.eslintignore`（代码质量在外网把关）；
   宿主若用 jest 等测试框架扫描 `src/**/*.test.js`，迁移包不含测试文件，无需处理。
6. **布局高度**：包住 `<router-view>` 的元素要有确定高度（如 `height: calc(100vh - 顶栏高度)`，或 flex 列里
   `flex: 1; min-height: 0`）。模块根节点 `width/height: 100%`，不再用 `100vw`。
7. **反向代理（生产）**：`/agent`、`/api/knowledge`、`/api/personnel` 三个前缀反代到 Agent 后端；SSE 关闭缓冲
   （`proxy_buffering off; proxy_read_timeout 3600s;`）。宿主若有统一登录 SDK 全局劫持 XHR / fetch，需对这三个前缀放行
   （模块导出 `EASY_AGENT_API_PREFIXES`）。
8. **旧目录**（`src/api/easyAgent`、`src/utils/easyAgent`、`src/pages/easyAgent` 下的旧文件、`src/mixins` 里的 Easy Agent mixin）：
   ```bash
   node <迁移包>/integration/tools/sync-easy-agent.cjs --package <迁移包> --target <宿主根> --legacy-scan
   ```
   只输出「与新模块内容完全相同 / 同名但内容不同（可能有内网定制）/ 模块里没有」以及宿主源码里仍引用旧路径的位置，
   **不删除任何文件**。先把内网定制回流到外网、把引用改到 `@/packages/easyAgent`，再人工清理。
   注意 `src/pages/easyAgent/index.vue` 是新的页面包装，不要当旧文件删。
9. **宿主里为旧接入加过的全局代码**（逐项确认无其他用途后再移除）：引入外网 `style.css` 的语句、为 Easy Agent 加的
   `Vue.use(Button/Dialog)`、全局 `v-tip` 指令、复制过的 `iconify-raw-webpack` 插件配置。新模块都不需要它们。
10. **构建与冒烟**：宿主 `npm run build`；进入页面、发一条消息、离开再进入、打开「专家·技能·连接器 → 编辑 mcp.json」。

旧路径 → 新路径（外网侧；内网旧目录是早先手工搬运的副本）：

| 外网旧路径 | 新路径 |
| --- | --- |
| `src/App.vue` | `src/packages/easyAgent/EasyAgentPage.vue`（外网 `src/App.vue` 改为薄壳） |
| `src/api/*.js` | `src/packages/easyAgent/api/*.js` |
| `src/utils/*.js`（含 `request.js`） | `src/packages/easyAgent/utils/*.js` |
| `src/features/**`、`src/shared/**`、`src/directives/tip.js`、`src/markdownSetup.js` | `src/packages/easyAgent/` 下同名路径 |
| `src/theme.js` | `src/packages/easyAgent/runtime/theme.js` |
| `src/style.css` | 模块部分 → `styles/base.css`；整页部分 → 外网 `src/standalone/global.css` |
| `src/App.vue` 的非 scoped 深色样式块 | `src/packages/easyAgent/styles/theme-dark.css` |
| `src/api/request.js`、`src/config.js` | 删除（合并进 `utils/request.js`；`config.js` 已无引用） |
| `~icons/*` + `scripts/iconify-raw-webpack.cjs` | `src/packages/easyAgent/assets/icons/*.js`（生成），插件删除 |

## 4. 日常同步与回滚

- 外网导出：`npm run export:easy-agent -- --baseline <manifest>` 或 `--baseline-git <提交>`；不给基线时输出「首次导入」清单。
  导出前自动跑 `check:easy-agent`；`--archive` 另打 `.tar.gz`；`--out` 指定目录（默认 `dist-easy-agent/`，已忽略）。
- 迁移包内容：`manifest.json`（受管根目录、逐文件 sha256 与大小、来源提交与模块目录未提交改动、依赖清单、合同版本、
  相对基线的新增 / 修改 / 删除、依赖变化、合同变化）、`module/src/packages/easyAgent/**`、`integration/**`、`CHANGES.md`。
- 内网同步工具规则：
  - 只操作 manifest 声明的受管目录；路径必须是规范的相对路径且在受管目录下，否则拒绝；
  - 先校验迁移包：每个文件哈希必须匹配，包里不能有 manifest 未声明的文件；
  - 新增 / 更新：宿主文件与上一版记录一致才更新；与上一版不一致 → 冲突，默认不覆盖；
  - 删除：只删上一版 manifest 记录、本版已移除、且宿主未改动的文件；
  - 受管目录里的未纳管文件只报告，保持不动；
  - 应用前备份到 `<宿主>/.easy-agent-backups/<时间戳>/`（建议加入宿主 `.gitignore`），写入后逐个校验哈希，任一步失败
    自动按备份恢复，**不写**新的同步状态；
  - 同一迁移包重复应用：无任何写入；
  - 宿主是 Git 仓库时检查受管文件是否被宿主 `.gitignore` 忽略（例如通用的 `workspace/` 规则会顺带忽略
    `features/workspace/`，文件本机存在、构建能过，但提交时会漏掉），有则醒目提示加反向规则或 `git add -f`；
  - 回滚：`--rollback <备份目录>`（恢复被覆盖 / 删除的文件、移除新增的文件、还原同步状态）。
  - 退出码：0 成功 / 预览无冲突；1 失败；2 有冲突未写入。
- 依赖或合同变化时，预览与应用都会醒目提示，工具不装包、不改路由与宿主配置。

## 5. 请求层合同（模块内唯一传输实现 `utils/request.js`）

| 函数 | 返回 | 用途 |
| --- | --- | --- |
| `request(config, fallback)` | 响应体（JSON 业务数据） | 普通接口；失败时 `error.message` = 后端 `detail` 或兜底文案，`error.response` 保留 |
| `requestRaw(config)` | 完整响应 `{ status, headers, data }` | 需要状态码 / 响应头（如 `Content-Disposition` 文件名） |
| `requestText` / `requestBlob` / `requestArrayBuffer` | 文本 / Blob / ArrayBuffer | 预览、下载 |
| `fetchWithAuth(url, init)` | 原生 `Response`（不预读 body） | SSE、需要原生流的场景；支持 `AbortSignal` |
| `resolveUrl(path)` | 拼好前缀的地址 | iframe / img / 下载链接 / XHR 上传 |

- 模块使用**自己的 axios 实例**：宿主在自己实例或全局 `axios.interceptors` 上挂的 401 跳转不会作用于模块请求；
  宿主改过的 `axios.defaults`（`Authorization`、`baseURL`、`withCredentials`）不会带到 Agent 请求（已测试）。
- 不复用宿主公共 request：宿主 request 多为「只返回 data + 401 整站跳转」，无法满足原始响应、二进制、SSE 与「401 只影响
  Agent」的要求。网关要求的附加头用 `api.headers()` 提供。
- 401：可续期时并发合并为一次续期、原请求最多重发一次；登录类接口（`/agent/auth/`、`/api/auth/`）与
  「被其他设备顶下线」不续期；失效只清 Agent 自己的 token，经模块内事件与 `auth.onExpired` 通知，**不派发 window 事件**。
- FormData 上传不手写 multipart boundary；SSE 只由 `api/sse.js` 的 `readSSE` 解析；取消（`AbortSignal`）在续期等待期间同样生效。

## 6. 依赖

运行期依赖由 `dependencies.json` 自动生成（实际 import + 锁文件）。宿主现状列为「待宿主确认」的，请对照内网 `package.json`。

| 包 | 版本范围 | 外网锁定 | 样例宿主实测（Node 14） | 宿主现状 | 说明 |
| --- | --- | --- | --- | --- | --- |
| vue | ^2.7.16 | 2.7.16 | 2.7.14 | 已有 2.7.14 | 用 2.7 内置 Composition API；2.7.14 实测可用（范围按外网写，宿主无需升级） |
| element-ui | ^2.15.14 | 2.15.14 | 2.15.13 | 已有 2.15.13 | 只用 `lib/button`、`lib/dialog`、`lib/message-box` 的 JS（局部注册）；主题 CSS 用宿主的 |
| axios | ^1.7.9 | 1.20.0 | 1.7.9 | 待宿主确认 | 需要 1.x（实例头合并、`signal` 取消）；宿主若是 0.x 需评估升级或并存方案 |
| marked | ^17.0.6 | 17.0.6 | 17.0.6 | 待宿主确认 | `engines: node>=20` 只在安装时警告（npm 6 不阻断）；打包在 Node 14 下通过 |
| marked-emoji / marked-katex-extension / gemoji | ^2.0.3 / ^5.1.10 / ^8.1.0 | 2.0.3 / 5.1.13 / 8.1.0 | 同左 | 待宿主确认 | Markdown 扩展 |
| katex | ^0.16.47 | 0.16.47 | 0.16.47 | 待宿主确认 | 引入 `katex.min.css`（选择器均为 `.katex*`，另有一条 `body{counter-reset:katex…}`）；字体随宿主构建输出 |
| highlight.js | ^11.11.1 | 11.12.0 | 11.12.0 | 待宿主确认 | 只注册常用语言；主题样式是模块内限域副本 |
| monaco-editor | ^0.53.0 | 0.53.0 | 0.53.0 | 待宿主确认 | mcp.json 编辑器；worker 见第 7 节 |
| docx-preview / exceljs / xlsx | ^0.3.7 / ^3.4.0 / ^0.18.5 | 0.3.7 / 3.10.0 / 0.18.5 | 同左 | 待宿主确认 | Office 预览（懒加载） |

`echarts` 已于 2026-10-30 随图表渲染功能一起去掉：数据类 MCP 工具返回的 `chart` 信封不再画图，按通用工具卡片原文展示
（外网 Vue 2 / Vue 3 与 AI 侧边栏 SDK 相同）。宿主若之前为它安装过 `echarts`，确认无其他用途后可卸载，不卸载也不影响构建。

编译期要求见 `dependencies.json` 的 `build` 段。外网专用工具（`@iconify/json`、`unplugin-icons`、Vue CLI 插件等）宿主不需要安装。
**不要**把外网 `package.json` 整份覆盖给宿主。

## 7. 样式、资源与构建

- 模块样式只在 `.easy-agent-root`（页面根节点 / SDK 影子根内的挂载点）内生效：重置用 `:where(.easy-agent-root)` 包裹，
  优先级与原全局重置一致（标签 0,0,1、通配 0,0,0），组件 scoped 样式与 Element UI 照旧覆盖重置。
  唯一刻意降低的是 `[type='button']` 等属性选择器（0,1,0 → 0,0,0），避免 `el-button` 丢底色。
- 主题变量（`--primary-color` 等）定义在模块根节点上，不写宿主 `:root` / `<html>`；暗色只由 `theme.mode` 控制
  （模块根节点 `data-theme`），宿主给 `<html>` 设 `data-theme="dark"` 不影响模块，模块的暗色规则也不会作用到宿主。
- 限域的局限：宿主同为 0,0,1 的全局标签样式与模块重置按加载顺序决胜（模块随路由 chunk 后加载，通常模块生效）；
  `#app button` 这类更高优先级的宿主规则无法靠限域抵御。已用代表性宿主样式验证（第 8 节）。
- 挂在 `body` 上的内容：`v-tip` 浮层（`ea-` 前缀，自带字体 / 盒模型）、Element `MessageBox` 与 `el-dialog` 遮罩（用宿主的 Element 主题与 z-index 管理）、
  临时下载链接。模块的模态浮层（设置、预览、人员管理等）是 `position: fixed` 的视口级遮罩，会盖住宿主顶栏，与 Element 对话框行为一致。
- `:where()` 需要 Chrome / Edge 88+、Firefox 78+、Safari 14+。
- 图标：模块内生成的 JS 字符串（Lucide：ISC；VSCode Icons：MIT，许可写在文件头），宿主无需任何插件。
- Monaco：`new Worker(new URL(..., import.meta.url))`，webpack 5 输出独立 worker chunk，地址跟随宿主 `publicPath`
  （已验证 `/portal/` 非根路径）；worker 必须与页面同源（`publicPath` 指向跨域 CDN 时浏览器拒绝）；宿主若已定义
  `self.MonacoEnvironment`，模块沿用宿主的，宿主工厂需能提供 `json` worker。
- 懒加载：路由页面、各面板、Office 预览、Monaco 均为动态 import，chunk 与字体随宿主构建输出到宿主 `publicPath` 下。

## 8. 验证记录（2026-10-30）

| 项 | 环境 / 命令 | 结果 |
| --- | --- | --- |
| 外网单元测试 | Node 24.18.0，`npm run test:unit` | 150 通过（2026-10-30 去掉图表渲染后；含请求合同、宿主 axios 默认值、工具卡片呈现、同步工具、边界检查、导出、导航、运行时配置；原有 122 个用例逐一保留） |
| 模块检查 | `npm run check:easy-agent` | 通过：受管文件 100 个，运行期依赖 13 个（2026-10-30 去掉 ECharts 图表渲染后） |
| 外网构建 | Node 24.18.0 与 Node 14.20.0 下 `npm run build`、`npm run build:sdk` | 均通过 |
| 独立应用视觉回归 | 改造前（HEAD 39d7eec）与改造后各截 6 屏（登录、首页、对话含代码 / 公式、加号菜单、设置、技能中心）逐像素比较 | 除时间显示的分钟数字外完全一致 |
| 同步工具 | 测试以 Node 14.20.0 执行工具 | 新增 / 更新 / 删除 / 冲突 / --force+备份 / 失败自动恢复 / 回滚 / 越界与篡改拒绝 / 旧目录扫描 全部通过 |
| 样例宿主安装与构建 | `integration/host-sample`：Node 14.20.0、npm 6.14.17、vue 2.7.14、vue-router 3.6.5、element-ui 2.15.13、webpack 5.96.1、@vue/cli-service 5.0.8、vue-loader 15.11.1、axios 1.7.9；`publicPath: /portal/`；用同步工具从迁移包导入 | 安装、同步、构建通过（仅体积提示） |
| 浏览器冒烟 | Chromium 156 无头 + `host-sample/mock-backend.cjs`（合同级 mock），`node host-sample/smoke.mjs` | 47/47 通过：布局 / 标题 / URL 不被抢占，带图表信封的 MCP 工具结果在页面与 AI 侧边栏都按通用工具卡片原文展示（无图表、无 canvas），宿主样式前后不变，宿主 token 与前缀不泄漏，无宿主 window 事件，SSE 流式与 Markdown / 代码 / KaTeX，离开后后端继续、返回后重新挂载，keep-alive 卸下全局监听，401 续期，被顶下线不互踢，Monaco worker / 字体 / chunk 在 `/portal/` 下可加载，SDK 与页面同页共存（token 分开、destroy / 再初始化不影响页面） |

未验证 / 缺口：真实后端联调（只用了合同级 mock）；知识库、人员管理、定时任务、文件预览等页面只验证了构建，
没有在浏览器里逐个打开；内网真实宿主（axios 版本、ESLint、浏览器内核、全局样式、keep-alive 用法）未接触。
**以上只说明样例宿主接入通过，不等同于内网验收。**

复现：

```bash
npm run export:easy-agent -- --out /tmp/ea
cp -r integration/host-sample /tmp/ea-host && cp -r integration/host-template/src/. /tmp/ea-host/src/ \
  && cp integration/host-template/vue.config.easyAgent.js /tmp/ea-host/
cd /tmp/ea-host && npm install            # Node 14 / npm 6
node /tmp/ea/easy-agent-*/integration/tools/sync-easy-agent.cjs --package /tmp/ea/easy-agent-* --target /tmp/ea-host --apply
npm run build && cp -r <frontend-vue2>/dist-sdk dist/sdk
node mock-backend.cjs --dist dist --port 18091 &
node smoke.mjs --chrome <chrome 路径> --base http://localhost:18091   # Node ≥ 22
```

## 9. 原有风险逐项处理

| 线索 | 处理 | 状态 |
| --- | --- | --- |
| Vue ^2.7.16 / webpack 5.111.1 与宿主 2.7.14 / 5.96.1 | 不改依赖；样例宿主按内网版本实测 | 已验证（样例宿主）；Vue CLI、vue-loader 实际版本待宿主确认 |
| 组件直接从 `vue` 导入 Composition API | 宿主是 2.7，保留，不做降级改写 | 构建与主流程已验证；知识库 / 人员管理页面未在浏览器打开 |
| `run-vite.mjs` 实际调用 vue-cli-service | 确认后保留（Docker / CI 依赖该脚本名） | 保留设计 |
| `dev` / `build` 依赖仓库级 `sync-frontend-shared.mjs` | 同步目标改为模块内 `shared/`；模块自带完整副本，宿主不需要 `frontend-shared/`；`check` 校验来源一致，来源缺失时报错（可显式 `--allow-missing-shared-source`） | 已解决 |
| `utils/request.js` 与 `api/request.js` 两套 | 合并为一套（`personnel/api.js` 改用统一层，语义逐项核对）；新增合同测试 | 已解决 |
| `/api/personnel`、`/api/knowledge/v1` 前缀 | 不改后端前缀；所有通道统一经 `resolveUrl`；导出前缀清单供宿主放行 | 已解决 |
| `src/config.js` 仍存在 | 引用图确认无引用后删除；`docker/entrypoint.sh` 仍写 `runtime-config.js`（Vue 2 页面从未加载）未改 | 已解决；部署脚本待确认 |
| 全局 reset / `:root` / `html,body,#app` / 滚动条 / `theme.js` 写 html / 标题 / history / `100vw` | 模块样式限域、主题写模块根、标题与地址经适配器、页面跟随宿主容器 | 已解决并在浏览器验证 |
| `main.js` 全局注册 Element 组件与 `tip` 指令 | 改为组件局部注册；Element 主题由宿主提供；独立应用 `main.js` 只引主题 CSS | 已解决 |
| `tip.js` 往 body / head 注入 | 保留单例浮层；卸载 / 缓存时收起；样式不依赖宿主盒模型与字体 | 保留设计 |
| `~icons/*` 依赖自定义插件 | 生成为模块内 JS（与原插件逐字节一致），删除插件 | 已解决 |
| Monaco Worker | webpack 5 原生 worker；`/portal/` 非根路径验证通过 | 已验证；宿主已有 `MonacoEnvironment`、跨域 CDN 待宿主确认 |
| SDK 与路由组件两种入口 | SDK 保留独立运行时与 Shadow DOM，经 `sidebar.js` 复用同一实现 | 已验证（构建 + 同页共存） |
| 模块级 `chatStore` / 事件总线 / `configureRequest` | 页面与页面内侧边栏共享（保留）；SDK 独立副本不覆盖页面配置；单页单实例并有守卫 | 已验证 |
| Node 14 / `node --test` / 锁文件 v3 / `marked` engines | 构建与工具支持 Node 14（已验证）；单元测试需 Node ≥ 20.11（Node 14 下明确拒绝）；锁文件 v3 只用于外网安装 | 已验证 |
| 额外发现：`SessionList` 与原 `App.vue` 的 document / window 监听从不移除 | 改为成对移除 / 模块内事件 | 已解决 |
| 额外发现：门户身份缺失时自动用 `demo/demoid` 登录 | 默认不回落；外网演示需显式 `VUE_APP_PASSWORDLESS_DEMO_FALLBACK=true` | 已解决（独立应用行为变化） |
| 额外发现：401 续期遇到「被顶下线」会重新登录、两端互踢 | 被顶下线不续期（SDK 同样适用） | 已解决（SDK 行为变化） |
| 额外发现：`KnowledgeWorkbench` 用了 Vue 3 的 `:global()`，Vue 2 scoped 不认，整条 hover 规则被丢弃 | 未改（与本次改造无关，单独修） | 未处理 |

## 10. 待宿主确认

1. 内网 `@vue/cli-service`、vue-loader（SFC 编译器）的实际版本；是否用 Vue CLI 5。
2. 内网 axios 版本（模块需要 1.x）。
3. 内网是否开启 ESLint `lintOnSave`、是否有 jest 等扫描 `src/**/*.test.js` 的工具。
4. 用户浏览器内核版本（`:where()` 需 Chrome 88+；依赖包的现代语法是否需要 `transpileDependencies`）。
5. 宿主是否全局劫持 XHR / fetch（统一登录 SDK、监控 SDK），401 时是否在拦截器里直接跳转。
6. 门户身份的实际存储键与是否需要续期（模板沿用 `system:share:*`）；Agent 后端对内网的访问方式（同源反代或跨域）。
7. 宿主真实路由（hash / history）与 `keep-alive` 用法；Easy Agent 页面父容器的高度来源。
8. 宿主是否自己使用 Monaco（`MonacoEnvironment`），`publicPath` 是否为跨域 CDN。
9. 内网旧目录里是否有定制（先 `--legacy-scan`）。

## 11. 合同变更记录

| 版本 | 日期 | 变化 | 宿主适配器需要做什么 |
| --- | --- | --- | --- |
| 1 | 2026-10-30 | 首版 | 按 `host-template/src/integrations/easyAgent.js` 创建 |
