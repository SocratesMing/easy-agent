export function workbookToPreviewSheets(workbook) {
  return workbook.worksheets.map(ws => {
    const data = []
    ws.eachRow(row => {
      const rowData = []
      row.eachCell({ includeEmpty: true }, (cell, colNumber) => {
        rowData[colNumber - 1] = cell.text ?? ''
      })
      data.push(rowData)
    })
    return { name: ws.name, data }
  })
}
