import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Icons from 'unplugin-icons/vite'
import monacoEditorPlugin from 'vite-plugin-monaco-editor'
import { resolveEnvMode } from './scripts/env-mode.mjs'

/**
 * 平台 / AGENT_ENV -> 构建 MODE（scripts/run-vite.mjs 通过 --mode 传入）。
 * 不再加载 .env.<mode>：后端地址与标题固定在同一份代码里。
 *
 * 开发代理：前端请求一律走同源相对路径 /agent/...（src/config.js 的 API_BASE_URL 为空），
 * dev server 把 /agent 转发到按 mode 从下表选出的后端（地址沿用原 .env.<mode> 的
 * VITE_API_BASE_URL）。临时连别的后端时设 AGENT_PROXY_TARGET 覆盖，不必改表；
 * 它只在 dev server 进程里读取，不会进入构建产物。
 */
const PROXY_TARGETS = {
  dev: 'http://localhost:8080',
  test: 'http://192.168.1.100:8007',
  prod: 'http://192.168.1.200:8000',
  win: 'http://127.0.0.1:8080',
}

export default defineConfig(() => {
  const effectiveMode = resolveEnvMode(process.argv.includes('build') ? 'build' : 'dev')
  const proxyTarget =
    (process.env.AGENT_PROXY_TARGET || '').trim() ||
    PROXY_TARGETS[effectiveMode] ||
    PROXY_TARGETS.dev

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
║  开发代理目标:     ${proxyTarget}  (仅 npm run dev 生效)
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
      // 开发代理：/agent 前缀的接口（REST + SSE 流式）全部转发到后端
      proxy: {
        '/agent': {
          target: proxyTarget,
          changeOrigin: true,
          // SSE 长连接：禁止中间层缓存/攒包，保证流式输出逐步到达
          configure(proxy) {
            proxy.on('proxyRes', (proxyRes) => {
              proxyRes.headers['cache-control'] = 'no-cache'
            })
          },
        },
      },
    },
  }
})
