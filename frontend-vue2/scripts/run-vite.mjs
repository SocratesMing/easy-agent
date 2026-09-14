import { spawnSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { resolveEnvMode } from './env-mode.mjs'

const require = createRequire(import.meta.url)
const root = resolve(import.meta.dirname, '..')
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

/** 读取 vue.config.js 中 devServer.proxy 的代理目标（后端地址唯一定义在 vue.config.js） */
function loadProxyTarget() {
  try {
    // vue.config.js 通过 VUE_APP_AGENT_ENV 判断当前环境，而该变量由 vue-cli 在加载
    // .env.<mode> 时注入；这里手动对齐，保证横幅显示的是当前 mode 对应的代理目标。
    const envVars = loadEnvVars(envFile)
    Object.keys(envVars).forEach((k) => {
      if (!process.env[k]) process.env[k] = envVars[k]
    })
    const config = require(resolve(root, 'vue.config.js'))
    const proxy = config && config.devServer && config.devServer.proxy
    const entry = proxy && proxy['/agent']
    return (entry && entry.target) || ''
  } catch (e) {
    return ''
  }
}

function printBanner() {
  const envVars = loadEnvVars(envFile)
  const agentEnv = process.env.AGENT_ENV || envVars.VUE_APP_AGENT_ENV || '(未设置)'
  // 后端地址唯一定义在 vue.config.js（devServer.proxy 目标），此处直接读取展示
  const proxyTarget = loadProxyTarget()

  const rows = [
    `命令      : ${command}  (vue-cli-service ${args.join(' ')})`,
    `环境模式  : ${mode}  ->  ${existsSync(envFile) ? `.env.${mode}` : `.env.${mode} (文件不存在, 使用默认值)`}`,
    `AGENT_ENV : ${agentEnv}`,
    `接口地址  : 相对路径 /agent/...（同源）`,
    `代理目标  : ${proxyTarget || '(未在 vue.config.js devServer.proxy 中解析到)'}`,
    `应用名称  : Easy Agent（定义在各 Vue 组件内）`,
  ]

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
