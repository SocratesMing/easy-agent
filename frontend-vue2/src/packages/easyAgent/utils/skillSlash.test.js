import assert from 'node:assert/strict'
import test from 'node:test'

import { buildMessageText } from './skillSlash.js'
import { buildTokenMap, stripSkillTokens, tokenForSkill } from './skillPillTokens.js'

test('buildMessageText：只去首尾空白，换行与缩进原样保留', () => {
  assert.equal(
    buildMessageText('\n  帮我看下这段代码：\n\ndef foo():\n    return 1\n  '),
    '帮我看下这段代码：\n\ndef foo():\n    return 1'
  )
  assert.equal(buildMessageText('名称      数量\r\n苹果      10'), '名称      数量\n苹果      10')
  assert.equal(buildMessageText('   \n  '), '')
  assert.equal(buildMessageText(null), '')
})

test('发送正文：剥掉技能标签后多行内容原样保留', () => {
  const skill = { dir_name: 'docx', name: 'Word 文档处理' }
  const text = tokenForSkill(skill) + '整理下面的日志：\nERROR 连接超时\n    WARN 重试'
  assert.equal(
    buildMessageText(stripSkillTokens(text, buildTokenMap([skill]))),
    '整理下面的日志：\nERROR 连接超时\n    WARN 重试'
  )
})
