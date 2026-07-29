/**
 * 前端静态托管（serve -s dist/）启动前置脚本。
 *
 * 由于 .env.<mode> 中的 VITE_* 变量在 `npm run build` 时已被固化进 bundle，
 * `serve` 运行期不会读取 .env。本脚本在启动 serve 之前：
 *   1) 根据 AGENT_ENV 读取对应 .env.<mode>（构建期配置，仅作参考）；
 *   2) 读取 dist/runtime-config.js（运行期配置，由 generate-runtime-config.sh
 *      在启动时生成），展示真正生效的后端地址。
 *
 * 用法（在 frontend/ 下）：
 *   npm run serve                  # 默认 prod
 *   AGENT_ENV=test npm run serve   # 加载 .env.test 信息
 *   API_BASE_URL=http://x:8000 npm run serve   # 运行期指定后端地址
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { resolveEnvMode } from './env-mode.mjs'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')

// serve 面向已构建产物，mode 解析与 build 保持一致（默认 prod）
const mode = resolveEnvMode('build')
const envFile = path.join(root, `.env.${mode}`)

/** 解析 .env.<mode> 中的键值对（仅取 VITE_* 展示） */
function loadEnvVars(file) {
  const vars = {}
  if (!fs.existsSync(file)) return vars
  const text = fs.readFileSync(file, 'utf-8')
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line || line.startsWith('#')) continue
    const eq = line.indexOf('=')
    if (eq === -1) continue
    const key = line.slice(0, eq).trim()
    const val = line.slice(eq + 1).trim().replace(/^["']|["']$/g, '')
    vars[key] = val
  }
  return vars
}

/** 解析 dist/runtime-config.js（运行期配置，启动脚本生成） */
function loadRuntimeConfig() {
  const file = path.join(root, 'dist', 'runtime-config.js')
  const cfg = { ENV_CONFIG: {} }
  if (!fs.existsSync(file)) return cfg
  const text = fs.readFileSync(file, 'utf-8')
  const pick = (k) => {
    const m = text.match(new RegExp(`${k}:\\s*"([^"]*)"`))
    return m ? m[1] : undefined
  }
  const api = pick('API_BASE_URL')
  const title = pick('APP_TITLE')
  const env = pick('AGENT_ENV')
  if (api !== undefined) cfg.API_BASE_URL = api
  if (title !== undefined) cfg.APP_TITLE = title
  if (env !== undefined) cfg.AGENT_ENV = env

  // 解析 ENV_CONFIG 表：ENV_CONFIG: { dev: { API_BASE_URL: "..." }, ... }
  const block = text.match(/ENV_CONFIG:\s*\{([\s\S]*?)\}\s*\}/)
  if (block) {
    for (const envKey of ['dev', 'test', 'prod']) {
      const eb = block[1].match(
        new RegExp(`${envKey}:\\s*\\{\\s*API_BASE_URL:\\s*"([^"]*)"`)
      )
      if (eb) cfg.ENV_CONFIG[envKey] = eb[1]
    }
  }
  return cfg
}

const env = loadEnvVars(envFile)
const runtime = loadRuntimeConfig()

// AGENT_ENV 实际生效值：运行期配置优先，否则取启动环境变量，再否则默认 prod
const agentEnvVal = runtime.AGENT_ENV || process.env.AGENT_ENV || 'prod'

const title = 'Easy Agent Frontend — 静态托管启动'
const rows = [
  `  AGENT_ENV       : ${agentEnvVal}  (运行期: ${runtime.AGENT_ENV || '未写入'} | 进程变量: ${process.env.AGENT_ENV || '未设置'})`,
  `  环境模式        : ${mode}  (构建期加载 ${path.basename(envFile)})`,
  `  生效后端地址    : ${runtime.API_BASE_URL || '(未生成, 将用构建期/相对路径)'}`,
  `  构建期后端地址  : ${env.VITE_API_BASE_URL || '(未设置)'}`,
  `  应用名称        : ${runtime.APP_TITLE || env.VITE_APP_TITLE || 'Easy Agent'}`,
  `  静态目录        : dist/`,
  `  环境配置表      : dev=${runtime.ENV_CONFIG.dev || '<空>'}  test=${runtime.ENV_CONFIG.test || '<空>'}  prod=${runtime.ENV_CONFIG.prod || '<空>'}`,
]

const width = 60
const bar = '═'.repeat(width)
const banner = [
  `╔${bar}╗`,
  `║${title.padEnd(width)}║`,
  `╠${bar}╣`,
  ...rows.map((r) => `║${r.padEnd(width)}║`),
  `╚${bar}╝`,
].join('\n')

console.log('\n' + banner + '\n')
