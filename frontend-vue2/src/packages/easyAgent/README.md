# Easy Agent 业务模块（受管目录）

本目录是 Easy Agent 前端的**全部业务实现**，外网维护、整体同步到内网宿主的同一路径
`src/packages/easyAgent/`。外网独立应用（`frontend-vue2/src/App.vue`）、内网宿主路由页面和
AI 侧边栏 SDK（`frontend-vue2/src/sdk/`）都复用这一份源码。

- **归属**：外网是权威来源。内网不要直接改这里的文件——修复回到外网，再用同步工具同步；
  同步工具会把本地改动识别为冲突，默认不覆盖。
- **宿主差异**放在宿主自己的适配器（`src/integrations/easyAgent.js`）里，经
  `configureEasyAgent()` 传入，不写进本目录。
- 接入、同步、回滚步骤见 `frontend-vue2/integration/README.md`（迁移包里的 `integration/README.md`）。

## 公开入口

| 入口 | 给谁用 | 内容 |
| --- | --- | --- |
| `index.js` | 宿主路由页面、外网独立应用 | `EasyAgentPage`、`configureEasyAgent`、`createVueRouterNavigation`、`EASY_AGENT_CONTRACT_VERSION`、`EASY_AGENT_API_PREFIXES`、主题工具 |
| `sidebar.js` | 仅外网 SDK 构建（`src/sdk/index.js`） | `AiSidebar` 及 SDK 运行时需要的请求 / 鉴权函数 |

外部只从这两个入口引用，不要引用 `features/**` 等内部路径（内部文件可以随时调整）。

## 目录

```text
index.js / sidebar.js   稳定公开入口
EasyAgentPage.vue       业务根组件（原 App.vue）
runtime/                适配合同：config（配置）、events（模块内事件）、navigation（地址读写）、theme（主题）
api/                    后端接口（/agent/...）；features/*/api.js 为 feature 内聚的接口
features/               chat / knowledge / personnel / skills / experts / connectors / settings / tasks / workspace
shared/                 共享组件、markdown、图表、预览；带「自动生成」头的文件来自仓库级 frontend-shared/
utils/                  工具函数；utils/request.js 是唯一的传输实现
directives/             v-tip（由使用的组件局部注册）
styles/                 base.css（限域重置 + 主题变量）、theme-dark.css、highlight-github-dark.css（生成）
assets/icons/           图标 SVG 字符串（生成）
dependencies.json       依赖清单（生成）
```

## 长期约定（`npm run check:easy-agent` 会检查，失败即非零退出）

1. **新增业务代码放进本目录**，不要再在宿主的 `src/api`、`src/utils`、`src/mixins` 等公共目录里放 Easy Agent 的实现。
2. **只用相对路径**引用模块内部文件；不要用 `@/`、`~` 等宿主 alias，不要向上逃逸出本目录。
3. **只引用 `dependencies.json` 声明的 npm 包**。新增 / 删除依赖后执行 `npm run deps:easy-agent`，
   审阅生成的变化，并在 `integration/README.md` 的依赖表里写明宿主是否已有、是否需要额外配置。
4. **不在业务代码里读宿主私有信息**（`system:share:*` 等存储键、宿主 token），也不在 import 时固化
   `window` / `process.env`；需要宿主提供的能力一律加到 `runtime/config.js` 的合同里。
5. **不做全局副作用**：不 `Vue.use` / `Vue.component` / `Vue.directive`（改为组件局部注册），不 `$mount`，
   不改 `document.title`、`history`、`<html>` 的属性与样式，不派发通用名字的 `window` 事件
   （模块内事件用 `runtime/events.js`）。
6. **样式限定在 `.easy-agent-root` 内**：非 scoped 样式与 `.css` 的选择器以 `:where(.easy-agent-root)` /
   `.easy-agent-root` 开头；暗色用 `.easy-agent-root[data-theme="dark"]`，不要写 `html[...]` / `:root`。
7. **图标**用 `assets/icons/*.js`（新增图标：改 `scripts/easy-agent/generate-icons.mjs` 的清单后
   `npm run icons:easy-agent`）；不要再写 `~icons/...`。
8. **生成文件不手改**：`shared/` 下带「自动生成」头的文件改 `frontend-shared/` 源文件后 `npm run sync:shared`；
   `styles/highlight-github-dark.css`、`assets/icons/*`、`dependencies.json` 各有生成命令。
9. **适配合同有不兼容变化**（字段改名、语义改变、新增必填项）时，`EASY_AGENT_CONTRACT_VERSION` +1，
   并在 `integration/README.md`「合同变更记录」里写清宿主适配器要改什么。
10. 组件里新增的 `document` / `window` 监听、定时器、observer 必须在 `beforeDestroy` 里成对清理；
    页面会随宿主路由反复创建 / 销毁，也可能被 `keep-alive` 缓存。

`*.test.js` 是外网单元测试，不进迁移包。
