# 接入验证用宿主样例（不是生产代码）

模拟内网宿主：Vue 2.7.14、Element UI 2.15.13（全量注册）、vue-router 3（hash 模式 + keep-alive）、
webpack 5.96.1、Vue CLI 5.0.8，在 Node 14.20.0 / npm 6.14.17 下安装与构建；部署在非根路径 `/portal/`。
宿主还刻意带上：全局 axios 默认值（Authorization / baseURL）与 401 跳转拦截器、同名 `v-tip` 指令、
`html[data-theme="dark"]`、同名 CSS 变量与标签级全局样式。

步骤（在 frontend-vue2/ 下执行，详见 integration/README.md「验证」）：

1. `npm run export:easy-agent -- --out <临时目录>` 生成迁移包；
2. 复制本目录到临时目录，再把 `integration/host-template/` 下的页面包装、适配器、路由片段与
   `vue.config.easyAgent.js` 复制进去（模拟首次接入）；
3. Node 14 下 `npm install`；`node <迁移包>/integration/tools/sync-easy-agent.cjs --package <迁移包> --target <宿主> --apply`；
4. `npm run build`，再用 `node mock-backend.cjs --dist <宿主>/dist` 起合同级 mock 后端，浏览器打开
   `http://localhost:8091/portal/#/easy-agent` 冒烟。
