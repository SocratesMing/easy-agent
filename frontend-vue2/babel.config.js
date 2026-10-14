// SDK 构建（EASY_SDK_BUILD=1）把语法降到 ES2018：兼容 Node 14 时代的 webpack 4 宿主（acorn 6 不识别 ?. / ??）
const SDK_TARGETS = { chrome: '64', edge: '79', firefox: '67', safari: '12' }

module.exports = {
  presets: [
    process.env.EASY_SDK_BUILD === '1'
      ? ['@vue/cli-plugin-babel/preset', { targets: SDK_TARGETS }]
      : '@vue/cli-plugin-babel/preset',
  ],
}
