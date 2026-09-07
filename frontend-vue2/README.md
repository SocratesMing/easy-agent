# easy-agent 前端（Vue 2 版本）

本目录是 `frontend/`（Vue 3 + Vite）的 **Vue 2 等价实现**，两者功能与交互完全一致，仅技术栈不同。

## 技术栈对照

| 项 | `frontend/`（Vue 3） | `frontend-vue2/`（Vue 2） |
| --- | --- | --- |
| 框架 | Vue 3.5 | Vue 2.7（内置 Composition API） |
| 构建 | Vite 7 | Vue CLI 5（webpack） |
| 组件写法 | `<script setup>` | `<script>` + `export default { setup() {} }` / Options API |
| UI 库 | — | element-ui 2.15 |
| 请求 | `fetch` + `authFetch` | axios 统一层（`src/api/request.js`） |

## 目录结构

与 `frontend/` 保持一致：`src/components/`、`src/api/`、`src/utils/`、`src/config.js`、`public/`、`scripts/`。
差异仅在构建配置：`vue.config.js` + `babel.config.js` + `postcss.config.cjs` + `tailwind.config.cjs`
（`frontend/` 用的是 `vite.config.js` + `postcss.config.js` + 根目录 `index.html`；
Vue CLI 的入口 HTML 位于 `public/index.html`）。

## 迁移约定

- `defineProps` / `defineEmits` → `props` / `emits` 选项或 `setup(props, { emit })`
- `onMounted` / `onUnmounted` / `onBeforeUnmount` → `mounted` / `destroyed` / `beforeDestroy`
- `<Teleport to="body">` → element-ui 的 Dialog/Popover 或挂载到 body 的实现
- `v-model:propName` → `:propName.sync` 或显式 prop + 事件
- `import.meta.env.VITE_*` → `process.env.VUE_APP_*`
- 事件名统一使用 kebab-case（Vue 2 不区分大小写）

`src/utils/vue2Compat.test.js` 是语法守卫，禁止 `<script setup>`、`defineProps`、`defineEmits`、
`defineExpose`、`<Teleport>`、`v-model:` 再次出现在源码中：

```bash
node --test src/utils/vue2Compat.test.js
```

## 开发 / 构建

```bash
npm install
npm run dev     # 开发服务器
npm run build   # 产物输出到 dist/
```

`npm run build` 会额外执行 `scripts/generate-runtime-config.sh`，在 `dist/runtime-config.js`
中写入运行期后端地址与欢迎语（与 Vue 3 版本一致的机制）。

> 后端默认托管的是 `frontend/dist`。如需让后端托管本目录产物，把 `easy_agent/app.py` 中
> `frontend_dist` 的指向改为 `frontend-vue2/dist`（或复制产物覆盖 `frontend/dist`）后重启服务。
