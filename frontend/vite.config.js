import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import Icons from 'unplugin-icons/vite'
import monacoEditorPlugin from 'vite-plugin-monaco-editor'
import { resolveEnvMode } from './scripts/env-mode.mjs'

/**
 * 平台 / AGENT_ENV -> 加载的 .env.<mode> 文件：
 *   Windows（或 AGENT_WIN=true 模拟） -> win   -> .env.win
 *   Linux + AGENT_ENV=dev   -> dev  -> .env.dev
 *   Linux + AGENT_ENV=test  -> test -> .env.test
 *   Linux + AGENT_ENV=prod  -> prod -> .env.prod
 *
 * 具体 mode 由 scripts/run-vite.mjs 通过 --mode 传入，Vite 据此自动把
 * .env.<mode> 注入到 import.meta.env（只有 mode 与文件名一致才会注入）。
 * 容器化部署时只需设置 AGENT_ENV（Windows 上自动识别平台），无需手动 --mode。
 */

export default defineConfig(() => {
  // 命令类型由启动脚本传入的 argv 决定（build / dev）
  const command = process.argv.includes('build') ? 'build' : 'dev'
  const effectiveMode = resolveEnvMode(command)
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
║  平台:             ${isWindows ? 'Windows' : 'Linux/macOS'}
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
