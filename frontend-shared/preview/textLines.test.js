import assert from 'node:assert/strict'
import test from 'node:test'

import {
  clampSourceText,
  lineNumberGutterWidth,
  splitSourceLines,
  takeLines,
  TEXT_CHAR_LIMIT,
  TEXT_LINE_PAGE,
} from './textLines.js'

test('换行口径与浏览器一致：CRLF、单个 CR、LF 都算一次换行', () => {
  assert.deepEqual(splitSourceLines('a\r\nb\rc\nd'), ['a', 'b', 'c', 'd'])
})

test('空文本不产生行号，末尾换行保留为一个空行（与文件实际行数一致）', () => {
  assert.deepEqual(splitSourceLines(''), [])
  assert.deepEqual(splitSourceLines(null), [])
  assert.deepEqual(splitSourceLines('a\nb\n'), ['a', 'b', ''])
})

test('按行分页：行号从 1 开始、连续，page 边界不丢行也不重复', () => {
  const lines = Array.from({ length: 5 }, (_, i) => `L${i + 1}`)
  const first = takeLines(lines, 2)
  assert.deepEqual(first.lines, [{ n: 1, text: 'L1' }, { n: 2, text: 'L2' }])
  assert.equal(first.total, 5)
  assert.equal(first.shown, 2)
  assert.equal(first.hasMore, true)

  const all = takeLines(lines, TEXT_LINE_PAGE)
  assert.equal(all.shown, 5)
  assert.equal(all.hasMore, false)
  assert.deepEqual(all.lines.map((l) => l.n), [1, 2, 3, 4, 5])
})

test('非法的行数上限退化成「全显示」，不会渲染出空页', () => {
  for (const bad of [0, -1, NaN, undefined, null]) {
    const page = takeLines(['a', 'b'], bad)
    assert.equal(page.shown, 2)
    assert.equal(page.hasMore, false)
  }
  assert.equal(takeLines([], 10).total, 0)
})

test('超大文本按行边界裁剪，绝不切在行中间', () => {
  assert.deepEqual(clampSourceText('a\nb\nc', 3), { text: 'a\nb', truncated: true, limit: 3 })
  assert.deepEqual(clampSourceText('aaa\nbbb\nccc', 5), { text: 'aaa', truncated: true, limit: 5 })
  // 整篇只有一行、没有换行可退：只能按字符裁，否则一行都显示不出来
  assert.deepEqual(clampSourceText('abcdef', 3), { text: 'abc', truncated: true, limit: 3 })
  assert.deepEqual(clampSourceText('a\nb', 10), { text: 'a\nb', truncated: false, limit: 10 })
  assert.equal(clampSourceText('x'.repeat(TEXT_CHAR_LIMIT + 1)).truncated, true)
})

test('裁剪结果切分后全是完整行（最后一行不带半截字符）', () => {
  const source = '句子一\n句子二\n句子三\n'
  const { text } = clampSourceText(source, 10)
  assert.deepEqual(splitSourceLines(text), ['句子一', '句子二'])
})

test('行号列宽随位数增长且不小于最小值，保证各行行号右对齐不错位', () => {
  assert.equal(lineNumberGutterWidth(9), '40px')
  assert.equal(lineNumberGutterWidth(3800), '56px')
  assert.equal(lineNumberGutterWidth(123456), '74px')
  assert.equal(lineNumberGutterWidth(0), '40px')
  assert.equal(lineNumberGutterWidth(undefined), '40px')
})
