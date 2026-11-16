/**
 * Excel 预览的纯数据层：把 ExcelJS 工作表 / SheetJS 二维数组转成规整的表格网格。
 * 与 ExcelPreview.vue 分开，便于在 node --test 里直接测。
 *
 * 输出：rows[行][单元格] = { key, text, rowspan, colspan }
 * 每行补齐到相同列数、中间空行保留、合并单元格被覆盖的位置不输出（由 rowspan/colspan 占位）。
 */

// 单表最多渲染的行/列，防止超大表格把页面卡死
const MAX_ROWS = 2000
const MAX_COLS = 200

function pad(n) {
  return n < 10 ? '0' + n : String(n)
}

function formatDate(d) {
  if (isNaN(d.getTime())) return ''
  const date = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
  const h = d.getUTCHours()
  const m = d.getUTCMinutes()
  const s = d.getUTCSeconds()
  if (!h && !m && !s) return date
  return `${date} ${pad(h)}:${pad(m)}:${pad(s)}`
}

// ExcelJS 的单元格值可能是字符串/数字/日期，也可能是富文本、公式、超链接、错误等对象
export function formatCellValue(value) {
  if (value === null || value === undefined) return ''
  if (value instanceof Date) return formatDate(value)
  if (typeof value !== 'object') return String(value)
  if (Array.isArray(value.richText)) return value.richText.map(part => (part && part.text) || '').join('')
  if ('formula' in value || 'sharedFormula' in value || 'result' in value) {
    return formatCellValue(value.result)
  }
  if ('text' in value) return formatCellValue(value.text)
  if ('error' in value) return String(value.error)
  return ''
}

function columnIndex(letters) {
  let n = 0
  for (let i = 0; i < letters.length; i++) n = n * 26 + (letters.charCodeAt(i) - 64)
  return n
}

// "A1:C3" → { top, left, bottom, right }（1 起始）
function parseRange(range) {
  const m = /^\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?$/.exec(String(range || '').toUpperCase())
  if (!m) return null
  const left = columnIndex(m[1])
  const top = Number(m[2])
  const right = m[3] ? columnIndex(m[3]) : left
  const bottom = m[4] ? Number(m[4]) : top
  return {
    top: Math.min(top, bottom),
    bottom: Math.max(top, bottom),
    left: Math.min(left, right),
    right: Math.max(left, right),
  }
}

// ExcelJS 路径（xlsx）
export function buildSheetRows(ws) {
  const values = {}
  let maxRow = 0
  let maxCol = 0
  ws.eachRow({ includeEmpty: false }, (row, rowNumber) => {
    row.eachCell({ includeEmpty: false }, cell => {
      if (cell.isMerged && cell.master && cell.master !== cell) return
      const col = cell.col || cell.column
      const text = formatCellValue(cell.value)
      if (text === '') return
      values[`${rowNumber}:${col}`] = text
      if (rowNumber > maxRow) maxRow = rowNumber
      if (col > maxCol) maxCol = col
    })
  })

  const merges = []
  const mergeList = (ws.model && ws.model.merges) || []
  mergeList.forEach(range => {
    const r = parseRange(range)
    if (!r) return
    merges.push(r)
    if (r.bottom > maxRow) maxRow = r.bottom
    if (r.right > maxCol) maxCol = r.right
  })

  return layoutGrid(values, merges, maxRow, maxCol)
}

// SheetJS 路径（.xls 等）：sheet_to_json(header:1) 得到的二维数组 + ws['!merges']（0 起始）。
// origin 是 ws['!ref'] 的左上角（0 起始），数据不从 A1 开始时用它还原真实位置。
export function buildRowsFromMatrix(matrix, sheetMerges, origin) {
  const r0 = (origin && origin.r) || 0
  const c0 = (origin && origin.c) || 0
  const values = {}
  let maxRow = 0
  let maxCol = 0
  ;(matrix || []).forEach((row, r) => {
    ;(row || []).forEach((value, c) => {
      const text = formatCellValue(value)
      if (text === '') return
      const rowNo = r0 + r + 1
      const colNo = c0 + c + 1
      values[`${rowNo}:${colNo}`] = text
      if (rowNo > maxRow) maxRow = rowNo
      if (colNo > maxCol) maxCol = colNo
    })
  })

  const merges = []
  ;(sheetMerges || []).forEach(m => {
    if (!m || !m.s || !m.e) return
    const r = { top: m.s.r + 1, left: m.s.c + 1, bottom: m.e.r + 1, right: m.e.c + 1 }
    merges.push(r)
    if (r.bottom > maxRow) maxRow = r.bottom
    if (r.right > maxCol) maxCol = r.right
  })

  return layoutGrid(values, merges, maxRow, maxCol)
}

function layoutGrid(values, merges, maxRow, maxCol) {
  maxRow = Math.min(maxRow, MAX_ROWS)
  maxCol = Math.min(maxCol, MAX_COLS)

  const covered = {}
  const spans = {}
  merges.forEach(r => {
    if (r.top > maxRow || r.left > maxCol) return
    const bottom = Math.min(r.bottom, maxRow)
    const right = Math.min(r.right, maxCol)
    spans[`${r.top}:${r.left}`] = { rowspan: bottom - r.top + 1, colspan: right - r.left + 1 }
    for (let row = r.top; row <= bottom; row++) {
      for (let col = r.left; col <= right; col++) {
        if (row !== r.top || col !== r.left) covered[`${row}:${col}`] = true
      }
    }
  })

  const rows = []
  for (let row = 1; row <= maxRow; row++) {
    const cells = []
    for (let col = 1; col <= maxCol; col++) {
      const key = `${row}:${col}`
      if (covered[key]) continue
      const span = spans[key] || { rowspan: 1, colspan: 1 }
      cells.push({ key, text: values[key] || '', rowspan: span.rowspan, colspan: span.colspan })
    }
    rows.push(cells)
  }
  return rows
}
