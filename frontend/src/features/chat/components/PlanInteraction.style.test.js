import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const cardSource = readFileSync(new URL('./PlanQuestionCard.vue', import.meta.url), 'utf8')
const stackSource = readFileSync(new URL('./PlanQuestionStack.vue', import.meta.url), 'utf8')
const chatSource = readFileSync(new URL('./Chat.vue', import.meta.url), 'utf8')
const cardStyle = cardSource.match(/<style scoped>([\s\S]*?)<\/style>/)?.[1] || ''
const stackStyle = stackSource.match(/<style scoped>([\s\S]*?)<\/style>/)?.[1] || ''
const chatStyle = [...chatSource.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)]
  .map(match => match[1])
  .join('\n')

function rule(style, selector) {
  const matches = [...style.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter(match => match[1].trim() === selector)
  assert.ok(matches.length, `缺少样式规则：${selector}`)
  return matches[0][2]
}

test('候选项去掉外层和序号方框，选中态只靠背景与勾选标识', () => {
  const body = rule(cardStyle, '.pq-option-body')
  const number = rule(cardStyle, '.pq-option-number')
  const selected = rule(cardStyle, '.pq-option.selected .pq-option-body')

  assert.match(body, /border:\s*0;/)
  assert.match(number, /border:\s*0;/)
  assert.match(selected, /background:/)
  assert.doesNotMatch(selected, /border:/)
})

test('候选项勾号和步骤勾号都通过网格布局居中', () => {
  const check = rule(cardStyle, '.pq-check')
  const checkMark = rule(cardStyle, '.pq-option.selected .pq-check::after')
  const stepLabel = rule(stackStyle, '.pqs-step > span')

  assert.match(check, /display:\s*grid;/)
  assert.match(check, /place-items:\s*center;/)
  assert.doesNotMatch(checkMark, /position:\s*absolute;/)
  assert.match(checkMark, /rotate\(45deg\)/)
  assert.match(stepLabel, /display:\s*grid;/)
  assert.match(stepLabel, /place-items:\s*center;/)
})

test('步骤导航和叠层卡片去掉多余边框，保持更轻的层次', () => {
  const step = rule(stackStyle, '.pqs-step')
  const layer = rule(stackStyle, '.pqs-layer')

  assert.match(step, /border:\s*0;/)
  assert.match(layer, /border:\s*0;/)
})

test('候选项焦点环向内收，不覆盖相邻候选项', () => {
  const focus = rule(cardStyle, '.pq-native:focus-visible + .pq-option-body')

  assert.match(focus, /outline:\s*2px solid/)
  assert.match(focus, /outline-offset:\s*-2px;/)
})

test('Plan 面板保留滚动能力但不显示最右侧滚动条', () => {
  const panel = rule(chatStyle, '.composer-plan-panel-inner')
  const webkit = rule(chatStyle, '.composer-plan-panel-inner::-webkit-scrollbar')

  assert.match(panel, /overflow-y:\s*auto;/)
  assert.match(panel, /scrollbar-width:\s*none;/)
  assert.match(panel, /-ms-overflow-style:\s*none;/)
  assert.doesNotMatch(panel, /scrollbar-gutter:/)
  assert.match(webkit, /display:\s*none;/)
})
