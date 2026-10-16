import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildSegments,
  buildTokenMap,
  insertSkillToken,
  reconcileTokenDeletion,
  removeTokenSegments,
  skillDirsInText,
  stripSkillTokens,
  tokenAfterCaret,
  tokenBeforeCaret,
  tokenForSkill,
} from './skillPillTokens.js'

const SKILLS = [
  { dir_name: 'docx', name: 'Word 文档处理' },
  { dir_name: 'pptx-generator', name: 'PPT 生成' },
]

const map = () => buildTokenMap(SKILLS)
const text = (segs) => segs.map((s) => (s.type === 'skill' ? s.token : s.text)).join('')

test('标签文本优先显示名', () => {
  assert.equal(tokenForSkill({ dir_name: 'docx', name: 'Word 文档处理' }), '/Word 文档处理')
  assert.equal(tokenForSkill({ dir_name: 'word-docx' }), '/word-docx')
})

test('文本可原样还原（不丢字、不粘字）', () => {
  const cases = [
    '',
    '帮我用 /Word 文档处理 排个版',
    '/Word 文档处理/PPT 生成 一起做',
    '/docx 这是未知目录名，应当保持普通文字',
    '路径 /usr/bin/env 不是技能',
    '/Word 文档处理第二行\n换行也正常',
  ]
  for (const value of cases) {
    assert.equal(text(buildSegments(value, map())), value)
  }
})

test('识别开头、空白后、以及紧邻上一个标签的 token', () => {
  const segs = buildSegments('/PPT 生成/Word 文档处理 你好', map())
  const skills = segs.filter((s) => s.type === 'skill')
  assert.deepEqual(skills.map((s) => s.dirName), ['pptx-generator', 'docx'])
  assert.deepEqual(skills.map((s) => s.start), [0, '/PPT 生成'.length])
  assert.equal(text(segs), '/PPT 生成/Word 文档处理 你好')
})

test('同时命中的 token 取最长，位置判断不误伤相邻文字', () => {
  const tokens = buildTokenMap([{ dir_name: 'a', name: 'doc' }, { dir_name: 'b', name: 'docx' }])
  const segs = buildSegments('/docx', tokens)
  assert.equal(segs.length, 1)
  assert.equal(segs[0].dirName, 'b')
  const other = buildSegments('/docs', tokens)
  assert.deepEqual(other.map((s) => s.type), ['skill', 'text'])
  assert.equal(text(other), '/docs')
})

test('同显示名的技能双方都回退目录名', () => {
  const tokens = buildTokenMap([
    { dir_name: 'one', name: '同名技能' },
    { dir_name: 'two', name: '同名技能' },
  ])
  assert.equal(tokens.has('/同名技能'), false)
  assert.equal(tokens.get('/one').dir_name, 'one')
  assert.equal(tokens.get('/two').dir_name, 'two')
})

test('suppressStart 让正在检索的半截查询词保持普通文字', () => {
  const segs = buildSegments('/doc', map(), { suppressStart: 0 })
  assert.deepEqual(segs, [{ type: 'text', text: '/doc' }])
})

test('光标紧邻标签之后 / 之前才命中', () => {
  const value = '看下 /Word 文档处理 的内容'
  const tokens = map()
  const start = value.indexOf('/Word 文档处理')
  const end = start + '/Word 文档处理'.length
  assert.equal(tokenBeforeCaret(value, end, tokens).dirName, 'docx')
  assert.equal(tokenBeforeCaret(value, end - 1, tokens), null)
  assert.equal(tokenBeforeCaret(value, end + 1, tokens), null)
  assert.equal(tokenAfterCaret(value, start, tokens).dirName, 'docx')
  assert.equal(tokenAfterCaret(value, start + 1, tokens), null)
})

test('插入标签后光标停在标签之后', () => {
  const inserted = insertSkillToken('帮我做', 3, '/Word 文档处理')
  assert.equal(inserted.text, '帮我做/Word 文档处理')
  assert.equal(inserted.caret, inserted.text.length)
  const mid = insertSkillToken('ab', 1, '/docx')
  assert.equal(mid.text, 'a/docxb')
  assert.equal(mid.caret, 6)
})

test('删除标签（含同一技能出现多次）后光标落在最前那个标签的起点', () => {
  const value = '/Word 文档处理 中间 /Word 文档处理'
  const segs = buildSegments(value, map()).filter((s) => s.type === 'skill')
  const removed = removeTokenSegments(value, segs)
  assert.equal(removed.text, ' 中间 ')
  assert.equal(removed.caret, 0)
})

test('发送前剥离标签，技能目录名单独取出', () => {
  const value = '/Word 文档处理/PPT 生成 帮我生成'
  assert.equal(skillDirsInText(value, map()).join(','), 'docx,pptx-generator')
  assert.equal(stripSkillTokens(value, map()), ' 帮我生成')
})

test('移动端：被删掉半个标签时整段删掉标签', () => {
  const value = '前面 /Word 文档处理 后面'
  const tokens = map()
  const end = value.indexOf('/Word 文档处理') + '/Word 文档处理'.length
  // 浏览器只删掉了标签的最后一个字（光标停在 end-1）
  const afterNative = value.slice(0, end - 1) + value.slice(end)
  const healed = reconcileTokenDeletion(value, afterNative, end - 1, tokens)
  assert.equal(healed.text, '前面  后面')
  assert.equal(healed.caret, value.indexOf('/Word 文档处理'))
})

test('移动端兜底不误伤普通删除 / 粘贴 / 替换', () => {
  const value = '前面 /Word 文档处理 后面'
  const tokens = map()
  // 普通文字的单字删除
  assert.equal(reconcileTokenDeletion(value, value.slice(0, 1) + value.slice(2), 1, tokens), null)
  // 删除点之前的文本被改动（粘贴 / 替换 / 输入法上屏）→ 交回常规处理
  assert.equal(reconcileTokenDeletion(value, 'X' + value.slice(1), 1, tokens), null)
  // 文本变长（输入）→ 不处理
  assert.equal(reconcileTokenDeletion(value, value + 'x', value.length, tokens), null)
})

test('移动端兜底不管「光标落在标签中间」的常规删除', () => {
  const value = '前面 /Word 文档处理 后面'
  const tokens = map()
  const start = value.indexOf('/Word 文档处理')
  const caret = start + 5          // 标签内部
  const afterNative = value.slice(0, caret - 1) + value.slice(caret)
  assert.equal(reconcileTokenDeletion(value, afterNative, caret - 1, tokens), null)
})
