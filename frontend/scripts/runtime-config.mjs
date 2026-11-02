/**
 * 运行期前端配置（window.__RUNTIME_CONFIG__）的渲染与解析。
 *
 * 生成端（generate-runtime-config.mjs）和读取端（print-serve-env.mjs）共用这里的
 * 文件格式，避免两边各写一套转义 / 正则后对不上。
 *
 * 运行期配置的值来自容器 / serve 启动时的环境变量（API_BASE_URL、APP_TITLE 等），
 * 可能包含 `"`、`\`、换行、CR 等字符，因此统一交给 JSON.stringify 转义，
 * 不再手工拼 JS 字符串字面量：值里的引号 / 反斜杠不会破坏语法，也不会变成代码。
 */

/** 运行期配置挂到 window 上的变量名（生成文件与解析端共用） */
export const RUNTIME_CONFIG_MARKER = 'window.__RUNTIME_CONFIG__'

/**
 * 把配置对象渲染成 dist/runtime-config.js 的完整文本。
 * 值全部经 JSON.stringify 转义；输出结构：
 *   window.__RUNTIME_CONFIG__ = { ... };
 * @param {object} config 运行期配置对象
 * @returns {string}
 */
export function renderRuntimeConfig(config) {
  const json = JSON.stringify(config, null, 2)
  return [
    '// 本文件由启动脚本根据运行期环境变量自动生成，请勿手工编辑。',
    '// 前端运行时根据 AGENT_ENV 值从 ENV_CONFIG 加载对应环境配置。',
    `${RUNTIME_CONFIG_MARKER} = ${json};`,
    '',
  ].join('\n')
}

/**
 * 解析 dist/runtime-config.js 文本，返回配置对象。
 *
 * 兼容文件头部注释行：先定位 `window.__RUNTIME_CONFIG__ =`，去掉结尾的 `;`
 * 后按 JSON 解析（与 renderRuntimeConfig 的输出严格对应）。
 * @param {string} text 文件内容
 * @returns {object} 运行期配置对象
 */
export function parseRuntimeConfig(text) {
  const markerAt = text.indexOf(RUNTIME_CONFIG_MARKER)
  if (markerAt === -1) {
    throw new Error(`文件里没有 ${RUNTIME_CONFIG_MARKER}`)
  }
  const eqAt = text.indexOf('=', markerAt + RUNTIME_CONFIG_MARKER.length)
  if (eqAt === -1) {
    throw new Error(`${RUNTIME_CONFIG_MARKER} 后缺少 "="`)
  }
  let body = text.slice(eqAt + 1).trim()
  if (body.endsWith(';')) body = body.slice(0, -1).trim()
  return JSON.parse(body)
}
