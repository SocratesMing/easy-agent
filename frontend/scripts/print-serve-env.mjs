/**
 * 前端静态托管（serve -s dist/）启动前置脚本：打印固定配置与构建产物状态。
 *
 * 前端固定使用同源相对路径 /agent/...，后端由同源服务或反向代理提供；
 * 不再读取 .env.<mode>、VITE_* 变量或运行期 runtime-config.js。
 * 若静态服务器与后端不同源，请用 Nginx 等反代 /agent 到后端。
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')
const distIndex = path.join(root, 'dist', 'index.html')
const distReady = fs.existsSync(distIndex)

const title = 'Easy Agent Frontend — 静态托管启动'
const rows = [
  '  后端地址        : 同源相对路径 /agent/...',
  '  应用名称        : Easy Agent',
  `  构建产物        : ${distReady ? 'dist/index.html 已就绪' : '⚠ 未找到 dist/index.html，请先执行 npm run build'}`,
  '  静态目录        : dist/',
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
