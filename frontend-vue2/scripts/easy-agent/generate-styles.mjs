#!/usr/bin/env node
/**
 * 生成业务模块内的限域第三方样式：src/packages/easyAgent/styles/highlight-github-dark.css
 *
 * 原来 main.js 全局引入 highlight.js/styles/github-dark.css（.hljs / .hljs-keyword …），嵌入宿主后
 * 会把宿主自己的代码高亮也染成这套配色。这里在每个选择器前加 `:where(.easy-agent-root) `：
 * 只作用于模块内，且 :where() 不增加优先级，组件里对 .hljs 的覆盖照旧生效。
 * 输出可重复生成（随迁移包同步），内网不需要再做任何前缀加工。
 *
 * 用法：
 *   node scripts/easy-agent/generate-styles.mjs           # 写入
 *   node scripts/easy-agent/generate-styles.mjs --check   # 只校验，不一致退出码 1
 */
import { createRequire } from 'module'
import { existsSync, readFileSync, writeFileSync } from 'fs'
import { join } from 'path'
import { FRONTEND_ROOT, MODULE_ROOT } from './lib.mjs'

const require = createRequire(join(FRONTEND_ROOT, 'package.json'))
const SCOPE = ':where(.easy-agent-root)'

export const SCOPED_STYLES = [
  {
    source: 'highlight.js/styles/github-dark.css',
    pkg: 'highlight.js',
    target: join(MODULE_ROOT, 'styles', 'highlight-github-dark.css'),
  },
]

/** 给规则选择器加前缀（不处理 @media 等嵌套：目标文件没有，遇到即报错，避免静默生成错误结果） */
export function scopeCss(css, scope = SCOPE) {
  let out = ''
  let i = 0
  while (i < css.length) {
    if (css.startsWith('/*', i)) {
      const end = css.indexOf('*/', i + 2)
      const stop = end === -1 ? css.length : end + 2
      out += css.slice(i, stop)
      i = stop
      continue
    }
    const open = css.indexOf('{', i)
    if (open === -1) {
      out += css.slice(i)
      break
    }
    const comment = css.indexOf('/*', i)
    if (comment !== -1 && comment < open) {
      out += css.slice(i, comment)
      i = comment
      continue
    }
    const selector = css.slice(i, open)
    if (selector.trim().startsWith('@')) throw new Error(`[styles] 不支持的 at-rule：${selector.trim()}`)
    const close = css.indexOf('}', open)
    if (close === -1) throw new Error('[styles] CSS 不完整')
    const leading = selector.match(/^\s*/)[0]
    const scoped = selector
      .trim()
      .split(',')
      .map((part) => `${scope} ${part.trim()}`)
      .join(',\n')
    out += `${leading}${scoped} ${css.slice(open, close + 1)}`
    i = close + 1
  }
  return out
}

export function renderScoped(item) {
  const sourceFile = require.resolve(item.source)
  const version = require(`${item.pkg}/package.json`).version
  const header = [
    '/* ⚠️ 自动生成，请勿直接编辑：scripts/easy-agent/generate-styles.mjs（npm run styles:easy-agent）',
    `   来源：${item.source}（${item.pkg} ${version}，BSD-3-Clause），选择器限定在 .easy-agent-root 内 */`,
    '',
  ].join('\n')
  return `${header}${scopeCss(readFileSync(sourceFile, 'utf8'))}`
}

/** 返回过期 / 缺失的生成文件；源包未安装时返回 null */
export function checkStyles() {
  const stale = []
  for (const item of SCOPED_STYLES) {
    let expected
    try {
      expected = renderScoped(item)
    } catch (error) {
      if (error && error.code === 'MODULE_NOT_FOUND') return null
      throw error
    }
    const current = existsSync(item.target) ? readFileSync(item.target, 'utf8') : null
    if (current !== expected) stale.push(item.target)
  }
  return stale
}

function main() {
  if (process.argv.includes('--check')) {
    const stale = checkStyles()
    if (stale === null) {
      console.error('[styles] 依赖未安装，无法校验限域样式（npm install 后重试）')
      process.exit(1)
    }
    if (stale.length) {
      console.error('[styles] 限域样式与生成器不一致，请执行 npm run styles:easy-agent：')
      for (const file of stale) console.error(`  - ${file}`)
      process.exit(1)
    }
    console.log('[styles] 限域样式与生成器一致')
    return
  }
  for (const item of SCOPED_STYLES) {
    writeFileSync(item.target, renderScoped(item))
    console.log(`[styles] 已生成 ${item.target}`)
  }
}

if (process.argv[1] && process.argv[1].endsWith('generate-styles.mjs')) main()
