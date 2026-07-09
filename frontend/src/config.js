/**
 * 前端全局配置（集中管理）
 *
 * 不同环境通过 .env.[mode] 文件注入 VITE_API_BASE_URL：
 * - .env.development  → npm run dev
 * - .env.test         → npm run dev:test / npm run build:test
 * - .env.production   → npm run build
 */

export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
export const APP_TITLE = import.meta.env.VITE_APP_TITLE || 'Easy Agent'
export const BUILD_MODE = import.meta.env.MODE || 'development'

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
