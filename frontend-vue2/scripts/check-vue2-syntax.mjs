#!/usr/bin/env node
/**
 * Vue 2.6 语法合规检查
 *
 * 用法：npm run lint:vue2 （Node >= 14）
 *
 * 检查项：
 *   1. 用 Vue 2.6 官方编译器(vue-template-compiler)编译每个 SFC 的模板
 *   2. 扫描 Vue 3 专属语法（<script setup>、defineXxx、Teleport、v-model 多绑定等）
 *   3. 扫描 Vue2.7/Vue3 组合式写法（setup()、import { ref/computed } from 'vue'）
 *   4. 组件 data 选项必须是函数
 *   5. v-for 必须绑定 :key
 *   6. v-if 与 v-for 不得出现在同一元素
 *   7. 事件名必须是 kebab-case
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const here = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(here, '..')
const SRC = path.join(ROOT, 'src')

// Node14 不解析 import.meta.dirname；createRequire 基于虚拟路径以保证可定位依赖
const require = createRequire(path.join(ROOT, 'noop.js'))
const { parseComponent, compile } = require('vue-template-compiler')

const problems = []
const add = (kind, file, msg) => problems.push({ kind, file, msg })

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) walk(p, out)
    else out.push(p)
  }
  return out
}

const files = walk(SRC).filter((f) => f.endsWith('.vue') || f.endsWith('.js'))
const sfcFiles = files.filter((f) => f.endsWith('.vue'))
const rel = (f) => path.relative(SRC, f)

// ── 1. 模板编译（Vue 2.6 官方编译器）────────────────────────────
for (const f of sfcFiles) {
  const src = fs.readFileSync(f, 'utf8')
  const sfc = parseComponent(src)
  const tpl = sfc && sfc.template ? sfc.template.content : ''
  if (!tpl) continue
  const res = compile(tpl)
  for (const e of res.errors || []) {
    add('模板编译错误', rel(f), typeof e === 'string' ? e : e.message || String(e))
  }
}

// ── 2. Vue 3 专属语法 ───────────────────────────────────────────
const VUE3_PATTERNS = [
  [/<script[^>]*\ssetup[\s>]/, 'Vue3 <script setup>'],
  [/\bdefine(Props|Emits|Expose|Model|Options|Slots)\s*\(/, 'Vue3 编译器宏'],
  [/\bwithDefaults\s*\(/, 'Vue3 编译器宏 withDefaults'],
  [/v-model\s*:\s*(?!lazy|number|trim)[a-zA-Z]/, 'Vue3 v-model 多绑定（Vue2 用 .sync）'],
  [/<[Tt]eleport[\s>]/, 'Vue3 <Teleport>'],
  [/<[Ss]uspense[\s>]/, 'Vue3 <Suspense>'],
  [/<Transition[\s>]/ , 'Vue3 <Transition>（Vue2 用 <transition>）'],
  [/import\.meta\.env/, 'Vite 的 import.meta.env（Vue CLI 用 process.env）'],
  [/\bcreateApp\s*\(/, 'Vue3 createApp（Vue2 用 new Vue）'],
  [/^\s*(beforeUnmount|unmounted)\s*[({:]/m, 'Vue3 Options 钩子（Vue2 为 beforeDestroy/destroyed）'],
  [/<style[^>]*>[\s\S]*?v-bind\s*\(/, 'Vue3 样式 v-bind()'],
  [/(?<!:):deep\s*\(/, 'Vue3 :deep()（Vue2 用 ::v-deep）'],
  [/\$slots\.[a-zA-Z]+\s*\(/, 'Vue3 插槽函数式调用'],
  [/\bupdate:modelValue\b|\bmodelValue\b/, 'Vue3 v-model 约定 modelValue（Vue2 为 value/input）'],
]

// ── 3. Vue2.7/Vue3 组合式 API（Vue 2.6 不支持）──────────────────
const COMPOSITION_PATTERNS = [
  [/\bsetup\s*\([^)]*\)\s*\{/, 'Vue2.6 不支持 setup() 组合式写法（应改为 Options API）'],
  [/\bimport\s*\{[^}]*\}\s*from\s*['"]vue['"]/, 'Vue2.6 不支持从 vue 解构导入组合式 API'],
]

for (const f of files) {
  const lines = fs.readFileSync(f, 'utf8').split('\n')
  const ruleSets = [
    ['Vue3 语法混入', VUE3_PATTERNS],
    ['Vue3/Vue2.7 组合式 API', COMPOSITION_PATTERNS],
  ]
  for (const [kindDesc, rules] of ruleSets) {
    for (const [re, desc] of rules) {
      const rx = new RegExp(re.source, re.flags.includes('m') ? re.flags : re.flags + 'm')
      lines.forEach((line, i) => {
        if (rx.test(line)) {
          add(kindDesc, rel(f), `${desc} → 第 ${i + 1} 行: ${line.trim().slice(0, 90)}`)
        }
      })
    }
  }
}

// ── 4. data 选项必须是函数 ──────────────────────────────────────
for (const f of sfcFiles) {
  const src = fs.readFileSync(f, 'utf8')
  const block = src.match(/<script[^>]*>([\s\S]*?)<\/script>/)
  if (!block) continue
  // 仅当 data 直接写成对象字面量才算违规（数组/标识符多为普通对象的键，如配置映射表）
  const dm = block[1].match(/^\s{2}data\s*:\s*\{/m)
  if (dm) add('响应式声明', rel(f), `data 选项必须是返回对象的函数: ${dm[0].trim()}`)
}

// ── 5/6. v-for 的 key、v-if 与 v-for 同元素 ────────────────────
for (const f of sfcFiles) {
  const src = fs.readFileSync(f, 'utf8')
  const sfc = parseComponent(src)
  const tpl = sfc && sfc.template ? sfc.template.content : ''
  if (!tpl) continue
  const tagRe = /<[a-zA-Z][^>]*>/gs
  let m
  while ((m = tagRe.exec(tpl)) !== null) {
    const tag = m[0]
    if (!/\sv-for\s*=/.test(tag)) continue
    // <template> 是虚拟容器，Vue 2.6 不允许在其上绑 key，key 应位于内部真实元素上；
    // 此处仅校验真实元素的 v-for（内部已带 :key 的写法视为合规）。
    if (/^<template[\s>]/.test(tag)) continue
    const line = tpl.slice(0, m.index).split('\n').length
    if (!/\s:key\s*=|\sv-bind:key\s*=/.test(tag)) {
      add('缺少 key', rel(f), `第 ${line} 行 v-for 未绑定 :key → ${tag.replace(/\s+/g, ' ').slice(0, 100)}`)
    }
    if (/\sv-if\s*=/.test(tag)) {
      add('v-if/v-for 同元素', rel(f), `第 ${line} 行 → ${tag.replace(/\s+/g, ' ').slice(0, 100)}`)
    }
  }
}

// ── 7. 事件名必须 kebab-case ────────────────────────────────────
for (const f of sfcFiles) {
  const src = fs.readFileSync(f, 'utf8')
  const camel = new Set()
  for (const mm of src.matchAll(/emit\(\s*['"]([a-zA-Z0-9:_-]+)['"]/g)) {
    // update:xxx 是 Vue2 .sync 的约定写法，其后的 prop 名保持 camelCase 属正常
    if (mm[1].startsWith('update:')) continue
    if (/[A-Z]/.test(mm[1])) camel.add(mm[1])
  }
  for (const mm of src.matchAll(/@([a-zA-Z0-9_-]+)\s*=/g)) {
    if (/[A-Z]/.test(mm[1])) camel.add(mm[1])
  }
  if (camel.size) {
    add('事件名 camelCase', rel(f), `应改为 kebab-case: ${[...camel].join(', ')}`)
  }
}

// ── 输出 ────────────────────────────────────────────────────────
if (!problems.length) {
  console.log(`✅ Vue 2.6 语法检查通过（已扫描 ${files.length} 个文件）`)
  process.exit(0)
}
const byKind = {}
for (const p of problems) {
  if (!byKind[p.kind]) byKind[p.kind] = []
  byKind[p.kind].push(p)
}
for (const [kind, list] of Object.entries(byKind)) {
  console.log(`\n### ${kind}（${list.length}）`)
  for (const p of list) console.log(`  - ${p.file}: ${p.msg}`)
}
console.log(`\n❌ 合计 ${problems.length} 项不符合 Vue 2.6 规范`)
process.exit(1)
