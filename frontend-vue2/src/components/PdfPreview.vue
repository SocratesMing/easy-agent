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

<script>
import * as pdfjsLib from 'pdfjs-dist/legacy/build/pdf.mjs'
import pdfjsWorkerUrl from 'pdfjs-dist/legacy/build/pdf.worker.min.mjs'
import { requestArrayBuffer } from '../api/request.js'

// webpack5 下通过 vue.config.js 的 asset rule 把 worker 输出为可访问的静态资源 URL
pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorkerUrl

export default {
  name: 'PdfPreview',
  props: {
    fileUrl: {
      type: String,
      default: '',
    },
  },
  data() {
    return {
      currentPage: 1,
      totalPages: 0,
      scale: 1.2,
      loading: true,
      errorMsg: '',
      pdfDoc: null,
    }
  },
  methods: {
    async loadPdf() {
      if (!this.fileUrl) return

      this.loading = true
      this.errorMsg = ''

      try {
        // 统一走 axios 封装（自动带鉴权头），4xx/5xx 由拦截器直接 reject
        const arrayBuffer = await requestArrayBuffer({ url: this.fileUrl })

        if (arrayBuffer.byteLength === 0) {
          throw new Error('文件内容为空')
        }

        this.pdfDoc = await pdfjsLib.getDocument({
          data: arrayBuffer,
          cMapUrl: 'https://unpkg.com/pdfjs-dist@4.10.38/cmaps/',
          cMapPacked: true,
          standardFontDataUrl:
            'https://unpkg.com/pdfjs-dist@4.10.38/standard_fonts/',
          useSystemFonts: true,
          disableFontFace: false,
          isEvalSupported: false,
          useWorkerFetch: false,
        }).promise

        this.totalPages = this.pdfDoc.numPages
        this.currentPage = 1

        await this.renderPage()
      } catch (e) {
        console.error('PDF load error:', e)
        this.errorMsg = `PDF 加载失败: ${e.message}`
      } finally {
        this.loading = false
      }
    },
    async renderPage() {
      if (!this.pdfDoc) return
      const canvas = this.$refs.canvasRef
      if (!canvas) return

      try {
        const page = await this.pdfDoc.getPage(this.currentPage)
        const viewport = page.getViewport({ scale: this.scale })

        const context = canvas.getContext('2d')

        canvas.height = viewport.height
        canvas.width = viewport.width

        const renderContext = {
          canvasContext: context,
          viewport: viewport,
          enableWebGL: false,
          renderInteractiveForms: false,
        }

        await page.render(renderContext).promise
      } catch (e) {
        console.error('Render page error:', e)
      }
    },
    prevPage() {
      if (this.currentPage > 1) {
        this.currentPage--
        this.renderPage()
      }
    },
    nextPage() {
      if (this.currentPage < this.totalPages) {
        this.currentPage++
        this.renderPage()
      }
    },
    zoomIn() {
      this.scale = Math.min(this.scale + 0.25, 3)
      this.renderPage()
    },
    zoomOut() {
      this.scale = Math.max(this.scale - 0.25, 0.5)
      this.renderPage()
    },
  },
  mounted() {
    this.loadPdf()
  },
  watch: {
    fileUrl() {
      if (this.pdfDoc) {
        this.pdfDoc.destroy()
        this.pdfDoc = null
      }
      this.currentPage = 1
      this.totalPages = 0
      this.loadPdf()
    },
  },
  beforeDestroy() {
    if (this.pdfDoc) {
      this.pdfDoc.destroy()
      this.pdfDoc = null
    }
  },
}
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
