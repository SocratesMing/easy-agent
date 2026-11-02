/**
 * 运行期前端配置生成器（node 入口，跨平台）。
 *
 * 背景：原 shell 版本依赖 bash（以及调用方用 sh 时 BASH_SOURCE 会报 Bad substitution），
 * 在 ARM Linux / Alpine 等只有 dash / busybox ash、甚至没有 bash 的精简镜像上构建会失败。
 * 现在生成逻辑放在本文件（npm run build / serve 直接调用 node），shell 脚本只做 POSIX 包装。
 *
 * 行为与历史 shell 版本保持一致：
 * - 读取 cwd 下各 .env.<mode> 的 VITE_API_BASE_URL（CRLF 会去掉 \r），构造 ENV_CONFIG 表；
 * - 后端地址优先级：显式 API_BASE_URL > .env.<AGENT_ENV>（仅显式设置 AGENT_ENV 时）> "/"；
 * - 默认 AGENT_ENV=prod、APP_TITLE=Easy Agent、DIST_DIR=dist；
 * - 所有值交给 JSON.stringify 转义后写 dist/runtime-config.js（值里有引号 / 反斜杠 /
 *   换行也不会生成非法 JS）。
 *
 * 用法（在 frontend/ 下）：
 *   node scripts/generate-runtime-config.mjs
 *   AGENT_ENV=test API_BASE_URL=http://x:8000 node scripts/generate-runtime-config.mjs
 */
import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'

import { renderRuntimeConfig } from './runtime-config.mjs'

const ENV_FILE_PREFIX = 'VITE_API_BASE_URL='

/**
 * 读取 .env.<mode> 中的 VITE_API_BASE_URL（与旧 shell 实现逐字对齐）：
 * 取最后一条匹配行、删掉所有 \r、去掉一层首尾引号。
 * @param {string} mode dev / test / prod
 * @returns {string}
 */
export function readEnvApi(mode) {
  const file = `.env.${mode}`
  if (!fs.existsSync(file)) return ''
  const lines = fs.readFileSync(file, 'utf-8').split('\n')
  let value = ''
  for (const line of lines) {
    if (!line.startsWith(ENV_FILE_PREFIX)) continue
    value = line
      .slice(ENV_FILE_PREFIX.length)
      .replace(/\r/g, '')
      .replace(/^["']/, '')
      .replace(/["']$/, '')
  }
  return value
}

/**
 * 计算本轮运行期配置（不写盘，便于单测）。
 * @param {NodeJS.ProcessEnv} [env]
 * @param {(mode: string) => string} [readApi]
 */
export function resolveRuntimeConfig(env = process.env, readApi = readEnvApi) {
  const agentEnvSet = env.AGENT_ENV || ''
  const agentEnv = agentEnvSet || 'prod'
  const devApi = readApi('dev')
  const testApi = readApi('test')
  const prodApi = readApi('prod')
  const envApi = agentEnvSet ? readApi(agentEnv) : ''

  let api
  let apiSource
  if (env.API_BASE_URL) {
    api = env.API_BASE_URL
    apiSource = '显式 API_BASE_URL (跨域，需后端开启 CORS)'
  } else if (envApi) {
    api = envApi
    apiSource = `.env.${agentEnv} 的 VITE_API_BASE_URL (按 AGENT_ENV 动态加载)`
  } else {
    api = '/'
    apiSource = '相对路径 / (同源访问，无需跨域)'
  }

  return {
    config: {
      AGENT_ENV: agentEnv,
      API_BASE_URL: api,
      APP_TITLE: envOrDefaultIn(env, 'APP_TITLE', 'Easy Agent'),
      APP_WELCOME_TITLE: envOrDefaultIn(env, 'APP_WELCOME_TITLE', ''),
      ENV_CONFIG: {
        dev: { API_BASE_URL: devApi },
        test: { API_BASE_URL: testApi },
        prod: { API_BASE_URL: prodApi },
      },
    },
    distDir: env.DIST_DIR || 'dist',
    apiSource,
    devApi,
    testApi,
    prodApi,
  }
}

/** 同 envOrDefault，但读取传入的 env 对象（便于单测） */
function envOrDefaultIn(env, name, fallback) {
  const value = env[name]
  return value === undefined || value === '' ? fallback : value
}

function main() {
  const { config, distDir, apiSource, devApi, testApi, prodApi } = resolveRuntimeConfig()
  const outFile = path.join(distDir, 'runtime-config.js')
  fs.mkdirSync(path.dirname(path.resolve(outFile)), { recursive: true })
  fs.writeFileSync(outFile, renderRuntimeConfig(config), 'utf-8')

  const welcome = config.APP_WELCOME_TITLE || '<使用构建期配置>'
  console.log(`==> 已生成运行期前端配置: ${outFile}`)
  console.log(`    AGENT_ENV        : ${config.AGENT_ENV}`)
  console.log(`    生效后端地址     : ${config.API_BASE_URL}  (来源: ${apiSource})`)
  console.log(`    首页欢迎语       : ${welcome}`)
  console.log(`    各环境默认地址   : dev=${devApi || '<空>'}  test=${testApi || '<空>'}  prod=${prodApi || '<空>'}`)
}

main()
