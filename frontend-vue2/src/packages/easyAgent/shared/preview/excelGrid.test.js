import assert from 'node:assert/strict'
import test from 'node:test'

import { buildRowsFromMatrix, buildSheetRows, formatCellValue } from './excelGrid.js'

// 最小化模拟 ExcelJS Worksheet：eachRow / eachCell / model.merges
function fakeSheet(cells, merges = []) {
  const rows = new Map()
  for (const [address, value] of Object.entries(cells)) {
    const m = /^([A-Z]+)(\d+)$/.exec(address)
    const col = m[1].split('').reduce((n, ch) => n * 26 + ch.charCodeAt(0) - 64, 0)
    const row = Number(m[2])
    if (!rows.has(row)) rows.set(row, [])
    rows.get(row).push({ col, value })
  }
  return {
    model: { merges },
    eachRow(_opts, cb) {
      ;[...rows.keys()].sort((a, b) => a - b).forEach(r => cb({
        eachCell(_o, cellCb) { rows.get(r).sort((a, b) => a.col - b.col).forEach(cellCb) },
      }, r))
    },
  }
}

const texts = rows => rows.map(row => row.map(cell => cell.text))

test('每行补齐到相同列数，表头不会因为稀疏行错位', () => {
  const ws = fakeSheet({ A1: '标题1', B1: '标题2', C1: '标题3', A2: 1, B2: 2, C2: 3, A3: 4, B3: 5, C4: 6 })
  assert.deepEqual(texts(buildSheetRows(ws)), [
    ['标题1', '标题2', '标题3'],
    ['1', '2', '3'],
    ['4', '5', ''],
    ['', '', '6'],
  ])
})

test('空行保留，合并单元格转成 rowspan/colspan', () => {
  const ws = fakeSheet({ A1: '合并', A3: 'x' }, ['A1:B2'])
  const rows = buildSheetRows(ws)
  assert.deepEqual(texts(rows), [['合并'], [], ['x', '']])
  assert.equal(rows[0][0].rowspan, 2)
  assert.equal(rows[0][0].colspan, 2)
})

test('富文本、公式、超链接、日期、错误值都转成可读文本', () => {
  assert.equal(formatCellValue({ richText: [{ text: 'a' }, { text: 'b' }] }), 'ab')
  assert.equal(formatCellValue({ formula: 'SUM(A1:A2)', result: 42 }), '42')
  assert.equal(formatCellValue({ text: '链接', hyperlink: 'http://x' }), '链接')
  assert.equal(formatCellValue(new Date(Date.UTC(2026, 10, 3))), '2026-11-03')
  assert.equal(formatCellValue({ error: '#DIV/0!' }), '#DIV/0!')
  assert.equal(formatCellValue(null), '')
  assert.equal(formatCellValue(false), 'false')
})

test('SheetJS 二维数组：按 !ref 起点还原位置，合并单元格转 span', () => {
  const rows = buildRowsFromMatrix([['标题', ''], ['a', 'b']], [{ s: { r: 1, c: 1 }, e: { r: 1, c: 2 } }], { r: 1, c: 1 })
  assert.deepEqual(texts(rows), [['', '', ''], ['', '标题'], ['', 'a', 'b']])
  assert.equal(rows[1][1].colspan, 2)
})
