// `~icons/<collection>/<name>` 的 Node 14 可用实现（unplugin-icons 23 / @iconify/utils 均为纯 ESM，Node 14 无法 require）。
// 输出与 unplugin-icons `compiler: 'raw'` 一致：export default '<svg viewBox=... width="1.2em" height="1.2em" >…</svg>'
const path = require('path')

const PREFIX = '~icons/'
const SCALE = 1.2

function resolveIcon(collection, name) {
  const data = require(`@iconify/json/json/${collection}.json`)
  let icon = data.icons[name]
  let alias = data.aliases && data.aliases[name]
  const merged = {}
  while (!icon && alias) {
    Object.assign(merged, alias, merged)
    icon = data.icons[alias.parent]
    alias = data.aliases && data.aliases[alias.parent]
  }
  if (!icon) throw new Error(`[iconify-raw] icon not found: ${collection}/${name}`)
  return {
    body: icon.body,
    left: merged.left ?? icon.left ?? data.left ?? 0,
    top: merged.top ?? icon.top ?? data.top ?? 0,
    width: merged.width ?? icon.width ?? data.width ?? 16,
    height: merged.height ?? icon.height ?? data.height ?? 16,
  }
}

function iconLoader() {
  const query = new URLSearchParams(this.resourceQuery.slice(1))
  const [collection, name] = query.get('icon').split('/')
  const icon = resolveIcon(collection, name)
  const ratio = icon.width / icon.height
  const width = `${+(SCALE * ratio).toFixed(4)}em`
  const svg = `<svg viewBox="${icon.left} ${icon.top} ${icon.width} ${icon.height}" width="${width}" height="${SCALE}em" >${icon.body}</svg>`
  return `export default ${JSON.stringify(svg)}`
}

class IconifyRawPlugin {
  apply(compiler) {
    const { NormalModuleReplacementPlugin } = compiler.webpack
    new NormalModuleReplacementPlugin(/^~icons\//, (resource) => {
      const icon = resource.request.slice(PREFIX.length).replace(/\.[a-z]+$/, '')
      resource.request = `${__filename}?icon=${icon}`
    }).apply(compiler)
    compiler.options.module.rules.push({
      resource: __filename,
      resourceQuery: /icon=/,
      type: 'javascript/auto',
      use: [{ loader: path.resolve(__filename) }],
    })
  }
}

module.exports = iconLoader
module.exports.IconifyRawPlugin = IconifyRawPlugin
