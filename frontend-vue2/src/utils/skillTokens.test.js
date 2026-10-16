import assert from 'node:assert/strict'
import test from 'node:test'

import { parseLeadingSkillTokens, stripSkillPrefix } from './skillTokens.js'

const KNOWN = ['pptx-generator', 'docx', 'skill_creator']

test('splits leading skill tokens from the remaining body', () => {
  const parsed = parseLeadingSkillTokens('/pptx-generator 生成一份PPT创作文档', KNOWN)
  assert.deepEqual(parsed.dirNames, ['pptx-generator'])
  assert.equal(parsed.body, '生成一份PPT创作文档')
})

test('supports several consecutive skill tokens', () => {
  const parsed = parseLeadingSkillTokens('/docx /skill_creator 一起处理', KNOWN)
  assert.deepEqual(parsed.dirNames, ['docx', 'skill_creator'])
  assert.equal(parsed.body, '一起处理')
})

test('keeps the whole text when it only contains skill tokens', () => {
  const parsed = parseLeadingSkillTokens('/docx', KNOWN)
  assert.deepEqual(parsed.dirNames, ['docx'])
  assert.equal(parsed.body, '')
})

test('ignores unknown directory names so paths are not mistaken for skills', () => {
  const parsed = parseLeadingSkillTokens('/usr/bin/env 看下这个路径', KNOWN)
  assert.deepEqual(parsed.dirNames, [])
  assert.equal(parsed.body, '/usr/bin/env 看下这个路径')
})

test('keeps skill-looking text that appears mid-message untouched', () => {
  const parsed = parseLeadingSkillTokens('帮我调用 /docx 处理一下', KNOWN)
  assert.deepEqual(parsed.dirNames, [])
  assert.equal(parsed.body, '帮我调用 /docx 处理一下')
})

test('does nothing without a known skill directory', () => {
  assert.deepEqual(parseLeadingSkillTokens('/docx 正文', []), {
    dirNames: [],
    body: '/docx 正文',
  })
  assert.deepEqual(parseLeadingSkillTokens('/docx 正文', null), {
    dirNames: [],
    body: '/docx 正文',
  })
})

test('tolerates empty and non-string content', () => {
  assert.deepEqual(parseLeadingSkillTokens('', KNOWN), { dirNames: [], body: '' })
  assert.deepEqual(parseLeadingSkillTokens(undefined, KNOWN), { dirNames: [], body: '' })
})

const DIRECTORY = {
  'pptx-generator': { dir_name: 'pptx-generator', name: 'PPT 生成' },
  docx: { dir_name: 'docx', name: 'Word 文档' },
}

test('stripSkillPrefix removes the prefix from session titles', () => {
  assert.equal(stripSkillPrefix('/docx 帮我做一个 word 文档', KNOWN, DIRECTORY), '帮我做一个 word 文档')
  assert.equal(stripSkillPrefix('/pptx-generator 生成一份PPT', KNOWN, DIRECTORY), '生成一份PPT')
})

test('stripSkillPrefix falls back to the skill name for skill-only titles', () => {
  assert.equal(stripSkillPrefix('/docx', KNOWN, DIRECTORY), 'Word 文档')
  // 标题按 15 字截断后只剩省略号：仍要退回技能名，不能显示成 "/skill-creator ..."
  assert.equal(stripSkillPrefix('/docx ...', KNOWN, DIRECTORY), 'Word 文档')
  // 没有技能信息时退回目录名
  assert.equal(stripSkillPrefix('/docx', KNOWN, {}), 'docx')
})

test('stripSkillPrefix keeps titles untouched when nothing matches', () => {
  assert.equal(stripSkillPrefix('/workspace 下的文件帮我看看', KNOWN, DIRECTORY), '/workspace 下的文件帮我看看')
  assert.equal(stripSkillPrefix('为什么一直没有完成任务', KNOWN, DIRECTORY), '为什么一直没有完成任务')
  assert.equal(stripSkillPrefix('/docx 你好', [], DIRECTORY), '/docx 你好')
  assert.equal(stripSkillPrefix('', KNOWN, DIRECTORY), '')
})
