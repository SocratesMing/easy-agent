// 运行期配置占位文件（由构建流程从 public/ 拷贝到 dist/）。
// 容器 / serve 启动脚本会在启动时覆盖 dist/runtime-config.js，
// 写入真实的后端地址（window.__RUNTIME_CONFIG__.API_BASE_URL）。
// 未注入运行期配置时保留空对象，前端回退到构建期 import.meta.env。
window.__RUNTIME_CONFIG__ = window.__RUNTIME_CONFIG__ || {}
