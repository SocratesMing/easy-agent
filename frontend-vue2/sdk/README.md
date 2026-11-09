# @easy-agent/ai-sidebar

Easy Agent AI 侧边栏 SDK。完整接入说明见构建后的 `README.md`（由 `docs/AI侧边栏SDK集成指南.md` 复制），或仓库内同一文档。

## 本地联调 Demo

Vue 2 基线与 SDK 均以 **Node 14** 构建。开发服务器（`npm run dev`）会把 `dist-sdk/` 挂到 `/sdk/`，
构建后可直接打开 `http://localhost:5173/sdk/demo.html?baseURL=http://localhost:5173&auth=passwordless&username=<用户>&userId=<工号>&auto=1`
（`baseURL` 用开发服务器自身，`/agent` 由其代理到后端）。也可单独起静态服务：

```bash
cd frontend-vue2
npm run build:sdk
cd dist-sdk
python3 -m http.server 5181
```

打开：

```text
http://localhost:5181/demo.html?baseURL=<后端地址>&username=<用户>&userId=<工号>&auto=1
```

`demo.html` 模拟第三方宿主页面，用于验证：

- 三种鉴权：免密 / 宿主 `getToken` / 静态 token
- `pageContext` 与多页面 `setPageContext`
- 划词提问、行级 `open({ context, prompt })`、实例方法与事件回调

源文件：`frontend-vue2/sdk/demo.html`（`build:sdk` 会复制到 `dist-sdk/`）。

Docker 前端镜像会把 `dist-sdk/` 打包到 `/sdk/`，部署后可直接访问 `http://<前端地址>/sdk/demo.html`；因此构建前端镜像前需先执行 `npm run build:sdk`。
