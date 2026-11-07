// 需兼容 Node 14：不用 import.meta.dirname（Node 20.11+）与 node: 前缀导入
import { spawnSync } from 'child_process'
import { createRequire } from 'module'
import { dirname, resolve } from 'path'
import { fileURLToPath } from 'url'
import { resolveEnvMode } from './env-mode.mjs'

const require = createRequire(import.meta.url)
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const command = process.argv[2] || 'dev'
const mode = resolveEnvMode(command)
const cliPath = resolve(require.resolve('@vue/cli-service/bin/vue-cli-service.js'))
const args = command === 'build' ? ['build', '--mode', mode] : ['serve', '--mode', mode]

/** 读取 vue.config.js 中 devServer.proxy 的代理目标（后端地址唯一定义在 vue.config.js） */
function loadProxyTarget() {
  try {
    const config = require(resolve(root, 'vue.config.js'))
    const proxy = config && config.devServer && config.devServer.proxy
    const entry = proxy && proxy['/agent']
    return (entry && entry.target) || ''
  } catch (e) {
    return ''
  }
}

function printBanner() {
  const agentEnv = process.env.AGENT_ENV || '(未设置)'
  // 后端地址唯一定义在 vue.config.js（devServer.proxy 目标），此处直接读取展示
  const proxyTarget = loadProxyTarget()

  const rows = [
    `命令      : ${command}  (vue-cli-service ${args.join(' ')})`,
    `环境模式  : ${mode}`,
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

// vue-cli 只在 mode 为 production 时默认 NODE_ENV=production。这里的 mode 是 prod / test / win，
// 不显式设置的话构建会按 development 出包（不压缩、模块用 eval 包裹、CSS 打进 JS），
// 和 build-sdk.mjs 一样在构建时固定为 production
const env = command === 'build' ? { ...process.env, NODE_ENV: 'production' } : process.env
const result = spawnSync(process.execPath, [cliPath, ...args], {
  stdio: 'inherit',
  env,
})

process.exit(result.status ?? 0)
