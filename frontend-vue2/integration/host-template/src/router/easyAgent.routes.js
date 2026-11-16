/**
 * Easy Agent 路由示例（首次接入时把这段并入宿主 src/router/index.js 的 routes，按宿主布局调整
 * path / meta / 父路由）。路由组件懒加载：首次进入页面才下载 Easy Agent 的 chunk 与样式。
 */
export default [
  {
    path: '/easy-agent',
    name: 'EasyAgent',
    component: () => import(/* webpackChunkName: "easy-agent" */ '@/pages/easyAgent/index.vue'),
    // keepAlive 由宿主决定：开启后离开页面时流式输出继续在后台接收；关闭则断开前端连接，
    // 后端照常生成，再次进入时自动重新挂载正在进行的回复
    meta: { title: 'Easy Agent', keepAlive: false },
  },
]
