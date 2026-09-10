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
    },
  },
  chainWebpack: (config) => {
    // pdfjs-dist 的 worker .mjs 在 webpack5 下以静态资源输出（等价于 Vite 的 ?url）
    // 输出文件名不含 hash，方便 pdf.js 运行时按固定 URL 创建 module worker
    config.module
      .rule('pdf-worker')
      .test(/pdfjs-dist[/\\]legacy[/\\]build[/\\]pdf\.worker(\.min)?\.mjs$/)
      .type('asset/resource')
      .set('generator', { filename: 'static/js/[name][ext]' })
  },
})
