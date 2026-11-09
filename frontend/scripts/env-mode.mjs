/**
 * 根据「运行平台 + AGENT_ENV」解析 Vite 的 --mode：
 *   Windows（含 AGENT_WIN=true 模拟） -> win
 *   Linux + AGENT_ENV=dev|test|prod -> dev / test / prod
 *   其余默认：build -> prod，dev -> dev
 *
 * MODE 只用于 Vite 构建标识，不再对应任何 .env 文件。
 */
export function resolveEnvMode(command) {
  const isWindows =
    process.platform === 'win32' ||
    (process.env.AGENT_WIN || '').toLowerCase() === 'true'

  if (isWindows) return 'win'

  const agentEnv = (process.env.AGENT_ENV || '').toLowerCase()
  if (agentEnv === 'dev') return 'dev'
  if (agentEnv === 'test') return 'test'
  if (agentEnv === 'prod') return 'prod'

  // 默认：构建用 prod，开发用 dev
  return command === 'build' ? 'prod' : 'dev'
}
