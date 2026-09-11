import assert from 'node:assert/strict'
import test from 'node:test'
import ExcelJS from 'exceljs'

import { workbookToPreviewSheets } from './excelPreviewRows.js'

test('keeps Excel columns, empty cells, formulas and multiple sheets', () => {
  const workbook = new ExcelJS.Workbook()
  const first = workbook.addWorksheet('指标')
  first.addRow(['品种', '', '数值'])
  first.addRow(['黄金', null, 719])
  first.getCell('C3').value = { formula: 'C2+1', result: 720 }
  const second = workbook.addWorksheet('说明')
  second.addRow(['已完成'])

  const sheets = workbookToPreviewSheets(workbook)

  assert.equal(sheets.length, 2)
  assert.deepEqual(sheets[0].data[0], ['品种', '', '数值'])
  assert.deepEqual(sheets[0].data[1], ['黄金', '', '719'])
  assert.equal(sheets[0].data[2][2], '720')
  assert.deepEqual(sheets[1].data[0], ['已完成'])
})
