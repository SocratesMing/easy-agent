import { reactive } from 'vue'

const runtimeConfig =
  (typeof window !== 'undefined' && window.__RUNTIME_CONFIG__) || {}
const envConfig = runtimeConfig.ENV_CONFIG || {}

export const AGENT_ENV =
  runtimeConfig.AGENT_ENV || process.env.VUE_APP_AGENT_ENV || 'prod'

const envApiBase = (envConfig[AGENT_ENV] && envConfig[AGENT_ENV].API_BASE_URL) || ''
const runtimeApiBase =
  runtimeConfig.API_BASE_URL ||
  envApiBase ||
  process.env.VUE_APP_API_BASE_URL ||
  ''
export const API_BASE_URL = runtimeApiBase.replace(/\/+$/, '')
export const APP_TITLE =
  runtimeConfig.APP_TITLE || process.env.VUE_APP_TITLE || 'Easy Agent'
export const APP_WELCOME_TITLE =
  runtimeConfig.APP_WELCOME_TITLE ||
  process.env.VUE_APP_WELCOME_TITLE ||
  `${APP_TITLE}，让工作更简单`
export const BUILD_MODE = process.env.NODE_ENV || 'development'

export const appRuntime = reactive({
  win: false,
  agentEnv: '',
})

appRuntime.agentEnv = AGENT_ENV

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

if (typeof document !== 'undefined') {
  document.title = APP_TITLE
}
