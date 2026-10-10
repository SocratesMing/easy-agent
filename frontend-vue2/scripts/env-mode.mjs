/**
 * 根据「运行平台 + AGENT_ENV」解析 Vite 的 --mode，
 * 使其自动加载对应的 .env.<mode> 文件：
 *   Windows（含 AGENT_WIN=true 模拟） -> win   -> .env.win
 *   Linux + AGENT_ENV=dev   -> dev  -> .env.dev
 *   Linux + AGENT_ENV=test  -> test -> .env.test
 *   Linux + AGENT_ENV=prod  -> prod -> .env.prod
 *   其余默认：build -> prod，dev -> dev
 *
 * 注意：Vite 仅在 --mode 与文件名一致时才会自动把 .env.<mode>
 * 注入到 import.meta.env，因此这里集中决定 mode，由 run-vite.mjs 传参。
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

  // 默认：构建用 prod（对应 .env.prod），开发用 dev
  return command === 'build' ? 'prod' : 'dev'
}
