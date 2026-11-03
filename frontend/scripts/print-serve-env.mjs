/**
 * 前端静态托管（serve -s dist/）启动前置脚本。
 *
 * 由于 .env.<mode> 中的 VITE_* 变量在 `npm run build` 时已被固化进 bundle，
 * `serve` 运行期不会读取 .env。本脚本在启动 serve 之前：
 *   1) 根据 AGENT_ENV 读取对应 .env.<mode>（构建期配置，仅作参考）；
 *   2) 读取 dist/runtime-config.js（运行期配置，由 generate-runtime-config.mjs
 *      在启动时生成；跨平台，不依赖 bash），展示真正生效的后端地址。
 *
 * 生成文件是 `window.__RUNTIME_CONFIG__ = <JSON>;`：解析时去掉前缀与结尾分号后
 * 直接 JSON.parse，不再用正则逐键抽取（值和键都可能含引号 / 反斜杠 / 换行）。
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
import { parseRuntimeConfig } from './runtime-config.mjs'

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
  const fallback = { ENV_CONFIG: {} }
  if (!fs.existsSync(file)) return fallback
  try {
    const cfg = parseRuntimeConfig(fs.readFileSync(file, 'utf-8'))
    return { ...cfg, ENV_CONFIG: cfg.ENV_CONFIG || {} }
  } catch (err) {
    // 未构建时 dist 里可能仍是 public/ 的占位文件；文件被改坏也不阻塞 serve，只提示回退
    console.warn(`[警告] 无法解析 ${path.relative(root, file)}：${err.message}`)
    return fallback
  }
}

/** 取 ENV_CONFIG.<env>.API_BASE_URL（每项是 { API_BASE_URL } 对象） */
function envApi(cfg, envKey) {
  return cfg.ENV_CONFIG?.[envKey]?.API_BASE_URL || '<空>'
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
  `  首页欢迎语      : ${runtime.APP_WELCOME_TITLE || env.VITE_APP_WELCOME_TITLE || 'Easy Agent，让工作更简单'}`,
  `  静态目录        : dist/`,
  `  环境配置表      : dev=${envApi(runtime, 'dev')}  test=${envApi(runtime, 'test')}  prod=${envApi(runtime, 'prod')}`,
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
