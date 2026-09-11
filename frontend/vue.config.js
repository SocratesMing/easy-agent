const { defineConfig } = require('@vue/cli-service')
const Icons = require('unplugin-icons/webpack')

// unplugin-icons 23 已移除内置 Vue 2 compiler，但 raw compiler 只会导出 SVG
// 字符串，Vue 2 会把它误当成组件标签。这里生成轻量 functional
// component，使所有 ~icons 保持现有用法，无需在业务组件里硬编码 SVG。
const vue2IconCompiler = {
  compiler(svg, collection, icon) {
    const match = svg.match(/^<svg\s*([^>]*)>([\s\S]*)<\/svg>$/i)
    if (!match) throw new Error(`Invalid SVG icon: ${collection}/${icon}`)
    const attrs = {}
    match[1].replace(/([:\w-]+)="([^"]*)"/g, (_, name, value) => {
      attrs[name] = value
      return ''
    })
    return `export default {
      name: ${JSON.stringify(`${collection}-${icon}`)},
      functional: true,
      render(h, ctx) {
        const data = Object.assign({}, ctx.data || {})
        data.attrs = Object.assign(${JSON.stringify(attrs)}, data.attrs || {})
        data.domProps = Object.assign({}, data.domProps || {}, { innerHTML: ${JSON.stringify(match[2])} })
        return h('svg', data)
      }
    }`
  },
}

module.exports = defineConfig({
  transpileDependencies: ['element-ui'],
  productionSourceMap: false,
  configureWebpack: {
    plugins: [Icons({ compiler: vue2IconCompiler, autoInstall: true })],
    devServer: {
      host: process.platform === 'win32' ? '127.0.0.1' : '0.0.0.0',
      port: Number(process.env.PORT || 5173),
    },
  },
})
