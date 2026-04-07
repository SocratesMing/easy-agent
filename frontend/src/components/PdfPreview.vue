<template>
  <div class="pdf-preview-container">
    <div class="pdf-toolbar">
      <button class="pdf-btn" @click="prevPage" :disabled="currentPage <= 1">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="15 18 9 12 15 6"></polyline>
        </svg>
      </button>
      <span class="pdf-page-info">{{ currentPage }} / {{ totalPages }}</span>
      <button class="pdf-btn" @click="nextPage" :disabled="currentPage >= totalPages">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="9 18 15 12 9 6"></polyline>
        </svg>
      </button>
      <button class="pdf-btn" @click="zoomIn">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8"></circle>
          <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          <line x1="11" y1="8" x2="11" y2="14"></line>
          <line x1="8" y1="11" x2="14" y2="11"></line>
        </svg>
      </button>
      <button class="pdf-btn" @click="zoomOut">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8"></circle>
          <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
          <line x1="8" y1="11" x2="14" y2="11"></line>
        </svg>
      </button>
    </div>
    <div class="pdf-content" ref="containerRef">
      <canvas ref="canvasRef"></canvas>
    </div>
    <div v-if="loading" class="pdf-loading">加载中...</div>
    <div v-if="errorMsg" class="pdf-error">
      <span>{{ errorMsg }}</span>
      <a :href="fileUrl" download class="download-link">下载文件</a>
    </div>
  </div>
</template>

<script setup>
import { ref, onMounted, watch, onUnmounted } from 'vue'
import * as pdfjsLib from 'pdfjs-dist/legacy/build/pdf.mjs'
import pdfjsWorker from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs?url'

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker

const props = defineProps({
  fileUrl: {
    type: String,
    default: ''
  }
})

const containerRef = ref(null)
const canvasRef = ref(null)
const currentPage = ref(1)
const totalPages = ref(0)
const scale = ref(1.2)
const loading = ref(true)
const errorMsg = ref('')
let pdfDoc = null

const CMAP_URL = 'https://unpkg.com/pdfjs-dist@4.10.38/cmaps/'
const STANDARD_FONT_DATA_URL = 'https://unpkg.com/pdfjs-dist@4.10.38/standard_fonts/'

async function loadPdf() {
  if (!props.fileUrl) return
  
  loading.value = true
  errorMsg.value = ''
  
  try {
    const response = await fetch(props.fileUrl)
    
    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }
    
    const arrayBuffer = await response.arrayBuffer()
    
    if (arrayBuffer.byteLength === 0) {
      throw new Error('文件内容为空')
    }
    
    pdfDoc = await pdfjsLib.getDocument({ 
      data: arrayBuffer,
      cMapUrl: CMAP_URL,
      cMapPacked: true,
      standardFontDataUrl: STANDARD_FONT_DATA_URL,
      useSystemFonts: true,
      disableFontFace: false,
      isEvalSupported: false,
      useWorkerFetch: false
    }).promise
    
    totalPages.value = pdfDoc.numPages
    currentPage.value = 1
    
    await renderPage()
  } catch (e) {
    console.error('PDF load error:', e)
    errorMsg.value = `PDF 加载失败: ${e.message}`
  } finally {
    loading.value = false
  }
}

async function renderPage() {
  if (!pdfDoc || !canvasRef.value) return
  
  try {
    const page = await pdfDoc.getPage(currentPage.value)
    const viewport = page.getViewport({ scale: scale.value })
    
    const canvas = canvasRef.value
    const context = canvas.getContext('2d')
    
    canvas.height = viewport.height
    canvas.width = viewport.width
    
    const renderContext = {
      canvasContext: context,
      viewport: viewport,
      enableWebGL: false,
      renderInteractiveForms: false
    }
    
    await page.render(renderContext).promise
  } catch (e) {
    console.error('Render page error:', e)
  }
}

function prevPage() {
  if (currentPage.value > 1) {
    currentPage.value--
    renderPage()
  }
}

function nextPage() {
  if (currentPage.value < totalPages.value) {
    currentPage.value++
    renderPage()
  }
}

function zoomIn() {
  scale.value = Math.min(scale.value + 0.25, 3)
  renderPage()
}

function zoomOut() {
  scale.value = Math.max(scale.value - 0.25, 0.5)
  renderPage()
}

onMounted(() => {
  loadPdf()
})

watch(() => props.fileUrl, () => {
  if (pdfDoc) {
    pdfDoc.destroy()
    pdfDoc = null
  }
  currentPage.value = 1
  totalPages.value = 0
  loadPdf()
})

onUnmounted(() => {
  if (pdfDoc) {
    pdfDoc.destroy()
  }
})
</script>

<style scoped>
.pdf-preview-container {
  width: 100%;
  height: 100%;
  display: flex;
  flex-direction: column;
  background: #525659;
  position: relative;
}

.pdf-toolbar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 8px;
  background: #374151;
  flex-shrink: 0;
}

.pdf-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  background: #4b5563;
  border: none;
  border-radius: 4px;
  color: white;
  cursor: pointer;
  transition: background 0.15s ease;
}

.pdf-btn:hover:not(:disabled) {
  background: #6b7280;
}

.pdf-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.pdf-btn svg {
  width: 16px;
  height: 16px;
}

.pdf-page-info {
  color: white;
  font-size: 13px;
  min-width: 60px;
  text-align: center;
}

.pdf-content {
  flex: 1;
  overflow: auto;
  display: flex;
  justify-content: center;
  padding: 16px;
}

.pdf-content canvas {
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.3);
}

.pdf-loading {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  color: white;
  font-size: 14px;
}

.pdf-error {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  color: #f87171;
  font-size: 14px;
}

.download-link {
  padding: 8px 16px;
  background: #166534;
  color: white;
  border-radius: 6px;
  text-decoration: none;
  font-size: 13px;
  transition: background 0.15s ease;
}

.download-link:hover {
  background: #15803d;
}
</style>
