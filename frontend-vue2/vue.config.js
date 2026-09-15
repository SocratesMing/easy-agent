const { defineConfig } = require('@vue/cli-service')
const Icons = require('unplugin-icons/webpack')
const CopyPlugin = require('copy-webpack-plugin')

// ── 环境与后端地址（开发代理目标）──────────────────────────────────────────
// 约定：前端所有请求一律使用相对路径（/agent/...），不拼后端域名。
//   · 开发/联调：由下面的 devServer.proxy 把 /agent 转发到对应环境后端，前端无跨域；
//   · 生产：后端同源托管 dist/（或由 Nginx 反代 /agent），相对路径直接命中后端。
//
// 当前环境取自 .env.<mode> 中的 VUE_APP_AGENT_ENV（由 scripts/env-mode.mjs 决定加载哪个
// .env 文件），也可用 VUE_APP_PROXY_TARGET 直接覆盖代理目标。
const PROXY_TARGETS = {
  dev: 'http://localhost:8000',
  test: 'http://192.168.1.100:8007',
  prod: 'http://192.168.1.200:8000',
  win: 'http://127.0.0.1:8000',
}

const AGENT_ENV =
  process.env.VUE_APP_AGENT_ENV || (process.platform === 'win32' ? 'win' : 'dev')
const PROXY_TARGET =
  process.env.VUE_APP_PROXY_TARGET || PROXY_TARGETS[AGENT_ENV] || PROXY_TARGETS.dev

module.exports = defineConfig({
  transpileDependencies: ['element-ui'],
  productionSourceMap: false,

  devServer: {
    host: process.platform === 'win32' ? '127.0.0.1' : '0.0.0.0',
    port: Number(process.env.PORT || 5173),
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
    },
  },

  configureWebpack: {
    plugins: [
      Icons({ compiler: 'raw', autoInstall: true }),
      // pdf.js 的 worker 必须与 pdfjs-dist 的安装版本严格一致，版本不同会直接报
      // "The API version does not match the Worker version"。Vue CLI(Webpack) 不支持
      // Vite 的 `?url` 语法，因此改为构建期从 node_modules 拷贝到输出根目录，
      // 免去手工同步（public/ 下的静态文件不会随依赖升级而更新）。
      new CopyPlugin({
        patterns: [
          {
            from: require.resolve('pdfjs-dist/legacy/build/pdf.worker.min.mjs'),
            to: 'pdf.worker.min.mjs',
          },
        ],
      }),
    ],
  },
})
