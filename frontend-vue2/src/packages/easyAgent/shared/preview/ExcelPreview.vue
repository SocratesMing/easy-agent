<template>
  <div class="ea-excel-preview">
    <div class="ea-excel-tabs" v-if="sheets.length > 1">
      <button
        v-for="(sheet, index) in sheets"
        :key="index"
        type="button"
        class="ea-excel-tab"
        :class="{ 'is-active': activeSheet === index }"
        @click="activeSheet = index"
      >
        {{ sheet.name }}
      </button>
    </div>
    <div class="ea-excel-body" v-if="sheets.length > 0">
      <table class="ea-excel-table" v-if="currentRows.length > 0">
        <tbody>
          <tr v-for="(row, rowIndex) in currentRows" :key="rowIndex" class="ea-excel-tr">
            <td
              v-for="cell in row"
              :key="cell.key"
              class="ea-excel-td"
              :class="{ 'ea-excel-td--head': rowIndex === 0 }"
              :rowspan="cell.rowspan > 1 ? cell.rowspan : null"
              :colspan="cell.colspan > 1 ? cell.colspan : null"
            >{{ cell.text }}</td>
          </tr>
        </tbody>
      </table>
      <div v-else class="ea-excel-empty">该工作表为空</div>
    </div>
    <div v-else-if="loading" class="ea-excel-empty">加载中...</div>
    <div v-else-if="error" class="ea-excel-empty">{{ error }}</div>
    <div v-else class="ea-excel-empty">表格内容为空</div>
  </div>
</template>

<script>
import ExcelJS from 'exceljs'
import { requestArrayBuffer } from '../../utils/request.js'
import { buildSheetRows, buildRowsFromMatrix } from './excelGrid.js'

// xlsx/xlsm 是 zip 容器（以 "PK" 开头）；.xls（BIFF）等旧格式 ExcelJS 无法解析，交给 SheetJS
function isZip(buffer) {
  const head = new Uint8Array(buffer, 0, Math.min(2, buffer.byteLength))
  return head[0] === 0x50 && head[1] === 0x4b
}

async function parseWithExcelJS(buffer) {
  const workbook = new ExcelJS.Workbook()
  await workbook.xlsx.load(buffer)
  return workbook.worksheets.map(ws => ({ name: ws.name, rows: buildSheetRows(ws) }))
}

async function parseWithSheetJS(buffer) {
  const XLSX = await import('xlsx')
  const workbook = XLSX.read(buffer, { type: 'array' })
  return workbook.SheetNames.map(name => {
    const ws = workbook.Sheets[name]
    const origin = ws['!ref'] ? XLSX.utils.decode_range(ws['!ref']).s : null
    const matrix = XLSX.utils.sheet_to_json(ws, { header: 1, defval: '', raw: false, blankrows: true })
    return { name, rows: buildRowsFromMatrix(matrix, ws['!merges'], origin) }
  })
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
  computed: {
    currentRows() {
      const sheet = this.sheets[this.activeSheet]
      return sheet ? sheet.rows : []
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
/*
 * 类名统一带 ea-excel- 前缀：内网宿主常有全局 .header / .active 等样式，
 * 曾把表头 <td class="header"> 变成 display:block，表头竖排、整表错位。
 * 表格元素再显式声明 display / float 等，避免宿主对 table/tr/td 的全局样式渗入。
 */
.ea-excel-preview {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #fafafa;
}

.ea-excel-tabs {
  display: flex;
  padding: 8px;
  background: #e5e7eb;
  flex-shrink: 0;
  overflow-x: auto;
}

.ea-excel-tab {
  margin: 0 4px 0 0;
  padding: 6px 12px;
  background: white;
  color: #374151;
  border: 1px solid #d1d5db;
  border-radius: 4px;
  font-size: 12px;
  line-height: 1.4;
  cursor: pointer;
  white-space: nowrap;
  transition: background 0.15s ease;
}

.ea-excel-tab:hover {
  background: #f3f4f6;
}

.ea-excel-tab.is-active {
  background: #166534;
  color: white;
  border-color: #166534;
}

.ea-excel-body {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 8px;
}

.ea-excel-table {
  display: table;
  table-layout: auto;
  border-collapse: collapse;
  border-spacing: 0;
  width: auto;
  min-width: 100%;
  margin: 0;
  background: white;
}

.ea-excel-table > tbody {
  display: table-row-group;
}

.ea-excel-tr {
  display: table-row;
  height: auto;
  background: transparent;
}

.ea-excel-td {
  display: table-cell;
  float: none;
  position: static;
  width: auto;
  height: auto;
  margin: 0;
  border: 1px solid #e5e7eb;
  padding: 6px 10px;
  font-size: 12px;
  line-height: 1.5;
  color: #374151;
  background: white;
  text-align: left;
  vertical-align: middle;
  min-width: 80px;
  white-space: pre-wrap;
  word-break: break-word;
}

.ea-excel-td--head {
  background: #f3f4f6;
  font-weight: 500;
}

.ea-excel-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #9ca3af;
  font-size: 14px;
}
</style>
