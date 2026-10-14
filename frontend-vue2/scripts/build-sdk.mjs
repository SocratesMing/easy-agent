/**
 * 构建 AI 侧边栏 SDK（npm run build:sdk）
 *
 * 1. vue-cli lib 模式打包 src/sdk/index.js（内联 Vue 运行时）→ dist-sdk/
 *    产物：easy-agent-sidebar.common.js（打包器）/ .umd.js / .umd.min.js（<script> 直接引入）
 * 2. 把抽取出的 CSS 以字符串形式回填进 JS：SDK 运行在 Shadow DOM 中，样式必须注入影子根，
 *    单文件即可使用，无需另外引入 CSS；@font-face 单独回填（影子根内声明的字体不生效，由 SDK 注入宿主 document）
 * 3. 写入 npm 包元数据（sdk/ 下的 package.json、类型声明、演示页）与 README（集成指南）
 */
// 需兼容 Node 14：不用 import.meta.dirname（Node 20.11+）与 node: 前缀导入
import { spawnSync } from 'child_process'
import { createRequire } from 'module'
import { copyFileSync, existsSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'fs'
import { dirname, resolve } from 'path'
import { fileURLToPath } from 'url'

const require = createRequire(import.meta.url)
const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const outDir = resolve(root, 'dist-sdk')
const sdkDir = resolve(root, 'sdk')
const NAME = 'easy-agent-sidebar'

const pkg = JSON.parse(readFileSync(resolve(sdkDir, 'package.json'), 'utf8'))
const cliPath = require.resolve('@vue/cli-service/bin/vue-cli-service.js')

const result = spawnSync(
  process.execPath,
  [
    cliPath,
    'build',
    '--target', 'lib',
    '--inline-vue',
    '--name', 'EasyAgentSidebar',
    '--filename', NAME,
    '--dest', 'dist-sdk',
    'src/sdk/index.js',
  ],
  { cwd: root, stdio: 'inherit', env: { ...process.env, EASY_SDK_BUILD: '1', NODE_ENV: 'production' } }
)
if (result.status !== 0) process.exit(result.status ?? 1)

const cssFile = resolve(outDir, `${NAME}.css`)
if (!existsSync(cssFile)) {
  console.error(`[build-sdk] 未找到 ${cssFile}`)
  process.exit(1)
}
const css = readFileSync(cssFile, 'utf8')
const fontFaces = css.match(/@font-face\s*{[^}]*}/g) || []
const mainCss = fontFaces.reduce((acc, rule) => acc.replace(rule, ''), css)
const fontCss = fontFaces.join('\n')

// JSON.stringify 得到合法的 JS 字符串字面量；U+2028/2029 在旧引擎中不能出现在字符串里
const toLiteral = (text) =>
  JSON.stringify(text).replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029')
const replacements = {
  __EASY_AGENT_SDK_CSS__: toLiteral(mainCss),
  __EASY_AGENT_SDK_FONT_CSS__: toLiteral(fontCss),
  __EASY_AGENT_SDK_VERSION__: toLiteral(pkg.version),
}

for (const file of readdirSync(outDir).filter(f => f.endsWith('.js'))) {
  const path = resolve(outDir, file)
  let code = readFileSync(path, 'utf8')
  for (const [token, literal] of Object.entries(replacements)) {
    const pattern = new RegExp(`(["'])${token}\\1`, 'g')
    if (!pattern.test(code)) {
      console.error(`[build-sdk] ${file} 中未找到占位符 ${token}`)
      process.exit(1)
    }
    code = code.replace(pattern, () => literal)
  }
  writeFileSync(path, code)
}

rmSync(cssFile)
// vue-cli 自动生成的 demo.html 依赖外部 CSS 与全局 Vue，换成 sdk/demo.html
rmSync(resolve(outDir, 'demo.html'), { force: true })
for (const file of ['package.json', 'index.d.ts', 'demo.html']) {
  copyFileSync(resolve(sdkDir, file), resolve(outDir, file))
}
copyFileSync(resolve(root, 'docs', 'AI侧边栏SDK集成指南.md'), resolve(outDir, 'README.md'))

const size = (f) => `${(readFileSync(resolve(outDir, f)).length / 1024).toFixed(0)} KB`
console.log(`\n[build-sdk] ${pkg.name}@${pkg.version} → dist-sdk/`)
console.log(`  ${NAME}.umd.min.js  ${size(`${NAME}.umd.min.js`)}  （CSS 已内联）`)
console.log(`  ${NAME}.common.js   ${size(`${NAME}.common.js`)}`)
console.log(`  发布：cd dist-sdk && npm publish\n`)
