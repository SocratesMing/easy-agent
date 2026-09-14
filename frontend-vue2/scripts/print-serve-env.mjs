/**
 * 前端静态托管（serve -s dist/）启动前置脚本：打印当前生效的部署信息。
 *
 * 背景：前端所有接口一律使用相对路径（/agent/...），后端地址不再写入 bundle，
 * 也不再生成运行期配置。因此静态托管时必须保证「同源」：
 *   · 推荐：由后端 FastAPI 直接托管 dist/（访问 http://<backend>/ 即可）；
 *   · 或用 Nginx 等反向代理把 /agent 转发到后端；
 * 否则接口会打到静态服务器自身端口导致 404。
 *
 * 用法（在 frontend-vue2/ 下）：
 *   npm run serve
 */
import fs from 'node:fs'
import path from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { resolveEnvMode } from './env-mode.mjs'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')
const require = createRequire(import.meta.url)

// serve 面向已构建产物，mode 解析与 build 保持一致（默认 prod）
const mode = resolveEnvMode('build')
const envFile = path.join(root, `.env.${mode}`)
const distIndex = path.join(root, 'dist', 'index.html')

/** 读取 vue.config.js 中 devServer.proxy 的代理目标（后端地址唯一定义在 vue.config.js） */
function loadProxyTarget() {
  try {
    // vue.config.js 依赖 VUE_APP_AGENT_ENV 判断环境（由 .env.<mode> 提供），此处对齐
    if (!process.env.VUE_APP_AGENT_ENV) process.env.VUE_APP_AGENT_ENV = mode
    const config = require(path.join(root, 'vue.config.js'))
    const proxy = config && config.devServer && config.devServer.proxy
    const entry = proxy && proxy['/agent']
    return (entry && entry.target) || ''
  } catch (e) {
    return ''
  }
}

const proxyTarget = loadProxyTarget()
const distReady = fs.existsSync(distIndex)

const title = 'Easy Agent Frontend — 静态托管启动'
const rows = [
  `  构建模式        : ${mode}  (${fs.existsSync(envFile) ? path.basename(envFile) : `.env.${mode} 不存在`})`,
  `  接口地址策略    : 相对路径 (/agent/...) —— 必须与后端同源`,
  `  后端地址参考    : ${proxyTarget || '见 vue.config.js 的 PROXY_TARGETS'}`,
  `  构建产物        : ${distReady ? 'dist/index.html 已就绪' : '⚠ 未找到 dist/index.html，请先执行 npm run build'}`,
  `  静态目录        : dist/`,
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

if (!distReady) {
  process.exit(1)
}
