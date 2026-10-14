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
    <div v-else class="preview-loading">{{ previewError || '加载中...' }}</div>
  </div>
</template>

<script>
import { ref, onMounted, watch } from 'vue'
import ExcelJS from 'exceljs'
import { workbookToPreviewSheets } from '../utils/excelPreviewRows.js'
export default {
  props: {
  fileUrl: {
    type: String,
    default: ''
  }
},
  setup(props, { emit }) {
const sheets = ref([])
const activeSheet = ref(0)
const previewError = ref('')

async function loadExcel() {
  if (!props.fileUrl) return
  
  try {
    previewError.value = ''
    const response = await fetch(props.fileUrl)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const arrayBuffer = await response.arrayBuffer()
    
    const workbook = new ExcelJS.Workbook()
    await workbook.xlsx.load(arrayBuffer)
    
    sheets.value = workbookToPreviewSheets(workbook)
    
    activeSheet.value = 0
  } catch (e) {
    console.error('Excel preview error:', e)
    sheets.value = []
    previewError.value = '无法预览该 Excel 文件，请下载后查看'
  }
}

onMounted(() => {
  loadExcel()
})

watch(() => props.fileUrl, () => {
  loadExcel()
})

    return {
      activeSheet,
      ExcelJS,
      loadExcel,
      onMounted,
      previewError,
      ref,
      sheets,
      watch,
    }
  },
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
