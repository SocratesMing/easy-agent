import test from 'node:test'
import assert from 'node:assert/strict'
import {
  CONTEXT_WINDOW_FALLBACK, contextBudget, estimateTokens, formatCharCount, formatContextWindow,
  longMessageFileContent, longMessageFileName, longMessageNotice, needsOffload, usagePercent,
} from './longMessage.js'

const usage = over => ({ contextLength: CONTEXT_WINDOW_FALLBACK, usedTokens: 0, autoCompressTokens: null, ...over })

test('token 估算与 compaction 同口径：一个汉字约 1 token，英文四字符约 1 token', () => {
  assert.equal(estimateTokens(''), 0)
  assert.equal(estimateTokens('测试'), 2)
  assert.equal(estimateTokens('abcd'), 1)
  assert.equal(estimateTokens('ab测试'), 3)
  assert.equal(estimateTokens(null), 0)
})

test('可用预算扣掉已用、输出预留与安全余量', () => {
  // 131072 - 8192(新会话固定开销) - (8192 输出 + 15%*131072)
  assert.equal(contextBudget(usage()), 95027)
  // 有历史时按真实用量扣，不再套固定开销
  assert.equal(contextBudget(usage({ usedTokens: 100000 })), 3219)
  // 自动压缩阈值更小时以它为上限：60000 - 8192 - (8192 + 9000)
  assert.equal(contextBudget(usage({ autoCompressTokens: 60000 })), 34616)
  // 已用超过窗口时不给负预算
  assert.equal(contextBudget(usage({ usedTokens: 200000 })), 0)
})

test('空会话里普通长度不提示，接近窗口的量才提示', () => {
  assert.equal(needsOffload('字'.repeat(1000), usage()), false)
  assert.equal(needsOffload('字'.repeat(40000), usage()), false)
  // 约 4.8 万汉字 ≈ 4.8 万 token，已超过 95019 的一半
  assert.equal(needsOffload('字'.repeat(48000), usage()), true)
})

test('历史占满窗口后，几千字的输入也会提示', () => {
  const crowded = usage({ contextLength: 131072, usedTokens: 120000 })
  assert.equal(needsOffload('字'.repeat(3000), crowded), true)
  // 同一段输入在空会话里不该报警
  assert.equal(needsOffload('字'.repeat(3000), usage()), false)
})

test('低于最小提示量不打扰，即使预算已经耗尽', () => {
  const full = usage({ usedTokens: 200000 })
  assert.equal(needsOffload('字'.repeat(500), full), false)
  assert.equal(needsOffload('字'.repeat(2500), full), true)
})

test('窗口与已用百分比用于提示文案', () => {
  assert.equal(formatContextWindow(131072), '131k')
  assert.equal(formatContextWindow(null), '131k')
  assert.equal(formatContextWindow(32000), '32k')
  assert.equal(usagePercent(usage({ usedTokens: 65536 })), 50)
  assert.equal(usagePercent(usage()), 0)
  assert.equal(usagePercent(usage({ usedTokens: 999999 })), 100)
})

test('字数用「万」展示，避免一长串数字', () => {
  assert.equal(formatCharCount(0), '0 字')
  assert.equal(formatCharCount(9999), '9999 字')
  assert.equal(formatCharCount(20000), '2 万字')
  assert.equal(formatCharCount(217387), '21.7 万字')
  assert.equal(formatCharCount(1234567), '123 万字')
  assert.equal(formatCharCount(undefined), '0 字')
})

test('落盘正文把超长单行切成小段，已有换行原样保留', () => {
  assert.equal(longMessageFileContent('第一行\n第二行'), '第一行\n第二行')
  assert.equal(longMessageFileContent('甲'.repeat(1200)), ['甲'.repeat(500), '甲'.repeat(500), '甲'.repeat(200)].join('\n'))
  const mixed = `短行\n${'乙'.repeat(600)}\n尾行`
  const lines = longMessageFileContent(mixed).split('\n')
  assert.deepEqual(lines.map(l => l.length), [2, 500, 100, 2])
  assert.equal(lines[0], '短行')
  assert.equal(lines[3], '尾行')
  assert.equal(longMessageFileContent(''), '')
  assert.equal(longMessageFileContent(null), '')
})

test('落盘名带秒级时间戳且补零，同名不会互相覆盖', () => {
  assert.equal(longMessageFileName(new Date(2026, 10, 16, 9, 5, 3)), '长文本-20261116-090503.txt')
  assert.equal(longMessageFileName(new Date(2026, 0, 2, 23, 59, 59)), '长文本-20260102-235959.txt')
  const a = longMessageFileName(new Date(2026, 10, 16, 9, 5, 3))
  const b = longMessageFileName(new Date(2026, 10, 16, 9, 5, 4))
  assert.notEqual(a, b)
})

test('替换后的正文保留开头并指向附件，短内容不截断也不加省略号', () => {
  const long = '甲'.repeat(500)
  const notice = longMessageNotice(long, '长文本-20261116-090503.txt')
  assert.match(notice, /^甲{200}……\n\n（以上内容较长，已存为附件 长文本-20261116-090503\.txt，请阅读该文件后再回答。）$/)
  assert.equal(notice.length < long.length, true, '替换后必须明显变短')

  const short = longMessageNotice('就这样', 'a.txt')
  assert.match(short, /^就这样\n\n（以上内容较长，已存为附件 a\.txt，请阅读该文件后再回答。）$/)
  assert.doesNotMatch(short, /……/)
})
