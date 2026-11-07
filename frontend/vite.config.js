import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Icons from 'unplugin-icons/vite'
import monacoEditorPlugin from 'vite-plugin-monaco-editor'
import { resolveEnvMode } from './scripts/env-mode.mjs'

/**
 * 平台 / AGENT_ENV -> 构建 MODE（scripts/run-vite.mjs 通过 --mode 传入）。
 * 不再加载 .env.<mode>：后端地址与标题固定在同一份代码里。
 */

export default defineConfig(() => {
  const effectiveMode = resolveEnvMode(process.argv.includes('build') ? 'build' : 'dev')

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
║  后端地址 (API):   同源相对路径 /agent/...
║  应用名称 (title): Easy Agent
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
