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
    <div class="sheet-content" v-if="currentRows.length">
      <table class="excel-table">
        <tbody>
          <tr v-for="(row, rowIndex) in currentRows" :key="rowIndex">
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
      <div v-if="truncated" class="excel-hint">表格较大，仅显示前 {{ MAX_ROWS }} 行</div>
    </div>
    <div v-else-if="loading" class="preview-loading">加载中...</div>
    <div v-else class="preview-error">{{ error || '该表格没有可显示的内容' }}</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch } from 'vue'
import * as XLSX from 'xlsx'

const MAX_ROWS = 5000

const props = defineProps({
  fileUrl: {
    type: String,
    default: ''
  }
})

const sheets = ref([])
const activeSheet = ref(0)
const loading = ref(false)
const error = ref('')

const currentRows = computed(() => {
  const sheet = sheets.value[activeSheet.value]
  return sheet ? sheet.data : []
})
const truncated = computed(() => currentRows.value.length >= MAX_ROWS)

function parseWorkbook(buffer) {
  // 用 SheetJS 而不是 ExcelJS：ExcelJS 3.x 解析带 drawing/批注（anchor 缺失）的 xlsx 会抛
  // "Cannot read properties of undefined (reading 'anchors')"，表现为预览永久空白；
  // SheetJS 容错更好，且顺带支持老版 .xls。raw:false 让日期/数字按单元格格式输出。
  const workbook = XLSX.read(new Uint8Array(buffer), { type: 'array' })
  return workbook.SheetNames.map((name) => ({
    name,
    data: XLSX.utils
      .sheet_to_json(workbook.Sheets[name], { header: 1, raw: false, defval: '', blankrows: false })
      .slice(0, MAX_ROWS)
      .map((row) => row.map((cell) => (cell == null ? '' : String(cell))))
  }))
}

async function loadExcel() {
  if (!props.fileUrl) return

  loading.value = true
  error.value = ''
  try {
    const response = await fetch(props.fileUrl)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const buffer = await response.arrayBuffer()

    const parsed = parseWorkbook(buffer)
    if (!parsed.length || !parsed[0].data.length) throw new Error('工作簿没有可显示的工作表')

    sheets.value = parsed
    activeSheet.value = 0
  } catch (e) {
    console.error('[ExcelPreview] 解析失败:', e)
    sheets.value = []
    error.value = 'Excel 解析失败，请下载后查看'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadExcel()
})

watch(() => props.fileUrl, () => {
  loadExcel()
})
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

.excel-hint {
  padding: 8px;
  color: #9ca3af;
  font-size: 12px;
}

.preview-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #9ca3af;
  font-size: 14px;
}

.preview-error {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #9ca3af;
  font-size: 14px;
}
</style>
