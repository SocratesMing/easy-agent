#!/usr/bin/env node
/**
 * 生成业务模块自带的图标资源：src/packages/easyAgent/assets/icons/<集合>.js
 *
 * 原来组件里写 `import X from '~icons/lucide/x'`，依赖外网 vue.config.js 里的 IconifyRawPlugin
 * （@iconify/json + 自定义 webpack 插件），宿主构建链天然解析不了。现在改为把实际用到的图标
 * 预先生成成普通 JS（导出 SVG 字符串），宿主无需任何插件；输出与原插件（unplugin-icons
 * `compiler: 'raw'`）逐字节一致，外观不变。许可信息写在生成文件头部。
 *
 * 新增图标：在下方 ICONS 里加名字 → npm run icons:easy-agent → 组件里按导出名引用。
 *
 * 用法：
 *   node scripts/easy-agent/generate-icons.mjs           # 写入
 *   node scripts/easy-agent/generate-icons.mjs --check   # 只校验（需已安装 @iconify/json），不一致退出码 1
 */
import { createRequire } from 'module'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'fs'
import { join } from 'path'
import { FRONTEND_ROOT, MODULE_ROOT } from './lib.mjs'

const require = createRequire(join(FRONTEND_ROOT, 'package.json'))
export const ICONS_DIR = join(MODULE_ROOT, 'assets', 'icons')
const SCALE = 1.2

/** 模块实际用到的图标（集合 → 图标名） */
export const ICONS = {
  lucide: [
    'activity', 'arrow-left', 'arrow-up', 'arrow-up-right', 'book-dashed', 'book-open', 'building-2',
    'check', 'chevron-down', 'chevron-right', 'circle-alert', 'circle-check', 'clock-3', 'database',
    'download', 'eye', 'file-clock', 'file-text', 'files', 'folder', 'folder-plus', 'pause', 'play',
    'layout-grid', 'library', 'list', 'list-filter', 'message-square-plus', 'panel-right-close', 'pencil',
    'plus', 'refresh-cw', 'search', 'settings-2', 'shield-check', 'sparkles', 'square', 'trash-2',
    'triangle-alert', 'upload', 'upload-cloud', 'user-round', 'user-round-cog', 'users', 'x',
  ],
  'vscode-icons': [
    'file-type-pdf2', 'file-type-word', 'file-type-excel', 'file-type-powerpoint', 'file-type-text',
    'file-type-markdown', 'file-type-image', 'file-type-python', 'file-type-js-official',
    'file-type-typescript-official', 'file-type-vue', 'file-type-html', 'file-type-css', 'file-type-json',
    'file-type-java', 'file-type-go', 'file-type-rust', 'file-type-c', 'file-type-cpp', 'file-type-sql',
    'file-type-zip', 'default-file',
  ],
}

/** 图标名 → 导出名（kebab-case → camelCase） */
export function exportName(name) {
  return name.replace(/-([a-z0-9])/g, (_, c) => c.toUpperCase())
}

function resolveIcon(data, collection, name) {
  let icon = data.icons[name]
  let alias = data.aliases && data.aliases[name]
  const merged = {}
  while (!icon && alias) {
    Object.assign(merged, alias, merged)
    icon = data.icons[alias.parent]
    alias = data.aliases && data.aliases[alias.parent]
  }
  if (!icon) throw new Error(`[icons] 图标不存在：${collection}/${name}`)
  const pick = (key, fallback) => (merged[key] ?? icon[key] ?? data[key] ?? fallback)
  return {
    body: icon.body,
    left: pick('left', 0),
    top: pick('top', 0),
    width: pick('width', 16),
    height: pick('height', 16),
  }
}

/** 与原 IconifyRawPlugin 输出一致的 SVG 字符串 */
function toSvg(icon) {
  const ratio = icon.width / icon.height
  const width = `${+(SCALE * ratio).toFixed(4)}em`
  return `<svg viewBox="${icon.left} ${icon.top} ${icon.width} ${icon.height}" width="${width}" height="${SCALE}em" >${icon.body}</svg>`
}

export function renderCollection(collection) {
  const data = require(`@iconify/json/json/${collection}.json`)
  const pkg = require('@iconify/json/package.json')
  const info = data.info || {}
  const license = info.license || {}
  const author = info.author || {}
  const lines = [
    '// ⚠️ 自动生成，请勿直接编辑：scripts/easy-agent/generate-icons.mjs（npm run icons:easy-agent）',
    `// 图标集：${info.name || collection}${info.version ? ` ${info.version}` : ''}（来源 @iconify/json ${pkg.version}）`,
    `// 作者：${author.name || '未知'}${author.url ? ` ${author.url}` : ''}`,
    `// 许可：${license.spdx || license.title || '未知'}${license.url ? ` ${license.url}` : ''}`,
    '// 每个导出是一段 SVG 字符串（1.2em 高），用法与原 `~icons/<集合>/<名字>` 的默认导出相同。',
    '',
  ]
  for (const name of ICONS[collection]) {
    lines.push(`export const ${exportName(name)} = ${JSON.stringify(toSvg(resolveIcon(data, collection, name)))}`)
  }
  return `${lines.join('\n')}\n`
}

export function iconFiles() {
  return Object.keys(ICONS).map((collection) => ({ collection, file: join(ICONS_DIR, `${collection}.js`) }))
}

/** 返回过期 / 缺失的生成文件；@iconify/json 未安装时返回 null（无法校验） */
export function checkIcons() {
  try {
    require.resolve('@iconify/json/package.json')
  } catch (_) {
    return null
  }
  const stale = []
  for (const { collection, file } of iconFiles()) {
    const expected = renderCollection(collection)
    const current = existsSync(file) ? readFileSync(file, 'utf8') : null
    if (current !== expected) stale.push(file)
  }
  return stale
}

function main() {
  const checkOnly = process.argv.includes('--check')
  if (checkOnly) {
    const stale = checkIcons()
    if (stale === null) {
      console.error('[icons] 未安装 @iconify/json，无法校验图标资源（npm install 后重试）')
      process.exit(1)
    }
    if (stale.length) {
      console.error('[icons] 图标资源与生成器不一致，请执行 npm run icons:easy-agent：')
      for (const file of stale) console.error(`  - ${file}`)
      process.exit(1)
    }
    console.log('[icons] 图标资源与生成器一致')
    return
  }
  mkdirSync(ICONS_DIR, { recursive: true })
  for (const { collection, file } of iconFiles()) {
    writeFileSync(file, renderCollection(collection))
    console.log(`[icons] 已生成 ${file}（${ICONS[collection].length} 个）`)
  }
}

if (process.argv[1] && process.argv[1].endsWith('generate-icons.mjs')) main()
