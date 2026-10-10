# easy-agent frontend

Vue 2.7 + Element UI 单页应用，Vue CLI（webpack）构建。无 Vue Router（`App.vue` 内布尔状态切页）、无独立状态管理库。

## 语法约束

Options API 为主，可使用 Vue 2.7 内置 Composition API 函数（`ref` / `computed` / `watch` / 生命周期钩子）；禁用 `<script setup>`、`defineProps`、`<Teleport>`、`v-model:` 等 Vue 3 专属语法，由 `src/utils/vue2Compat.test.js` 守卫。

## 常用命令

```bash
npm run dev        # 开发服务器，默认 http://localhost:5173，/agent、/api 代理到后端
npm run build      # 生产构建到 dist/（由后端 FastAPI 托管）
npm run test:unit  # 单元测试（Node 内置 test runner，含 vue2Compat 语法守卫）
```

## 环境与构建模式

构建入口 `scripts/run-vite.mjs`（实际调用 Vue CLI）按 `AGENT_ENV` 映射构建 mode 并加载 `.env.[mode]`；运行期后端地址由 `dist/runtime-config.js` 机制注入，详见根目录 README「配置说明」。
