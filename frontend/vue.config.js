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
})
