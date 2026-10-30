/**
 * Easy Agent 对宿主 vue.config.js 的增量配置（首次接入时人工合并，不要整份替换宿主配置）。
 *
 * 只有开发服务器代理是必须的；生产环境由宿主 Nginx 把下面三个前缀反代到 Agent 后端
 * （或在适配器里配置 api.baseURL 跨域直连）。模块本身不需要额外 loader / 插件 / alias。
 */
const EASY_AGENT_TARGET = process.env.EASY_AGENT_PROXY_TARGET || 'http://localhost:8080'

module.exports = {
  devServer: {
    // SSE 流式必须关闭压缩，否则事件会被 gzip 攒到流结束才到达
    compress: false,
    proxy: {
      // 具体前缀放在宿主自己的 '/api' 代理之前（webpack-dev-server 按声明顺序匹配）
      '/agent': {
        target: EASY_AGENT_TARGET,
        changeOrigin: true,
        onProxyRes(proxyRes) {
          proxyRes.headers['cache-control'] = 'no-cache'
        },
      },
      '/api/knowledge': { target: EASY_AGENT_TARGET, changeOrigin: true },
      '/api/personnel': { target: EASY_AGENT_TARGET, changeOrigin: true },
    },
  },
  // 宿主 browserslist 需要支持的浏览器若不支持依赖包里的较新语法（marked 17、monaco-editor 等），
  // 在这里按需加入转译，例如：transpileDependencies: ['marked', 'marked-emoji'],
}
