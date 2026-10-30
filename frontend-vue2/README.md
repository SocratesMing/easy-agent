# easy-agent 前端（Vue 2 版本）

本目录是 `frontend/`（Vue 3 + Vite）的 **Vue 2 等价实现**，两者功能与交互完全一致，仅技术栈不同。

## 技术栈对照

| 项 | `frontend/`（Vue 3） | `frontend-vue2/`（Vue 2） |
| --- | --- | --- |
| 框架 | Vue 3.5 | Vue 2.7（内置 Composition API） |
| 构建 | Vite 7 | Vue CLI 5（webpack） |
| 组件写法 | `<script setup>` | `<script>` + `export default { setup() {} }` / Options API |
| UI 库 | — | element-ui 2.15 |
| 请求 | `fetch` + `authFetch` | axios 统一层（`src/packages/easyAgent/utils/request.js`） |
| 后端地址 | `src/config.js` + 运行期配置 | **`vue.config.js` 的 devServer.proxy**（前端一律用相对路径；嵌入 / SDK 场景由运行时配置 `api.baseURL` 加前缀） |

## 目录结构

业务代码全部在**受管模块** `src/packages/easyAgent/`：外网独立应用、内网 Vue 2 宿主的路由页面、
AI 侧边栏 SDK 共用这一份源码，内网通过迁移包整体同步这一个目录（见 `integration/README.md`）。

```text
src/packages/easyAgent/   业务模块（index.js / sidebar.js 公开入口，EasyAgentPage.vue 根组件，
                          runtime/ 适配合同，api/ features/ shared/ utils/ directives/ styles/ assets/）
src/App.vue src/main.js   外网独立应用的薄启动壳
src/standalone/           独立应用适配器（登录开关、门户免密、标题）与整页样式
src/sdk/index.js          AI 侧边栏 SDK 构建入口
integration/              内网接入模板、同步工具、验证用宿主样例、接入与同步指南
scripts/easy-agent/       check / export / 图标、限域样式、依赖清单生成
tests/                    仓库级结构测试（模块单元测试与源码放在一起）
```

新增业务代码一律放进 `src/packages/easyAgent/`，规则见该目录的 `README.md`，`npm run check:easy-agent` 会检查。
构建配置：`vue.config.js` + `babel.config.js` + `postcss.config.cjs`（Vue CLI 的入口 HTML 位于 `public/index.html`）。

## 迁移约定

- `defineProps` / `defineEmits` → `props` / `emits` 选项或 `setup(props, { emit })`
- `onMounted` / `onUnmounted` / `onBeforeUnmount` → `mounted` / `destroyed` / `beforeDestroy`
- `<Teleport to="body">` → element-ui 的 Dialog/Popover 或挂载到 body 的实现
- `v-model:propName` → `:propName.sync` 或显式 prop + 事件
- `import.meta.env.VITE_*` → `process.env.VUE_APP_*`
- 事件名统一使用 kebab-case（Vue 2 不区分大小写）

`tests/vue2Compat.test.js` 是语法守卫，禁止 `<script setup>`、`defineProps`、`defineEmits`、
`defineExpose`、`<Teleport>`、`v-model:` 再次出现在源码中：

```bash
node --test tests/vue2Compat.test.js
```

## 业务模块的检查、导出与同步

```bash
npm run check:easy-agent    # 模块边界、依赖清单、生成物、公开入口、全局副作用、样式限域
npm run export:easy-agent   # 生成迁移包（-- --baseline <上一版 manifest> 输出变化清单，-- --archive 打包）
npm run icons:easy-agent    # 重新生成模块图标（改 scripts/easy-agent/generate-icons.mjs 的清单后）
npm run deps:easy-agent     # 依赖变化后重新生成 dependencies.json
```

内网同步：`node <迁移包>/integration/tools/sync-easy-agent.cjs --package <迁移包> --target <宿主根>`（先预览，再 `--apply`）。
Node 版本：构建、SDK 构建与上述工具支持 Node 14；`npm run test:unit` 需要 Node ≥ 20.11。

## 开发 / 构建

```bash
npm install
npm run dev     # 开发服务器
npm run build   # 产物输出到 dist/
```

开发时前端所有请求走相对路径 `/agent/...`，由 `vue.config.js` 的 `devServer.proxy`
转发到对应环境后端（地址见 `PROXY_TARGETS`，可按 `VUE_APP_PROXY_TARGET` 覆盖）。
生产环境需与后端**同源**部署（后端托管 `dist/`，或 Nginx 反代 `/agent`），
详见 `docs/配置说明.md`。

> 后端（`easy_agent/app.py` 的 `frontend_dist`）默认托管的就是本目录的 `dist/`，构建后重启服务即可。
