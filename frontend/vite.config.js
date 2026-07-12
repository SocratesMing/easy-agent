import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import Icons from 'unplugin-icons/vite'
import monacoEditorPlugin from 'vite-plugin-monaco-editor'

/**
 * AGENT_ENV -> Vite mode 映射：
 *   dev  -> development  -> .env.development
 *   test -> test         -> .env.test
 *   prod -> production   -> .env.production
 *
 * 容器化部署时只需设置 AGENT_ENV 环境变量，无需 --mode 参数。
 */
function resolveMode(defaultMode) {
  const agentEnv = (process.env.AGENT_ENV || '').toLowerCase()
  if (agentEnv === 'dev') return 'development'
  if (agentEnv === 'test') return 'test'
  if (agentEnv === 'prod') return 'production'
  return defaultMode
}

export default defineConfig(({ mode }) => {
  const effectiveMode = resolveMode(mode)
  const env = loadEnv(effectiveMode, process.cwd(), '')
  const envFile = `.env.${effectiveMode}`

  // 根据操作系统选择默认监听地址：
  //   Linux / macOS -> 0.0.0.0（允许局域网访问，适合服务器/容器）
  //   Windows       -> 127.0.0.1（仅本机访问，避免 Windows 防火墙弹窗）
  const isWindows = process.platform === 'win32'
  const defaultHost = isWindows ? '127.0.0.1' : '0.0.0.0'

  // 启动/构建时在终端输出配置信息
  const banner = `
╔══════════════════════════════════════════════════╗
║           Easy Agent Frontend                    ║
╠══════════════════════════════════════════════════╣
║  AGENT_ENV:        ${process.env.AGENT_ENV || '(未设置, 使用默认)'}
║  环境模式 (mode):  ${effectiveMode}
║  配置文件:         ${envFile}
║  后端地址 (API):   ${env.VITE_API_BASE_URL || 'http://localhost:8000 (默认)'}
║  应用名称 (title): ${env.VITE_APP_TITLE || 'Easy Agent'}
║  监听地址 (host):  ${defaultHost}  (${isWindows ? 'Windows' : 'Linux/macOS'})
╚══════════════════════════════════════════════════╝
`
  console.log(banner)

  return {
    plugins: [
      vue(),
      Icons({
        autoInstall: true,
      }),
      monacoEditorPlugin.default({})
    ],
    server: {
      host: defaultHost,
    },
  }
})
