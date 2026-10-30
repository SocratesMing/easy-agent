#!/usr/bin/env node
/**
 * 业务模块边界与闭包检查（npm run check:easy-agent），任一项失败即非零退出。
 *
 *  1. 依赖边界：模块内的 import / export from / 动态 import() / require / new URL(…, import.meta.url)
 *     / CSS @import 与 url() / 模板里的 src、href，只能是 ①指向模块内部且存在的相对路径，
 *     ②已在 dependencies.json 声明、且在 package.json dependencies 里的 npm 包。
 *     禁止 @/、~/、~icons/ 等宿主或特殊插件 alias、绝对路径、node: 内置模块、向上逃逸出模块目录。
 *  2. 生成物一致：图标、限域第三方样式、依赖清单与生成器输出一致；共享代码与仓库级
 *     frontend-shared/ 一致（来源缺失时报错，不静默跳过）。
 *  3. 公开入口：index.js / sidebar.js 导出约定的名字；模块 README 与依赖清单存在。
 *  4. 全局副作用约束：不做 Vue 全局注册、不 $mount、不写宿主 html 主题 / history（navigation 适配层除外）、
 *     不读 system:share:* 等宿主私有存储键；样式选择器限定在 .easy-agent-root 内（:host 供 SDK）。
 *
 * 选项：--allow-missing-shared-source  仓库级 frontend-shared/ 不可用时只告警（仍会醒目打印）
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'fs'
import { dirname, join, normalize, relative, resolve } from 'path'
import { checkDependencies, DEPENDENCIES_FILE } from './deps.mjs'
import { checkIcons } from './generate-icons.mjs'
import { checkStyles } from './generate-styles.mjs'
import {
  FRONTEND_ROOT,
  MODULE_REL,
  MODULE_ROOT,
  REPO_ROOT,
  isRelative,
  listModuleFiles,
  packageNameOf,
  readJson,
  scanReferences,
  toPosix,
} from './lib.mjs'

// 当前收集目标：checkFile 等纯函数把发现写进传入的数组，main 汇总
let errors = []
let warnings = []
const fail = (msg) => errors.push(msg)
const warn = (msg) => warnings.push(msg)

/**
 * 检查单个模块文件（纯函数，测试直接调用）：依赖边界、全局副作用、样式限域。
 * @param {string} rel 相对模块根目录的路径
 * @param {string} code 文件内容
 * @param {{ deps: object, pkgDeps: object, exists?: (rel: string, spec: string) => boolean }} ctx
 * @returns {string[]} 问题列表（空数组 = 通过）
 */
export function checkFile(rel, code, ctx) {
  const saved = errors
  errors = []
  try {
    for (const ref of scanReferences(rel, code)) checkReference(rel, ref, ctx)
    checkForbidden(rel, code)
    checkStylesOf(rel, code)
    return errors
  } finally {
    errors = saved
  }
}

const PUBLIC_EXPORTS = {
  'index.js': ['EasyAgentPage', 'configureEasyAgent', 'EASY_AGENT_CONTRACT_VERSION', 'EASY_AGENT_API_PREFIXES', 'createVueRouterNavigation'],
  'sidebar.js': ['AiSidebar', 'chatStore', 'configureRequest', 'passwordlessLogin', 'getUserProfile', 'getModels'],
}

