import assert from 'node:assert/strict'
import test from 'node:test'

import { buildTokenMap } from './skillPillTokens.js'
import { buildMessageText, parseSlashQuery } from './skillSlash.js'

const SKILLS = [
  { dir_name: 'docx', name: 'Word 文档处理' },
  { dir_name: 'pptx-generator', name: 'PPT 生成' },
]

const map = () => buildTokenMap(SKILLS)
const end = (value) => parseSlashQuery(value, value.length)

test('行首或空白后的 / 命中（原有行为不变）', () => {
  assert.deepEqual(end('/'), { query: '', start: 0 })
  assert.deepEqual(end('/doc'), { query: 'doc', start: 0 })
  assert.deepEqual(end('帮我 /wo'), { query: 'wo', start: 3 })
  assert.deepEqual(end('帮我\n/'), { query: '', start: 3 })
})

test('词中间与 URL 里的 / 不命中（原有行为不变）', () => {
  assert.equal(end('http://'), null)
  assert.equal(end('路径 /usr/bin 后'), null)
  assert.equal(end('10/20/'), null)
  assert.equal(end('abc'), null)
})

test('buildMessageText：归一化正文（原有行为不变）', () => {
  assert.equal(buildMessageText('  帮我   排版  '), '帮我 排版')
  assert.equal(buildMessageText(null), '')
})

test('紧邻已选标签后的 / 命中：连续选技能无需空格', () => {
  const value = '/Word 文档处理/'
  assert.deepEqual(parseSlashQuery(value, value.length, map()), { query: '', start: value.length - 1 })

  const typed = '/PPT 生成/wo'
  assert.deepEqual(parseSlashQuery(typed, typed.length, map()), { query: 'wo', start: '/PPT 生成'.length })
})

test('连续标签中途的 / 不命中（// 属于误打）', () => {
  const value = '/Word 文档处理//'
  assert.equal(parseSlashQuery(value, value.length, map()), null)
})

test('普通文字后的 / 不命中：日期、路径、URL 不误弹', () => {
  assert.equal(parseSlashQuery('10/20/', 6, map()), null)
  assert.equal(parseSlashQuery('http://a/', 9, map()), null)
  assert.equal(parseSlashQuery('看 docx/', 7, map()), null)
})

test('未传 tokenMap 时保持旧行为：标签后 / 不命中', () => {
  const value = '/Word 文档处理/'
  assert.equal(end(value), null)
})

test('selectSkill 场景：标签后 /查询词 的 start 不吃掉前面的标签', () => {
  // 选中技能时会删掉 [start, caret) 的 /查询词 再插入标签，start 必须正好落在标签末尾
  const value = '/Word 文档处理/wo'
  const slash = parseSlashQuery(value, value.length, map())
  const cleared = value.slice(0, slash.start) + value.slice(value.length)
  assert.equal(cleared, '/Word 文档处理')
})
