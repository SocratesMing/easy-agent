const { defineConfig } = require('@vue/cli-service')
const Icons = require('unplugin-icons/webpack')

module.exports = defineConfig({
  transpileDependencies: ['element-ui'],
  productionSourceMap: false,
  configureWebpack: {
    plugins: [Icons({ compiler: 'raw', autoInstall: true })],
    devServer: {
      host: process.platform === 'win32' ? '127.0.0.1' : '0.0.0.0',
      port: Number(process.env.PORT || 5173),
      // HMR 的 WebSocket 端口跟随「当前页面端口」，而不是写死的 devServer.port。
      // 否则当 5173 被占用、dev server 自动换到别的端口时，HMR 仍去连旧端口，
      // 控制台会报 `WebSocket connection to ws://<host>:<port>/ws failed`，
      // 表现为「改完代码页面不自动更新」。
      client: {
        webSocketURL: { port: 0 },
      },
    },
  },
})
