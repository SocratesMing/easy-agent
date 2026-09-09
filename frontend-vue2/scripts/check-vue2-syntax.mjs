#!/usr/bin/env node
/**
 * Vue 2 语法合规检查
 *
 * 用法：npm run lint:vue2
 *
 * 检查项：
 *   1. 用 Vue 2 官方编译器编译每个 SFC 的模板（可查出多根节点、指令误用等）
 *   2. 扫描 Vue 3 专属语法（<script setup>、defineXxx、v-model 多绑定、Teleport 等）
 *   3. 组件 data 选项必须是函数
 *   4. v-for 必须绑定 :key
 *   5. v-if 与 v-for 不得出现在同一元素（Vue2 中 v-for 优先，与 Vue3 相反）
 *   6. 事件名必须是 kebab-case（Vue2 不做大小写转换）
 */
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'

const here = path.dirname(fileURLToPath(import.meta.url))
const ROOT = path.resolve(here, '..')
const SRC = path.join(ROOT, 'src')

const require = createRequire(path.join(ROOT, 'noop.js'))
const { compileTemplate, parse } = require('vue/compiler-sfc')

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

// ── 1. 模板编译（Vue 2 官方编译器）──────────────────────────────
for (const f of sfcFiles) {
  const src = fs.readFileSync(f, 'utf8')
  const descriptor = parse(src, { filename: f }).descriptor
  if (!descriptor?.template?.content) continue
  const res = compileTemplate({
    source: descriptor.template.content,
    filename: f,
    id: 'lint',
  })
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
  [/import\.meta\.env/, 'Vite 的 import.meta.env（Vue CLI 用 process.env）'],
  [/\bcreateApp\s*\(/, 'Vue3 createApp（Vue2 用 new Vue）'],
  [/^\s*(beforeUnmount|unmounted)\s*[({:]/m, 'Vue3 Options 钩子（Vue2 为 beforeDestroy/destroyed）'],
  [/<style[^>]*>[\s\S]*?v-bind\s*\(/, 'Vue3 样式 v-bind()'],
  [/(?<!:):deep\s*\(/, 'Vue3 :deep()（Vue2 用 ::v-deep）'],
  [/\$slots\.[a-zA-Z]+\s*\(/, 'Vue3 插槽函数式调用'],
  [/\bupdate:modelValue\b|\bmodelValue\b/, 'Vue3 v-model 约定 modelValue（Vue2 为 value/input）'],
]

for (const f of files) {
  const lines = fs.readFileSync(f, 'utf8').split('\n')
  for (const [re, desc] of VUE3_PATTERNS) {
    const rx = new RegExp(re.source, re.flags.includes('m') ? re.flags : re.flags + 'm')
    lines.forEach((line, i) => {
      if (rx.test(line)) {
        add('Vue3 语法混入', rel(f), `${desc} → 第 ${i + 1} 行: ${line.trim().slice(0, 90)}`)
      }
    })
  }
}

// ── 3. data 选项必须是函数 ──────────────────────────────────────
for (const f of sfcFiles) {
  const src = fs.readFileSync(f, 'utf8')
  const block = src.match(/<script[^>]*>([\s\S]*?)<\/script>/)
  if (!block) continue
  // 仅当 data 直接写成对象字面量才算违规（数组/标识符多为普通对象的键，如配置映射表）
  const dm = block[1].match(/^\s{2}data\s*:\s*\{/m)
  if (dm) add('响应式声明', rel(f), `data 选项必须是返回对象的函数: ${dm[0].trim()}`)
}

// ── 4/5. v-for 的 key、v-if 与 v-for 同元素 ─────────────────────
for (const f of sfcFiles) {
  const src = fs.readFileSync(f, 'utf8')
  const descriptor = parse(src, { filename: f }).descriptor
  const tpl = descriptor?.template?.content
  if (!tpl) continue
  const tagRe = /<[a-zA-Z][^>]*>/gs
  let m
  while ((m = tagRe.exec(tpl)) !== null) {
    const tag = m[0]
    if (!/\sv-for\s*=/.test(tag)) continue
    const line = tpl.slice(0, m.index).split('\n').length
    if (!/\s:key\s*=|\sv-bind:key\s*=/.test(tag)) {
      add('缺少 key', rel(f), `第 ${line} 行 v-for 未绑定 :key → ${tag.replace(/\s+/g, ' ').slice(0, 100)}`)
    }
    if (/\sv-if\s*=/.test(tag)) {
      add('v-if/v-for 同元素', rel(f), `第 ${line} 行 → ${tag.replace(/\s+/g, ' ').slice(0, 100)}`)
    }
  }
}

// ── 6. 事件名必须 kebab-case ────────────────────────────────────
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
  console.log(`✅ Vue 2 语法检查通过（已扫描 ${files.length} 个文件）`)
  process.exit(0)
}
const byKind = {}
for (const p of problems) (byKind[p.kind] ||= []).push(p)
for (const [kind, list] of Object.entries(byKind)) {
  console.log(`\n### ${kind}（${list.length}）`)
  for (const p of list) console.log(`  - ${p.file}: ${p.msg}`)
}
console.log(`\n❌ 合计 ${problems.length} 项不符合 Vue 2 规范`)
process.exit(1)
