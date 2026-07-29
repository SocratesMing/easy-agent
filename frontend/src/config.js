/**
 * 前端全局配置（集中管理）
 *
 * 后端地址优先级：运行期 runtime-config.js > 构建期 .env.[mode] > 相对路径。
 * - 运行期：启动脚本（docker/entrypoint.sh 或 scripts/generate-runtime-config.sh）
 *   根据容器/serve 启动时的环境变量写出 dist/runtime-config.js，
 *   使“构建镜像时未知 AGENT_ENV / 后端地址”也能在部署时动态确定。
 * - 构建期：.env.win / .env.dev / .env.test / .env.prod 注入 VITE_API_BASE_URL。
 */

import { reactive } from 'vue'

// 运行期配置（serve / 容器启动脚本生成的 dist/runtime-config.js）。
// 其中 ENV_CONFIG 是按环境（dev/test/prod）预置的“环境专属配置表”，
// 前端 build 后据此按 AGENT_ENV 值加载对应环境的后端地址。
const _rt = (typeof window !== 'undefined' && window.__RUNTIME_CONFIG__) || {}
const _envConfig = _rt.ENV_CONFIG || {}

// 当前运行环境标识：优先用运行期注入的 AGENT_ENV，否则回退构建期 MODE。
export const AGENT_ENV = _rt.AGENT_ENV || import.meta.env.MODE || 'prod'

// 后端地址解析优先级（高 -> 低）：
//   1) 运行期显式覆盖：window.__RUNTIME_CONFIG__.API_BASE_URL；
//   2) 当前环境默认地址：ENV_CONFIG[AGENT_ENV].API_BASE_URL（按 AGENT_ENV 值加载）；
//   3) 构建期固化值：VITE_API_BASE_URL（来自 .env.[mode]，已打进 bundle）；
//   4) 兜底：空字符串 → '/api/...' 相对路径，自动跟随页面当前 origin。
// 末尾斜杠统一去除，避免拼出 "//api/..." 这种错误协议相对地址。
const _envApiBase = _envConfig[AGENT_ENV]?.API_BASE_URL || ''
const _runtimeApiBase = _rt.API_BASE_URL || _envApiBase || import.meta.env.VITE_API_BASE_URL || ''
export const API_BASE_URL = (_runtimeApiBase ?? '').replace(/\/+$/, '')
export const APP_TITLE = _rt.APP_TITLE || import.meta.env.VITE_APP_TITLE || 'Easy Agent'
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

// 加载运行期配置时即用 AGENT_ENV 初始化（即便后端 /api/auth/config 尚未返回，
// 前端也已知道当前处于哪个环境，可据此加载对应环境配置）。
appRuntime.agentEnv = AGENT_ENV

// 浏览器侧始终打印当前生效配置（含生产模式），便于在控制台核对实际加载的环境
console.log(
  `%c[Easy Agent 配置信息] (${BUILD_MODE})\n` +
    `  运行环境: ${AGENT_ENV}\n` +
    `  后端地址: ${API_BASE_URL}\n` +
    `  应用名称: ${APP_TITLE}\n` +
    `  构建时间: ${new Date().toLocaleString()}`,
  BUILD_MODE === 'development'
    ? 'color: #0ea5e9; font-weight: bold; font-size: 12px;'
    : 'color: #64748b; font-size: 12px;'
)
