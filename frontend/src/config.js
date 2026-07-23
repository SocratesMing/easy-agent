/**
 * 前端全局配置（集中管理）
 *
 * 不同环境通过 .env.[mode] 文件注入 VITE_API_BASE_URL：
 * - .env.win         → Windows 平台（npm run dev / build，按平台自动选择）
 * - .env.dev         → Linux + AGENT_ENV=dev   (npm run dev)
 * - .env.test        → Linux + AGENT_ENV=test  (npm run dev:test / build:test)
 * - .env.prod        → Linux + AGENT_ENV=prod  (npm run build)
 */

import { reactive } from 'vue'

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
export const APP_TITLE = import.meta.env.VITE_APP_TITLE || 'Easy Agent'
export const BUILD_MODE = import.meta.env.MODE || 'development'

/**
 * 运行时环境信息，由后端 /api/auth/config 在登录/加载时填充。
 * - win: 后端是否运行在 Windows 系统上（用于路径/命令等差异化处理）
 * - agentEnv: 后端运行环境标识（dev | test | prod）
 */
export const appRuntime = reactive({
  win: false,
  agentEnv: '',
})

if (import.meta.env.DEV) {
  console.log(
    `%c[Easy Agent 配置信息]\n` +
    `  环境: ${BUILD_MODE}\n` +
    `  后端地址: ${API_BASE_URL}\n` +
    `  应用名称: ${APP_TITLE}\n` +
    `  构建时间: ${new Date().toLocaleString()}`,
    'color: #0ea5e9; font-weight: bold; font-size: 12px;'
  )
}
