# 对话内可视化：图表、流程图与交互组件（2026-11-12）

## 背景

对话里需要像截图那样直接看到图：数据图表、流程图，以及能拖滑块的交互演示。
2026-10-30 曾把 ECharts 从两个前端里删掉（c82f1127，SDK 1846 KB → 1299 KB），
所以这次的前提是**不把图表库打进前端包**，也不给内网宿主增加依赖。

## 方案

三类内容统一交给一个沙箱 iframe 渲染，ECharts / Mermaid 由后端托管、按需加载。

| 类型 | 来源 | 渲染 |
|---|---|---|
| 图表 | 正文里的 ` ```echarts ` 代码块（JSON 格式的 ECharts option，可包一层 `{"height", "option"}`）；数据类工具结果里的 `chart` 字段（`jmfx_mcp_server` 的信封） | 框架页 `echarts.init` |
| 流程图 | ` ```mermaid ` 代码块 | Mermaid，`securityLevel: 'strict'` |
| 交互组件 | ` ```widget ` 代码块（HTML，可含 `<style>` / `<svg>` / `<script>`） | 在沙箱里执行；` ```html ` 仍是普通代码块，不会被运行 |

链路：

1. `frontend-shared/markdown/index.js` 的 `renderer.code` 识别三种语言：流式中围栏还没闭合时输出固定骨架
   （「图表生成中…」，逐帧 HTML 不变）；闭合后在普通代码块外包一层 `.ea-visual` 占位。
   占位块在中间帧与最终渲染时 HTML 相同，`patchMarkdownBlocks` 复用节点，iframe 不会重建。
2. `MarkdownBody` 每次刷新后调 `mountVisuals`（`frontend-shared/visual/mount.js`）：建卡片
   （标题、源码、下载），进入视口附近才创建 iframe。工具卡片里的图表用 `VisualFrame.vue`，
   信封解析在 `frontend-shared/visual/envelope.js`（对象 / JSON / MCP content 数组 / Python repr）。
3. iframe 加载 `/agent/viz/frame.html`（`api/viz.py`，静态文件在 `easy_agent/static/viz/`），
   通过 postMessage 收发：
   - 外层 → 框架页：`render{kind, text, theme}`、`theme{theme}`、`export`
   - 框架页 → 外层：`ready{kinds}`、`size{height}`、`rendered`、`error{message}`、`unsupported`、
     `export{format, dataUrl}`、`prompt{text}`、`link{url}`、`widget-error{message}`
4. 组件里的 `sendPrompt(text)` 变成冒泡的 `ea-visual-prompt` 事件，`Chat.vue` 把文字填进输入框（不自动发送）。

过程渲染（分段折叠，尚未提交）里，结果带图表的工具调用放进 `describeProcessGroup().visuals`，
组折叠时与待审批卡片一样露出来。

## 安全

- iframe 只开 `sandbox="allow-scripts"`：不同源，拿不到外层 DOM、登录 token、cookie 与 localStorage；
  不能弹窗、不能跳转外层页面、不能提交表单、不能直接下载（下载由外层完成）。
- 框架页 CSP：`default-src 'none'`、`script-src 'self'`（开启交互组件时加 `'unsafe-inline'`）、
  `style-src 'self' 'unsafe-inline'`、`img-src data: blob:`、`connect-src 'none'`、`frame-src 'none'`、
  `form-action 'none'`、`base-uri 'none'`。组件拿到的只有它自己的源码，也没有通道外传。
- 组件里引用 CDN 上的 echarts / mermaid 会被换成本地托管的版本，其他外链脚本直接跳过。
- 页面里的 markdown 仍按原规则转义模型写的原始 HTML（`renderer.html`），这次没有放宽。
- 剩余风险：组件脚本可以让自己的 iframe 跳到外部地址（CSP 管不到导航），能带出的只有它自己的源码。

## 配置

`config.yaml` 的 `visual` 段（`bootstrap/config_models.py:VisualConfig`）：

```yaml
visual:
  enabled: true        # 总开关：false 时提示词不再提可视化，代码块按源码显示
  html_widgets: true   # HTML 交互组件；false 只关这一类
```

开关同时作用于三处：系统提示词（`agent/visual_prompt.py`，组装在联网说明之后、专家内容之前）、
框架页的 `data-enabled` / `data-widgets` 与 CSP。前端不读配置：框架页在 `ready.kinds` 里报告可用类型，
不支持的类型退回显示源码。

## 退回与降级

- 10 秒内没收到 `ready`（例如宿主 CSP 拦了 iframe、后端没有该路由）：显示源码并提示。
- 类型未开启：显示源码并提示。
- 渲染出错（JSON 不合法、Mermaid 语法错误）：显示错误与源码。
- 文件预览等不调 `mountVisuals` 的场景：样式不生效，照常显示代码块。

## 验证（2026-11-12）

- 单测：`frontend-shared/visual/*.test.js`（代码块识别、骨架、节点复用、信封解析）、两套前端的
  `toolPresentation.test.js`、`tests/api/test_viz.py`、`tests/agent/test_visual_prompt.py`。
- 浏览器（mock 后端 + 无头 Chromium，Vue 2 / Vue 3 各 18 项）：四张卡片都渲染、iframe 只有
  `allow-scripts`、源码与下载（PNG / HTML）、`sendPrompt` 填进输入框、沙箱内拿不到外层页面、
  不能用 localStorage、不能联网、深色主题重画、刷新后历史回放；关掉 `html_widgets` 时组件退回源码。
- 体积：SDK `umd.min.js` 比改动前多约 20 KB（含同时在改的过程渲染），打包产物里没有 ECharts / Mermaid。

## 待确认

- 内网宿主页面的 CSP `frame-src` 需要允许 Agent 后端的地址，否则 iframe 加载不出来（会退回显示源码）。
- 第三方库升级方法见 `easy_agent/static/viz/vendor/README.md`。
