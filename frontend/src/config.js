/**
 * 前端全局配置（集中管理）
 *
 * 后端地址固定为同源相对路径 /agent/...：后端直接托管 dist/ 或由同源反代转发，
 * 不再读取 .env.[mode] 与运行期 runtime-config.js。
 * 运行环境标识取 Vite 内建的构建 MODE，应用名称固定。
 */

import { reactive } from 'vue'

// 当前运行环境标识：取 Vite 构建期 MODE（内建值，不依赖自定义环境变量）。
export const AGENT_ENV = import.meta.env.MODE || 'prod'

// 固定同源：所有请求走 /agent/...，由后端或同源反向代理提供。
export const API_BASE_URL = ''
export const APP_TITLE = 'Easy Agent'
export const APP_WELCOME_TITLE = `${APP_TITLE}，让工作更简单`
export const BUILD_MODE = import.meta.env.MODE || 'development'

/**
 * 运行时环境信息，由后端 /agent/auth/config 在登录/加载时填充。
 * - win: 后端是否运行在 Windows 系统上（用于路径/命令等差异化处理）
 * - agentEnv: 后端运行环境标识（dev | test | prod）
 */
export const appRuntime = reactive({
  win: false,
  agentEnv: '',
})

// 加载运行期配置时即用 AGENT_ENV 初始化（即便后端 /agent/auth/config 尚未返回，
// 前端也已知道当前处于哪个环境，可据此加载对应环境配置）。
appRuntime.agentEnv = AGENT_ENV

// 浏览器侧始终打印当前生效配置（含生产模式），便于在控制台核对实际加载的环境
console.log(
  `%c[${APP_TITLE} 配置信息] (${BUILD_MODE})\n` +
    `  运行环境: ${AGENT_ENV}\n` +
    `  后端地址: ${API_BASE_URL}\n` +
    `  应用名称: ${APP_TITLE}\n` +
    `  构建时间: ${new Date().toLocaleString()}`,
  BUILD_MODE === 'development'
    ? 'color: #0ea5e9; font-weight: bold; font-size: 12px;'
    : 'color: #64748b; font-size: 12px;'
)

// 同步网站标题（浏览器标签）为配置的应用名称
if (typeof document !== 'undefined') {
  document.title = APP_TITLE
}
