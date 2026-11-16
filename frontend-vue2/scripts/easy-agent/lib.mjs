/**
 * easy-agent 模块工具的公共函数（check / export / 图标与依赖清单生成共用）。
 *
 * 需兼容 Node 14（内网构建机）：不用 import.meta.dirname、fs.cp、Array#at 等新 API。
 */
import { createHash } from 'crypto'
import { existsSync, readdirSync, readFileSync, statSync } from 'fs'
import { dirname, join, relative, resolve, sep } from 'path'
import { fileURLToPath } from 'url'

export const FRONTEND_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..')
export const REPO_ROOT = resolve(FRONTEND_ROOT, '..')
/** 受管业务模块：外网权威源码，整体同步到宿主的同一路径 */
export const MODULE_REL = 'src/packages/easyAgent'
export const MODULE_ROOT = join(FRONTEND_ROOT, MODULE_REL)
/** 首次接入模板、工具与文档（不属于运行时，不同步覆盖宿主文件） */
export const INTEGRATION_ROOT = join(FRONTEND_ROOT, 'integration')

/** 外网测试文件不进迁移包（宿主的 jest / eslint 可能会扫到 *.test.js） */
export function isManagedFile(rel) {
  // .easy-agent-sync.json 是宿主侧的同步状态文件，*.easy-agent-tmp 是同步工具的临时文件
  return !/\.test\.js$/.test(rel) && !/(^|\/)(\.DS_Store|\.easy-agent-sync\.json)$/.test(rel) && !/\.easy-agent-tmp$/.test(rel)
}

export function toPosix(p) {
  return p.split(sep).join('/')
}

/** 递归列出目录下的文件（返回相对 dir 的 posix 路径，已排序） */
export function listFiles(dir) {
  const out = []
  const walk = (current) => {
    for (const entry of readdirSync(current, { withFileTypes: true })) {
      const full = join(current, entry.name)
      if (entry.isDirectory()) walk(full)
      else if (entry.isFile()) out.push(toPosix(relative(dir, full)))
    }
  }
  if (existsSync(dir)) walk(dir)
  return out.sort()
}

/** 受管模块的文件清单（不含测试） */
export function listModuleFiles() {
  return listFiles(MODULE_ROOT).filter(isManagedFile)
}

export function sha256(buffer) {
  return createHash('sha256').update(buffer).digest('hex')
}

export function fileDigest(file) {
  const buffer = readFileSync(file)
  return { sha256: sha256(buffer), size: statSync(file).size }
}

/** 裸模块说明符 → npm 包名（element-ui/lib/button → element-ui，@scope/a/b → @scope/a） */
export function packageNameOf(specifier) {
  const parts = specifier.split('/')
  return specifier.startsWith('@') ? parts.slice(0, 2).join('/') : parts[0]
}

const JS_PATTERNS = [
  // import x from 'a' / import 'a' / export * from 'a' / export { x } from 'a'
  { kind: 'import', re: /(?:^|[\s;])(?:import|export)\s+(?:[^'"`;]*?\sfrom\s+)?['"]([^'"]+)['"]/g },
  // import('a')
  { kind: 'dynamic-import', re: /\bimport\(\s*['"]([^'"]+)['"]\s*\)/g },
  // require('a')
  { kind: 'require', re: /\brequire\(\s*['"]([^'"]+)['"]\s*\)/g },
  // new URL('a', import.meta.url)：webpack 5 的 worker / 资源写法
  { kind: 'url', re: /new\s+URL\(\s*['"]([^'"]+)['"]\s*,\s*import\.meta\.url\s*\)/g },
]

const CSS_PATTERNS = [
  { kind: 'css-import', re: /@import\s+(?:url\()?\s*['"]?([^'")\s;]+)['"]?\s*\)?/g },
  { kind: 'css-url', re: /url\(\s*['"]?([^'")]+)['"]?\s*\)/g },
]

function stripJsComments(code) {
  // 只去掉整行 // 注释与 /* */ 块注释，避免把注释里的示例当成依赖
  return code.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:'"`])\/\/.*$/gm, '$1')
}

function stripCssComments(code) {
  return code.replace(/\/\*[\s\S]*?\*\//g, '')
}

function collect(code, patterns, out) {
  for (const { kind, re } of patterns) {
    re.lastIndex = 0
    let m
    while ((m = re.exec(code)) !== null) out.push({ kind, specifier: m[1] })
  }
}

/**
 * 扫描一个源文件里的依赖引用（JS / Vue SFC / CSS）。
 * Vue SFC：<script> 按 JS 扫，<style> 按 CSS 扫，<template> 扫 src / href 里的相对资源。
 */
export function scanReferences(file, code) {
  const refs = []
  if (file.endsWith('.css')) {
    collect(stripCssComments(code), CSS_PATTERNS, refs)
  } else if (file.endsWith('.vue')) {
    const script = /<script[^>]*>([\s\S]*?)<\/script>/g
    let m
    while ((m = script.exec(code)) !== null) collect(stripJsComments(m[1]), JS_PATTERNS, refs)
    const style = /<style[^>]*>([\s\S]*?)<\/style>/g
    while ((m = style.exec(code)) !== null) collect(stripCssComments(m[1]), CSS_PATTERNS, refs)
    const template = /<template>([\s\S]*)<\/template>/.exec(code)
    if (template) {
      const attr = /\s(?:src|href)="(\.{1,2}\/[^"]+|@\/[^"]+|~[^"]+)"/g
      while ((m = attr.exec(template[1])) !== null) refs.push({ kind: 'template-asset', specifier: m[1] })
    }
  } else if (file.endsWith('.js') || file.endsWith('.mjs') || file.endsWith('.cjs')) {
    collect(stripJsComments(code), JS_PATTERNS, refs)
  }
  // data: / http: / 片段锚点不是依赖
  return refs.filter((r) => !/^(data:|https?:|#|\/\/)/.test(r.specifier))
}

export function isRelative(specifier) {
  return specifier.startsWith('./') || specifier.startsWith('../')
}

export function readJson(file) {
  return JSON.parse(readFileSync(file, 'utf8'))
}

/** 稳定的 JSON 序列化（2 空格缩进 + 结尾换行），生成文件与 --check 比对共用 */
export function formatJson(value) {
  return `${JSON.stringify(value, null, 2)}\n`
}
