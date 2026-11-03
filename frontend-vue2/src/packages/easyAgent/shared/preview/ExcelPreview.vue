<template>
  <div class="excel-preview-container">
    <div class="sheet-tabs" v-if="sheets.length > 1">
      <button 
        v-for="(sheet, index) in sheets" 
        :key="index"
        class="sheet-tab"
        :class="{ active: activeSheet === index }"
        @click="activeSheet = index"
      >
        {{ sheet.name }}
      </button>
    </div>
    <div class="sheet-content" v-if="sheets.length > 0">
      <table class="excel-table">
        <tbody>
          <tr v-for="(row, rowIndex) in sheets[activeSheet]?.data" :key="rowIndex">
            <td 
              v-for="(cell, colIndex) in row" 
              :key="colIndex"
              :class="{ 'header': rowIndex === 0 }"
            >
              {{ cell }}
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else-if="loading" class="preview-loading">加载中...</div>
    <div v-else-if="error" class="preview-loading">{{ error }}</div>
    <div v-else class="preview-loading">表格内容为空</div>
  </div>
</template>

<script>
import ExcelJS from 'exceljs'
import { requestArrayBuffer } from '../../utils/request.js'

// xlsx/xlsm 是 zip 容器（以 "PK" 开头）；.xls（BIFF）等旧格式 ExcelJS 无法解析，交给 SheetJS
function isZip(buffer) {
  const head = new Uint8Array(buffer, 0, Math.min(2, buffer.byteLength))
  return head[0] === 0x50 && head[1] === 0x4b
}

async function parseWithExcelJS(buffer) {
  const workbook = new ExcelJS.Workbook()
  await workbook.xlsx.load(buffer)
  return workbook.worksheets.map(ws => {
    const data = []
    ws.eachRow((row) => {
      const rowData = []
      row.eachCell({ includeEmpty: true }, cell => {
        rowData[cell.column - 1] = cell.value ?? ''
      })
      data.push(rowData)
    })
    return { name: ws.name, data }
  })
}

async function parseWithSheetJS(buffer) {
  const XLSX = await import('xlsx')
  const workbook = XLSX.read(buffer, { type: 'array' })
  return workbook.SheetNames.map(name => ({
    name,
    data: XLSX.utils.sheet_to_json(workbook.Sheets[name], { header: 1, defval: '', raw: false }),
  }))
}
export default {
  props: {
    fileUrl: {
      type: String,
      default: ''
    }
  },
  data() {
    return {
      sheets: [],
      activeSheet: 0,
      loading: false,
      error: ''
    }
  },
  methods: {
    async loadExcel() {
      if (!this.fileUrl) return

      const seq = (this._loadSeq = (this._loadSeq || 0) + 1)
      this.loading = true
      this.error = ''
      this.sheets = []
      try {
        const arrayBuffer = await requestArrayBuffer({ url: this.fileUrl })
        let sheets
        if (isZip(arrayBuffer)) {
          try {
            sheets = await parseWithExcelJS(arrayBuffer)
          } catch (e) {
            console.warn('Excel preview: ExcelJS 解析失败，改用 SheetJS:', e)
            sheets = await parseWithSheetJS(arrayBuffer)
          }
        } else {
          sheets = await parseWithSheetJS(arrayBuffer)
        }
        if (seq !== this._loadSeq) return
        this.sheets = sheets
        this.activeSheet = 0
      } catch (e) {
        if (seq !== this._loadSeq) return
        console.error('Excel preview error:', e)
        this.sheets = []
        this.error = 'Excel 预览加载失败，请下载后查看'
      } finally {
        if (seq === this._loadSeq) this.loading = false
      }
    }
  },
  mounted() {
    this.loadExcel()
  },
  watch: {
    fileUrl() {
      this.loadExcel()
    }
  }
}
</script>

<style scoped>
.excel-preview-container {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #fafafa;
}

.sheet-tabs {
  display: flex;
  gap: 4px;
  padding: 8px;
  background: #e5e7eb;
  flex-shrink: 0;
  overflow-x: auto;
}

.sheet-tab {
  padding: 6px 12px;
  background: white;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  font-size: 12px;
  cursor: pointer;
  white-space: nowrap;
  transition: all 0.15s ease;
}

.sheet-tab:hover {
  background: #f3f4f6;
}

.sheet-tab.active {
  background: #166534;
  color: white;
  border-color: #166534;
}

.sheet-content {
  flex: 1;
  overflow: auto;
  padding: 8px;
}

.excel-table {
  border-collapse: collapse;
  width: 100%;
  background: white;
}

.excel-table td {
  border: 1px solid #e5e7eb;
  padding: 6px 10px;
  font-size: 12px;
  color: #374151;
  text-align: left;
  min-width: 80px;
}

.excel-table td.header {
  background: #f3f4f6;
  font-weight: 500;
}

.preview-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #9ca3af;
  font-size: 14px;
}
</style>