// 全局副作用：[正则, 说明, 允许出现的文件]
const FORBIDDEN = [
  [/\bVue\.(use|component|directive|mixin|filter)\s*\(/, 'Vue 全局注册（会覆盖宿主同名组件 / 指令），请改为局部注册', []],
  [/\bVue\.prototype\.\w+\s*=/, '改写 Vue.prototype', []],
  [/\.\$mount\s*\(/, '自行 $mount（嵌入页面由宿主根实例渲染）', []],
  [/document\.title\s*=/, '修改 document.title（仅 app.setDocumentTitle 开启时允许）', ['EasyAgentPage.vue']],
  [/history\.(pushState|replaceState)\s*\(/, '直接改写 history（请经 runtime/navigation.js）', ['runtime/navigation.js']],
  [/document\.documentElement\.(style|setAttribute|classList)/, '改写宿主 <html> 的样式 / 属性', []],
  [/system:share:/, '读取宿主私有存储键（应放在宿主适配器里）', []],
  [/window\.dispatchEvent\s*\(/, '派发 window 事件（宿主可能有同名监听）；模块内事件请用 runtime/events.js', ['utils/connectorSync.js']],
]

// 样式选择器允许的开头（模块根节点 / SDK 影子根 / keyframes 步骤）
const SELECTOR_OK = /^(:where\(\.easy-agent-root\)|\.easy-agent-root|:host)(?![\w-])/

function stripComments(code) {
  return code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`\\])\/\/.*$/gm, '$1')
}

function resolveRelative(fromRel, specifier) {
  const clean = specifier.split('?')[0].split('#')[0]
  const base = resolve(MODULE_ROOT, dirname(fromRel))
  const target = normalize(join(base, clean))
  const candidates = [target, `${target}.js`, `${target}.vue`, join(target, 'index.js')]
  const found = candidates.find((c) => existsSync(c) && statSync(c).isFile())
  return { target, found }
}

function checkReference(rel, ref, ctx) {
  const { deps, pkgDeps } = ctx
  const spec = ref.specifier
  if (spec.startsWith('node:')) return fail(`${rel}: 浏览器模块引用了 Node 内置模块 ${spec}`)
  if (spec.startsWith('@/') || spec.startsWith('~')) {
    return fail(`${rel}: ${ref.kind} 引用 ${spec} —— 依赖宿主 alias / 特殊加载器，请改为模块内相对路径或声明的 npm 包`)
  }
  if (spec.startsWith('/')) return fail(`${rel}: ${ref.kind} 使用绝对路径 ${spec}（部署基础路径变化后会失效）`)
  if (isRelative(spec)) {
    const { target, found } = resolveRelative(rel, spec)
    const inside = toPosix(relative(MODULE_ROOT, target))
    if (inside.startsWith('..')) return fail(`${rel}: ${ref.kind} 引用 ${spec} 逃逸出模块目录（${MODULE_REL}）`)
    const exists = ctx.exists ? ctx.exists(rel, spec) : !!found
    if (!exists) return fail(`${rel}: ${ref.kind} 引用 ${spec} 指向不存在的文件`)
    return undefined
  }
  const name = packageNameOf(spec)
  if (!deps[name]) return fail(`${rel}: 引用了未在 dependencies.json 声明的包 ${name}（${spec}），请执行 npm run deps:easy-agent 并审阅`)
  if (!pkgDeps[name]) return fail(`${rel}: 运行期依赖 ${name} 不在 frontend-vue2/package.json 的 dependencies 里`)
  return undefined
}

/** 按顶层逗号拆分选择器列表（:where(a, b) 括号内的逗号不拆） */
function splitSelectors(list) {
  const out = []
  let depth = 0
  let current = ''
  for (const ch of list) {
    if (ch === '(') depth += 1
    if (ch === ')') depth -= 1
    if (ch === ',' && depth === 0) {
      out.push(current.trim())
      current = ''
    } else {
      current += ch
    }
  }
  out.push(current.trim())
  return out.filter(Boolean)
}

/** 逐条检查样式规则选择器（跳过 @font-face / @keyframes 内部） */
function checkCssSelectors(rel, css, label) {
  const code = css.replace(/\/\*[\s\S]*?\*\//g, '')
  const stack = []
  let i = 0
  let buffer = ''
  while (i < code.length) {
    const ch = code[i]
    if (ch === '{') {
      const head = buffer.trim()
      buffer = ''
      const parent = stack[stack.length - 1]
      if (head.startsWith('@')) {
        stack.push(/^@(font-face|keyframes|-webkit-keyframes|page)/.test(head) ? 'opaque' : 'group')
      } else if (parent === 'opaque') {
        stack.push('opaque')
      } else {
        for (const selector of splitSelectors(head)) {
          if (!SELECTOR_OK.test(selector)) fail(`${rel}: ${label}选择器未限定在模块根节点内：${selector}`)
        }
        stack.push('rule')
      }
    } else if (ch === '}') {
      stack.pop()
      buffer = ''
    } else if (ch === ';' && stack[stack.length - 1] !== 'rule') {
      buffer = ''
    } else {
      buffer += ch
    }
    i += 1
  }
}

function checkStylesOf(rel, code) {
  if (rel.endsWith('.css')) return checkCssSelectors(rel, code, '')
  if (!rel.endsWith('.vue')) return undefined
  const re = /<style([^>]*)>([\s\S]*?)<\/style>/g
  let m
  while ((m = re.exec(code)) !== null) {
    const attrs = m[1]
    const body = m[2]
    // :global(...) 是 Vue 3 写法，Vue 2 的 scoped 编译不认（整条规则被浏览器丢弃），不算宿主污染
    const css = body.replace(/\/\*[\s\S]*?\*\//g, '').replace(/:global\([^)]*\)/g, '')
    if (/(^|[\s,(])(html|body|:root)(?=[\s.[:{,>)]|$)/m.test(css)) {
      fail(`${rel}: <style> 里出现 html / body / :root 选择器（嵌入宿主后会作用到整个页面）`)
    }
    if (!/\bscoped\b/.test(attrs)) checkCssSelectors(rel, body, '非 scoped <style> ')
  }
  return undefined
}

function checkForbidden(rel, code) {
  const source = stripComments(code)
  for (const [re, message, allow] of FORBIDDEN) {
    if (allow.includes(rel)) continue
    if (re.test(source)) fail(`${rel}: ${message}`)
  }
}

function checkPublicEntries() {
  for (const [file, names] of Object.entries(PUBLIC_EXPORTS)) {
    const full = join(MODULE_ROOT, file)
    if (!existsSync(full)) {
      fail(`缺少公开入口 ${MODULE_REL}/${file}`)
      continue
    }
    const code = stripComments(readFileSync(full, 'utf8'))
    for (const name of names) {
      const exported = new RegExp(`export\\s*\\{[^}]*\\b${name}\\b[^}]*\\}|export\\s+(const|function|let|class)\\s+${name}\\b`).test(code)
      if (!exported) fail(`公开入口 ${file} 未导出 ${name}`)
    }
  }
  for (const required of ['README.md', 'dependencies.json', 'runtime/config.js']) {
    if (!existsSync(join(MODULE_ROOT, required))) fail(`模块缺少 ${required}`)
  }
}

function checkSharedSources(allowMissing) {
  const sourceRoot = join(REPO_ROOT, 'frontend-shared')
  if (!existsSync(sourceRoot)) {
    const msg = '仓库级共享代码来源 frontend-shared/ 不存在，无法核验 shared/ 下生成副本的来源一致性'
    if (allowMissing) warn(`${msg}（--allow-missing-shared-source：本项未验证）`)
    else fail(`${msg}（在完整仓库中运行，或显式加 --allow-missing-shared-source）`)
    return
  }
  const banner = (rel) => ['// ⚠️ 自动生成，请勿直接编辑。', `// 源文件：frontend-shared/${rel}`, '// 同步：npm run sync:shared（frontend-vue2 / frontend 各自目录下执行）', ''].join('\n')
  const walk = (dir, prefix = '') => {
    const out = []
    for (const name of readdirSync(dir).sort()) {
      const full = join(dir, name)
      const rel = prefix ? `${prefix}/${name}` : name
      if (statSync(full).isDirectory()) out.push(...walk(full, rel))
      else if (name.endsWith('.js')) out.push(rel)
    }
    return out
  }
  for (const rel of walk(sourceRoot)) {
    const expected = banner(rel) + readFileSync(join(sourceRoot, rel), 'utf8')
    const copy = join(MODULE_ROOT, 'shared', rel)
    if (!existsSync(copy) || readFileSync(copy, 'utf8') !== expected) {
      fail(`共享代码 shared/${rel} 与 frontend-shared/${rel} 不一致（请执行 npm run sync:shared，不要直接改生成副本）`)
    }
  }
}

function main() {
  const allowMissing = process.argv.includes('--allow-missing-shared-source')
  if (!existsSync(DEPENDENCIES_FILE)) fail('缺少 dependencies.json（npm run deps:easy-agent）')
  const deps = existsSync(DEPENDENCIES_FILE) ? readJson(DEPENDENCIES_FILE).runtime || {} : {}
  const pkgDeps = readJson(join(FRONTEND_ROOT, 'package.json')).dependencies || {}

  const files = listModuleFiles()
  if (files.length === 0) fail(`模块目录 ${MODULE_REL} 为空或不存在`)
  for (const rel of files) {
    if (!/\.(js|vue|css)$/.test(rel)) continue
    errors.push(...checkFile(rel, readFileSync(join(MODULE_ROOT, rel), 'utf8'), { deps, pkgDeps }))
  }

  const [depsOk] = checkDependencies()
  if (!depsOk) fail('dependencies.json 与模块实际引用不一致（npm run deps:easy-agent 后审阅变化）')
  const staleIcons = checkIcons()
  if (staleIcons === null) fail('未安装 @iconify/json，无法核验图标资源')
  else for (const file of staleIcons) fail(`图标资源过期：${toPosix(relative(FRONTEND_ROOT, file))}（npm run icons:easy-agent）`)
  const staleStyles = checkStyles()
  if (staleStyles === null) fail('依赖未安装，无法核验限域第三方样式')
  else for (const file of staleStyles) fail(`限域样式过期：${toPosix(relative(FRONTEND_ROOT, file))}（npm run styles:easy-agent）`)
  checkSharedSources(allowMissing)
  checkPublicEntries()

  for (const message of warnings) console.warn(`⚠️  ${message}`)
  if (errors.length) {
    console.error(`❌ easy-agent 模块检查失败（${errors.length} 项）：`)
    for (const message of errors) console.error(`  - ${message}`)
    process.exit(1)
  }
  console.log(`✅ easy-agent 模块检查通过：${files.length} 个受管文件，${Object.keys(deps).length} 个运行期依赖${warnings.length ? `，${warnings.length} 项未验证（见上方告警）` : ''}`)
}

if (process.argv[1] && process.argv[1].endsWith('check.mjs')) main()
