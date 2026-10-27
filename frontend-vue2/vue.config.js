const { defineConfig } = require('@vue/cli-service')

// ── 环境与后端地址（开发代理目标）──────────────────────────────────────────
// 约定：前端所有请求一律使用相对路径（/agent/...），不拼后端域名。
//   · 开发/联调：由下面的 devServer.proxy 把 /agent 转发到对应环境后端，前端无跨域；
//   · 生产：后端同源托管 dist/（或由 Nginx 反代 /agent），相对路径直接命中后端。
//
// 当前环境取自 .env.<mode> 中的 VUE_APP_AGENT_ENV（由 scripts/env-mode.mjs 决定加载哪个
// .env 文件），也可用 VUE_APP_PROXY_TARGET 直接覆盖代理目标。
const PROXY_TARGETS = {
  dev: 'http://localhost:8080',
  test: 'http://192.168.1.100:8007',
  prod: 'http://192.168.1.200:8000',
  win: 'http://127.0.0.1:8080',
}

const AGENT_ENV =
  process.env.VUE_APP_AGENT_ENV || (process.platform === 'win32' ? 'win' : 'dev')
const PROXY_TARGET =
  process.env.VUE_APP_PROXY_TARGET || PROXY_TARGETS[AGENT_ENV] || PROXY_TARGETS.dev

// AI 侧边栏 SDK 构建（scripts/build-sdk.mjs 设置）：lib 模式。
// SDK 需支持 Node 14 构建：unplugin-icons 23 为纯 ESM（Node 14 无法 require），改用等价的 CJS 实现
const IS_SDK_BUILD = process.env.EASY_SDK_BUILD === '1'

function iconsPlugin() {
  if (IS_SDK_BUILD) {
    const { IconifyRawPlugin } = require('./scripts/iconify-raw-webpack.cjs')
    return new IconifyRawPlugin()
  }
  return require('unplugin-icons/webpack')({ compiler: 'raw', autoInstall: true })
}

module.exports = defineConfig({
  // SDK 的 common.js 会被宿主再次打包：依赖全部转译，避免 ?. / ?? 等语法让 webpack 4 宿主解析失败
  transpileDependencies: IS_SDK_BUILD ? true : ['element-ui'],
  // 压缩阶段同样限制在 ES2018，否则 terser 会把 `a != null ? a : b` 重新压成 `a ?? b`
  ...(IS_SDK_BUILD ? { terser: { terserOptions: { ecma: 2018 } } } : {}),
  productionSourceMap: false,

  devServer: {
    // 默认仅本机可访问；设 DEV_HOST=0.0.0.0 可开放局域网访问（vue-cli 的
    // --host 参数会被本配置覆盖，故用环境变量作为开关）。
    host: process.env.DEV_HOST || (process.platform === 'win32' ? '127.0.0.1' : '0.0.0.0'),
    port: Number(process.env.PORT || 5173),
    // 静态资源一律 no-cache：防止局域网访客浏览器缓存旧版 index.html/JS，
    // 打开出旧版应用（缺新模块/路由时表现为点入口显示 404，且新接口请求不发）。
    headers: { 'Cache-Control': 'no-cache' },
    // SSE 流式必须关闭压缩：webpack-dev-server 默认 compress=true 会对
    // text/event-stream 做 gzip，gzip 流要攒满缓冲块才 flush，导致事件被
    // 一次性延迟到流结束才到达（表现为「后端已 step2，前端仍显示正在响应」）。
    compress: false,
    // HMR 的 WebSocket 端口跟随「当前页面端口」，而不是写死的 devServer.port。
    // 否则当 5173 被占用、dev server 自动换到别的端口时，HMR 仍去连旧端口，
    // 控制台会报 `WebSocket connection to ws://<host>:<port>/ws failed`，
    // 表现为「改完代码页面不自动更新」。
    client: {
      webSocketURL: { port: 0 },
    },
    // 开发代理：/agent 前缀的接口（REST + SSE 流式）全部转发到后端
    proxy: {
      '/agent': {
        target: PROXY_TARGET,
        changeOrigin: true,
        // SSE 长连接：禁止中间层缓存/攒包，保证流式输出逐步到达
        onProxyRes(proxyRes) {
          proxyRes.headers['cache-control'] = 'no-cache'
        },
      },
      // /api 旧前缀（知识库 /api/knowledge/v1、人员 /api/personnel）后端有
      // legacy 兼容路由：缓存了旧版前端的浏览器（SPA 旧 JS 用旧前缀）在 dev
      // server 上会 404，必须一并代理到后端，旧页面才能继续工作。
      '/api': {
        target: PROXY_TARGET,
        changeOrigin: true,
      },
    },
  },

  configureWebpack: {
    // SDK 体积：全量 highlight.js（190+ 语言）换成官方常用语言子集，压缩后少约 700 KB
    resolve: IS_SDK_BUILD ? { alias: { 'highlight.js$': 'highlight.js/lib/common' } } : {},
    plugins: [iconsPlugin()],
  },
})
