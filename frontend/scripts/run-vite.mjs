/**
 * Vite 启动器：根据平台与 AGENT_ENV 解析 --mode，
 * 让 Vite 自动加载对应的 .env.<mode> 文件。
 * 用法（package.json 脚本已接好）：
 *   node scripts/run-vite.mjs dev     # 对应 npm run dev
 *   node scripts/run-vite.mjs build   # 对应 npm run build
 */
import { spawnSync } from 'node:child_process'
import { resolveEnvMode } from './env-mode.mjs'

const command = process.argv[2] || 'dev'
const mode = resolveEnvMode(command)

const viteArgs = command === 'build' ? ['build', '--mode', mode] : ['--mode', mode]

const res = spawnSync('vite', viteArgs, {
  stdio: 'inherit',
  shell: process.platform === 'win32',
})
process.exit(res.status ?? 0)
