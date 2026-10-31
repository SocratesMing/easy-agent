// 代表性宿主配置：非根部署路径 + 宿主自己的 /api 代理；Easy Agent 只合并了 integration/host-template/vue.config.easyAgent.js
const easyAgent = require('./vue.config.easyAgent.js')

module.exports = {
  publicPath: '/portal/',
  productionSourceMap: false,
  devServer: {
    ...easyAgent.devServer,
    proxy: {
      ...easyAgent.devServer.proxy,
      '/api': { target: 'http://localhost:9999', changeOrigin: true },
    },
  },
}
