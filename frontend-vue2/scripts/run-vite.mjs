import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { existsSync, readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { resolveEnvMode } from './env-mode.mjs'

const require = createRequire(import.meta.url)
// Node14 没有 import.meta.dirname（Node20+ 才支持），用 fileURLToPath 推导
const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '..')
const command = process.argv[2] || 'dev'
const mode = resolveEnvMode(command)
const envFile = resolve(root, `.env.${mode}`)
const cliPath = resolve(require.resolve('@vue/cli-service/bin/vue-cli-service.js'))
const args = command === 'build' ? ['build', '--mode', mode] : ['serve', '--mode', mode]

/**
 * 解析 .env.<mode> 中的键值对（与 vue-cli 行为一致：取 VUE_APP_*）。
 */
function loadEnvVars(file) {
  const vars = {}
  if (!existsSync(file)) return vars
  const text = readFileSync(file, 'utf8')
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

function printBanner() {
  const envVars = loadEnvVars(envFile)
  const agentEnv = process.env.AGENT_ENV || envVars.VUE_APP_AGENT_ENV || '(未设置)'
  const apiBase = envVars.VUE_APP_API_BASE_URL || '(未设置)'
  const title = envVars.VUE_APP_TITLE || '(未设置)'
  const welcome = envVars.VUE_APP_WELCOME_TITLE || '(未设置)'

  const rows = [
    `命令      : ${command}  (vue-cli-service ${args.join(' ')})`,
    `环境模式  : ${mode}  ->  ${existsSync(envFile) ? `.env.${mode}` : `.env.${mode} (文件不存在, 使用默认值)`}`,
    `AGENT_ENV : ${agentEnv}`,
    `后端地址  : ${apiBase}`,
    `应用名称  : ${title}`,
    `欢迎语    : ${welcome}`,
  ]
  if (process.env.API_BASE_URL) {
    rows.push(`API_BASE_URL 覆盖: ${process.env.API_BASE_URL} (仅 serve 静态托管时生效)`)
  }

  const width = 60
  const bar = '═'.repeat(width)
  const banner = [
    `╔${bar}╗`,
    `║${'前端环境配置'.padEnd(width)}║`,
    `╠${bar}╣`,
    ...rows.map((r) => `║  ${r.padEnd(width - 2)}║`),
    `╚${bar}╝`,
  ].join('\n')
  console.log('\n' + banner + '\n')
}

printBanner()

const result = spawnSync(process.execPath, [cliPath, ...args], {
  stdio: 'inherit',
  env: process.env,
})

process.exit(result.status ?? 0)
