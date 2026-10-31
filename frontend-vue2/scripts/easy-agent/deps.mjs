#!/usr/bin/env node
/**
 * 生成业务模块的依赖清单：src/packages/easyAgent/dependencies.json
 *
 * 依据「模块源码里实际出现的裸模块引用」+ frontend-vue2/package.json 的版本范围 +
 * package-lock.json 的锁定版本与 engines，列出模块的运行期依赖；构建要求与外网专用工具
 * 写在固定段落里。清单随模块同步到宿主，同步工具据此提示依赖变化（不自动装包）。
 *
 * 用法：
 *   node scripts/easy-agent/deps.mjs           # 写入
 *   node scripts/easy-agent/deps.mjs --check   # 只校验，不一致退出码 1
 */
import { existsSync, readFileSync, writeFileSync } from 'fs'
import { join } from 'path'
import {
  FRONTEND_ROOT,
  MODULE_ROOT,
  formatJson,
  isRelative,
  listModuleFiles,
  packageNameOf,
  readJson,
  scanReferences,
} from './lib.mjs'

export const DEPENDENCIES_FILE = join(MODULE_ROOT, 'dependencies.json')

// 编译期要求：宿主构建链需要具备的能力（不是 npm 包清单，宿主按此核对自己的配置）
const BUILD_REQUIREMENTS = {
  vue: 'Vue 2.7.x：组件用到 2.7 内置 Composition API（从 vue 导入 ref / computed / onMounted 等），不支持 Vue 2.6',
  sfc: 'vue-loader ≥ 15.10 且使用 vue@2.7 自带的 vue/compiler-sfc（Vue CLI 5 默认）；宿主不再需要 vue-template-compiler',
  webpack: 'webpack 5：Monaco 编辑器用 new Worker(new URL(..., import.meta.url)) 输出 worker chunk；动态 import() 输出懒加载 chunk',
  css: '能处理 .vue <style scoped>、.css 导入与 url() 引用的字体（Vue CLI 默认 css-loader / postcss 即可）',
  babel: '模块源码需经宿主 babel-loader 转译（src 内文件默认会转译）；依赖包中的现代语法按宿主 browserslist 决定是否 transpileDependencies',
  assets: '无需额外 loader / 插件：图标是模块内生成的 JS 字符串（不再依赖 ~icons 与 @iconify/json）',
}

// 只在外网使用的工具：不属于模块运行期依赖，宿主不需要安装
const EXTERNAL_TOOLING = {
  '@iconify/json': '生成 assets/icons/*.js（npm run icons:easy-agent）',
  'unplugin-icons': '历史遗留的开发依赖，模块与构建均不再使用',
  '@vue/cli-service': '外网独立应用与 SDK 的构建工具（宿主用自己的构建链）',
  '@vue/cli-plugin-babel': '外网构建工具',
  autoprefixer: '外网构建工具',
  postcss: '外网构建工具',
}

function lockInfo(lock, name) {
  const meta = (lock.packages || {})[`node_modules/${name}`]
  if (!meta) return { locked: null, engines: null, license: null }
  return {
    locked: meta.version || null,
    engines: meta.engines && meta.engines.node ? meta.engines.node : null,
    license: meta.license || null,
  }
}

export function buildDependencies() {
  const pkg = readJson(join(FRONTEND_ROOT, 'package.json'))
  const lock = readJson(join(FRONTEND_ROOT, 'package-lock.json'))
  const ranges = { ...(pkg.devDependencies || {}), ...(pkg.dependencies || {}) }
  const used = {}
  for (const rel of listModuleFiles()) {
    const code = readFileSync(join(MODULE_ROOT, rel), 'utf8')
    for (const ref of scanReferences(rel, code)) {
      const spec = ref.specifier
      if (isRelative(spec) || spec.startsWith('/') || spec.startsWith('@/') || spec.startsWith('~')) continue
      const name = packageNameOf(spec)
      if (!used[name]) used[name] = { specifiers: new Set(), usedBy: new Set() }
      used[name].specifiers.add(spec)
      used[name].usedBy.add(rel)
    }
  }
  const runtime = {}
  for (const name of Object.keys(used).sort()) {
    const info = lockInfo(lock, name)
    runtime[name] = {
      range: ranges[name] || null,
      locked: info.locked,
      license: info.license,
      nodeEngines: info.engines,
      declaredIn: pkg.dependencies && pkg.dependencies[name] ? 'dependencies' : (ranges[name] ? 'devDependencies' : null),
      specifiers: Array.from(used[name].specifiers).sort(),
      usedBy: Array.from(used[name].usedBy).sort(),
    }
  }
  return {
    schemaVersion: 1,
    note: '自动生成（npm run deps:easy-agent），请勿手改；宿主现状对照见 integration/README.md「依赖」',
    runtime,
    build: BUILD_REQUIREMENTS,
    externalTooling: EXTERNAL_TOOLING,
  }
}

/** 返回 [是否一致, 期望内容] */
export function checkDependencies() {
  const expected = formatJson(buildDependencies())
  const current = existsSync(DEPENDENCIES_FILE) ? readFileSync(DEPENDENCIES_FILE, 'utf8') : null
  return [current === expected, expected]
}

function main() {
  if (process.argv.includes('--check')) {
    const [ok] = checkDependencies()
    if (!ok) {
      console.error('[deps] dependencies.json 与模块实际引用不一致，请执行 npm run deps:easy-agent 并审阅变化')
      process.exit(1)
    }
    console.log('[deps] dependencies.json 与模块实际引用一致')
    return
  }
  const [, expected] = checkDependencies()
  writeFileSync(DEPENDENCIES_FILE, expected)
  console.log(`[deps] 已生成 ${DEPENDENCIES_FILE}`)
}

if (process.argv[1] && process.argv[1].endsWith('deps.mjs')) main()
