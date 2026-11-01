/**
 * 根据环境变量写出 dist/runtime-config.js（运行期前端配置）。
 *
 * 由 generate-runtime-config.sh 调用：shell 负责“读 .env.<AGENT_ENV> + 地址优先级”
 * 这些既有业务逻辑，算好的值通过 RT_* 环境变量传进来（不经过 shell 二次解析）；
 * 本脚本只负责用 JSON.stringify 做转义与写盘，不再手工拼 JS 字符串。
 *
 * 用法（在 frontend/ 下）：
 *   node scripts/write-runtime-config.mjs <输出文件>
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { renderRuntimeConfig } from './runtime-config.mjs'

/** 输入值的环境变量名（由 generate-runtime-config.sh 注入） */
const ENV_KEYS = {
  AGENT_ENV: 'RT_AGENT_ENV',
  API_BASE_URL: 'RT_API_BASE_URL',
  APP_TITLE: 'RT_APP_TITLE',
  APP_WELCOME_TITLE: 'RT_APP_WELCOME_TITLE',
  DEV_API: 'RT_ENV_DEV',
  TEST_API: 'RT_ENV_TEST',
  PROD_API: 'RT_ENV_PROD',
}

/** 统一成字符串：未设置按空串处理，避免 JSON.stringify 丢掉键 */
function toText(value) {
  return value == null ? '' : String(value)
}

/**
 * 从环境变量构造运行期配置对象（键 / 结构与历史生成结果保持一致）。
 * @param {NodeJS.ProcessEnv} [env]
 * @returns {{AGENT_ENV: string, API_BASE_URL: string, APP_TITLE: string,
 *   APP_WELCOME_TITLE: string, ENV_CONFIG: Record<string, {API_BASE_URL: string}>}}
 */
export function configFromEnv(env = process.env) {
  return {
    AGENT_ENV: toText(env[ENV_KEYS.AGENT_ENV]),
    API_BASE_URL: toText(env[ENV_KEYS.API_BASE_URL]),
    APP_TITLE: toText(env[ENV_KEYS.APP_TITLE]),
    APP_WELCOME_TITLE: toText(env[ENV_KEYS.APP_WELCOME_TITLE]),
    ENV_CONFIG: {
      dev: { API_BASE_URL: toText(env[ENV_KEYS.DEV_API]) },
      test: { API_BASE_URL: toText(env[ENV_KEYS.TEST_API]) },
      prod: { API_BASE_URL: toText(env[ENV_KEYS.PROD_API]) },
    },
  }
}

/**
 * 生成运行期配置文件（自动创建输出目录）。
 * @param {string} outFile 输出文件路径（如 dist/runtime-config.js）
 * @param {NodeJS.ProcessEnv} [env] 环境变量来源，默认 process.env（便于单测注入）
 * @returns {string} 写出的文件内容
 */
export function writeRuntimeConfig(outFile, env = process.env) {
  const text = renderRuntimeConfig(configFromEnv(env))
  fs.mkdirSync(path.dirname(path.resolve(outFile)), { recursive: true })
  fs.writeFileSync(outFile, text, 'utf-8')
  return text
}

/** 是否被 node 直接执行（被 import 时不写文件） */
function isMainModule() {
  const entry = process.argv[1]
  if (!entry) return false
  return path.resolve(entry) === fileURLToPath(import.meta.url)
}

if (isMainModule()) {
  const outFile = process.argv[2]
  if (!outFile) {
    console.error('用法: node scripts/write-runtime-config.mjs <输出文件>')
    process.exit(2)
  }
  writeRuntimeConfig(outFile)
}
