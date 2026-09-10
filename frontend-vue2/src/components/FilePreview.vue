<template>
    <div v-if="visible" class="preview-overlay" @click.self="handleClose">
      <div class="preview-dialog">
        <div class="preview-header">
          <h3 class="preview-title">{{ filename }}</h3>
          <div class="header-actions">
            <button class="download-btn" @click="handleDownload" title="下载文件">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
              </svg>
            </button>
            <button class="close-btn" @click="handleClose">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
              </svg>
            </button>
          </div>
        </div>
        <div class="preview-content">
          <div v-if="loading" class="preview-loading">
            <div class="spinner"></div>
            <span>加载中...</span>
          </div>
          <div v-else-if="error" class="preview-error">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <circle cx="12" cy="12" r="10"></circle>
              <line x1="12" y1="8" x2="12" y2="12"></line>
              <line x1="12" y1="16" x2="12.01" y2="16"></line>
            </svg>
            <p>{{ error }}</p>
          </div>
          <div v-else-if="isPdf" class="preview-pdf">
            <iframe :src="previewUrl" class="pdf-iframe"></iframe>
          </div>
          <div v-else-if="isPptx" class="preview-pptx">
            <iframe v-if="pptxPdfUrl" :src="pptxPdfUrl" class="pptx-iframe" title="PPTX 预览"></iframe>
          </div>
          <div v-else-if="isDocx" class="preview-docx">
            <DocxPreview v-if="docxUrl" :file-url="docxUrl" />
          </div>
          <div v-else-if="isExcel" class="preview-excel">
            <ExcelPreview v-if="excelUrl" :file-url="excelUrl" />
          </div>
          <div v-else-if="isImage" class="preview-image">
            <img :src="previewUrl" :alt="filename" />
          </div>
          <div v-else-if="isCsv" class="preview-csv">
            <div class="csv-table-wrapper">
              <table class="csv-table">
                <thead>
                  <tr>
                    <th class="row-num">#</th>
                    <th v-for="(header, hi) in csvData.headers" :key="hi">{{ header }}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(row, ri) in csvData.rows" :key="ri">
                    <td class="row-num">{{ ri + 1 }}</td>
                    <td v-for="(cell, ci) in row" :key="ci">{{ cell }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div v-else-if="isMarkdown" class="preview-markdown">
            <div class="markdown-body" v-html="renderedMarkdown"></div>
          </div>
          <div v-else-if="isHtml" class="preview-html">
            <iframe :src="htmlUrl" class="html-iframe" sandbox="allow-same-origin"></iframe>
          </div>
          <div v-else-if="isText" class="preview-text">
            <div class="code-block">
              <div class="line-numbers">
                <span v-for="n in lineCount" :key="n">{{ n }}</span>
              </div>
              <pre v-html="highlightedCode"></pre>
            </div>
          </div>
          <div v-else class="preview-unsupported">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
              <line x1="12" y1="18" x2="12" y2="12"></line>
              <line x1="9" y1="15" x2="15" y2="15"></line>
            </svg>
            <p>该文件类型暂不支持在线预览</p>
            <button class="download-fallback-btn" @click="handleDownload">下载文件</button>
          </div>
        </div>
      </div>
    </div>
</template>

<script>
import { API_BASE_URL } from '../config.js'
import { marked } from 'marked'
import { setupMarkedExtensions, normalizeMathDelimiters } from '../markdownSetup.js'
import hljs from 'highlight.js'
// 代码展示统一亮黑底色，配套使用 highlight.js 的深色主题
import 'highlight.js/styles/github-dark.css'
import DocxPreview from './DocxPreview.vue'
import ExcelPreview from './ExcelPreview.vue'
import { getStoredToken } from '../api/auth.js'
import { requestBlob, requestArrayBuffer, requestText } from '@/utils/request'

// 注册 KaTeX 数学公式 + emoji 短代码扩展（幂等，模块加载时仅执行一次）
setupMarkedExtensions()
marked.setOptions({
  breaks: true,
  gfm: true,
  headerIds: false,
  highlight: function(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      try {
        return hljs.highlight(code, { language: lang }).value
      } catch (__) {}
    }
    return hljs.highlightAuto(code).value
  }
})

// 模块级常量（无需响应式）
const imageExts = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg', '.ico']
const textExts = ['.txt', '.json', '.xml', '.csv', '.js', '.ts', '.vue', '.py', '.java', '.go', '.rs', '.c', '.cpp', '.h', '.hpp', '.sh', '.bat', '.css', '.scss', '.less', '.sql', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.env', '.log', '.md', '.jsx', '.tsx', '.rb', '.php', '.swift', '.kt', '.scala', '.lua', '.pl', '.r', '.dart', '.ex', '.exs', '.erl', '.hs', '.ml', '.jl', '.tf', '.proto', '.graphql', '.makefile', '.cmake', '.dockerfile', '.gitignore', '.properties', '.gradle']

// 代码语言映射
const codeLangMap = {
  js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript',
  vue: 'xml', html: 'xml', htm: 'xml', xml: 'xml',
  py: 'python', java: 'java', go: 'go', rs: 'rust',
  c: 'c', cpp: 'cpp', h: 'c', hpp: 'cpp',
  sh: 'bash', bat: 'bat',
  css: 'css', scss: 'scss', less: 'less',
  sql: 'sql', json: 'json', yaml: 'yaml', yml: 'yaml',
  toml: 'ini', rb: 'ruby', php: 'php', swift: 'swift',
  kt: 'kotlin', lua: 'lua', pl: 'perl', r: 'r',
  dart: 'dart', tf: 'hcl', proto: 'protobuf', graphql: 'graphql'
}

// 安全获取文件扩展名（无扩展名时返回空字符串）
function getExt(name) {
  if (!name) return ''
  const parts = String(name).split('.')
  if (parts.length < 2) return ''
  return parts.pop().toLowerCase()
}

// 普通文本转义 HTML，防止将原文误当作标签渲染
function escapeHtml(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

export default {
  components: { DocxPreview, ExcelPreview },
  props: {
    filename: { type: String, default: '' },
    filePath: { type: String, default: '' },
    sessionId: { type: String, default: null },
    taskId: { type: String, default: null },
    visible: { type: Boolean, default: false }
  },
  data() {
    return {
      loading: false,
      error: '',
      textContent: '',
      previewUrl: '',
      docxUrl: '',
      excelUrl: '',
      // PPTX：经后端 LibreOffice 转为 PDF 后以 iframe 预览
      pptxPdfUrl: '',
      htmlUrl: ''
    }
  },
  computed: {
    isImage() {
      const ext = getExt(this.filename)
      return ext && imageExts.includes('.' + ext)
    },
    isPdf() {
      return getExt(this.filename) === 'pdf'
    },
    isPptx() {
      return getExt(this.filename) === 'pptx'
    },
    isDocx() {
      return getExt(this.filename) === 'docx'
    },
    isExcel() {
      return ['xlsx', 'xls'].includes(getExt(this.filename))
    },
    isMarkdown() {
      return getExt(this.filename) === 'md'
    },
    isHtml() {
      return ['html', 'htm'].includes(getExt(this.filename))
    },
    isCsv() {
      return getExt(this.filename) === 'csv'
    },
    isCode() {
      const ext = getExt(this.filename)
      const codeExts = ['js', 'ts', 'vue', 'py', 'java', 'go', 'rs', 'c', 'cpp', 'h', 'hpp', 'sh', 'bat', 'css', 'scss', 'less', 'sql', 'html', 'htm', 'xml', 'json', 'yaml', 'yml', 'toml', 'jsx', 'tsx', 'rb', 'php', 'swift', 'kt', 'lua', 'pl', 'r', 'dart', 'tf', 'proto', 'graphql']
      return codeExts.includes(ext)
    },
    isText() {
      const ext = getExt(this.filename)
      return ext && textExts.includes('.' + ext)
    },
    // 预览/下载基础 URL：定时任务工作目录走独立端点，否则走会话文件端点
    previewBaseUrl() {
      return this.taskId
        ? `${API_BASE_URL}/agent/scheduled-tasks/${this.taskId}/workspace/file`
        : `${API_BASE_URL}/agent/files/preview`
    },
    highlightedCode() {
      if (!this.textContent) return ''
      const ext = getExt(this.filename)
      const lang = codeLangMap[ext]

      // 如果是代码文件且有对应语言，使用语法高亮
      if (this.isCode && lang && hljs.getLanguage(lang)) {
        try {
          return hljs.highlight(this.textContent, { language: lang }).value
        } catch (e) {
          console.warn('[FilePreview] 语法高亮失败:', ext, e)
        }
      }

      // JSON 特殊处理
      if (ext === 'json') {
        try {
          const parsed = JSON.parse(this.textContent)
          return hljs.highlight(JSON.stringify(parsed, null, 2), { language: 'json' }).value
        } catch (e) {
          // JSON 解析失败，按普通文本处理
        }
      }

      // 普通文本，转义 HTML
      return escapeHtml(this.textContent)
    },
    lineCount() {
      if (!this.textContent) return 0
      return this.textContent.split('\n').length
    },
    csvData() {
      if (!this.textContent) return { headers: [], rows: [] }
      const lines = this.textContent.split('\n').filter(l => l.trim())
      if (lines.length === 0) return { headers: [], rows: [] }

      const parseLine = (line) => {
        const result = []
        let current = ''
        let inQuotes = false
        for (let i = 0; i < line.length; i++) {
          const char = line[i]
          if (char === '"' && line[i + 1] === '"') {
            current += '"'
            i++
          } else if (char === '"') {
            inQuotes = !inQuotes
          } else if (char === ',' && !inQuotes) {
            result.push(current)
            current = ''
          } else {
            current += char
          }
        }
        result.push(current)
        return result
      }

      const headers = parseLine(lines[0])
      const rows = lines.slice(1, 1000).map(parseLine) // 限制最多 1000 行
      return { headers, rows }
    },
    renderedMarkdown() {
      if (!this.textContent) return ''
      return marked.parse(normalizeMathDelimiters(this.textContent))
    }
  },
  watch: {
    visible(newVal) {
      if (newVal && this.filename) {
        this.loadPreview()
      }
    }
  },
  methods: {
    handleDownload() {
      const token = getStoredToken()
      const params = new URLSearchParams()
      params.set('file_path', this.filePath)
      if (this.sessionId) params.set('session_id', this.sessionId)
      if (token) params.set('token', token)
      params.set('download', 'true')
      const url = `${this.previewBaseUrl}?${params.toString()}`
      const link = document.createElement('a')
      link.href = url
      link.download = this.filename
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
    },
    handleClose() {
      // pptx/docx/excel/html 均为 Blob URL，需释放
      if (this.docxUrl) URL.revokeObjectURL(this.docxUrl)
      if (this.excelUrl) URL.revokeObjectURL(this.excelUrl)
      if (this.pptxPdfUrl) URL.revokeObjectURL(this.pptxPdfUrl)
      if (this.htmlUrl) URL.revokeObjectURL(this.htmlUrl)
      this.$emit('close')
    },
    async loadPreview() {
      this.loading = true
      this.error = ''
      this.textContent = ''
      if (this.docxUrl) {
        URL.revokeObjectURL(this.docxUrl)
      }
      if (this.excelUrl) {
        URL.revokeObjectURL(this.excelUrl)
      }
      this.docxUrl = ''
      this.excelUrl = ''
      if (this.pptxPdfUrl) {
        URL.revokeObjectURL(this.pptxPdfUrl)
      }
      this.pptxPdfUrl = ''
      if (this.htmlUrl) {
        URL.revokeObjectURL(this.htmlUrl)
        this.htmlUrl = ''
      }

      const ts = new Date().toISOString()
      // 统一打印预览请求日志：文件名、路径、会话ID
      console.log(
        `[${ts}] [FilePreview] 预览请求 | 文件名: ${this.filename} | 路径: ${this.filePath} | 会话: ${this.sessionId || '无'}`
      )

      // 校验必要参数
      if (!this.filePath) {
        this.error = '文件路径为空，无法预览'
        console.error(
          `[${ts}] [FilePreview] loadPreview 失败: filePath 为空`,
          { filename: this.filename, sessionId: this.sessionId }
        )
        this.loading = false
        return
      }

      const token = getStoredToken()

      // 构建预览 URL：定时任务工作目录走独立端点，否则走会话文件端点
      const params = new URLSearchParams()
      params.set('file_path', this.filePath)
      if (this.sessionId) params.set('session_id', this.sessionId)
      if (token) params.set('token', token)
      this.previewUrl = `${this.previewBaseUrl}?${params.toString()}`
      console.log(
        `[${ts}] [FilePreview] 加载预览 | 文件: ${this.filename} | 类型: ${getExt(this.filename) || '无扩展名'} | URL: ${this.previewUrl}`
      )

      const ext = getExt(this.filename)

      // 构建 auth headers
      const headers = token ? { 'Authorization': `Bearer ${token}` } : {}

      try {
        if (this.isPdf) {
          console.log('[FilePreview] PDF 预览')
        } else if (this.isPptx) {
          console.log('[FilePreview] PPTX 预览 (LibreOffice → PDF)')
          // 后端用 LibreOffice 把 pptx 转成 PDF，返回 PDF 流，由浏览器内置查看器渲染
          const pdfParams = new URLSearchParams(params)
          pdfParams.set('target', 'pdf')
          const pdfUrl = `${this.previewBaseUrl}?${pdfParams.toString()}`
          const blob = await requestBlob({ url: pdfUrl, headers })
          this.pptxPdfUrl = URL.createObjectURL(blob)
        } else if (this.isDocx) {
          console.log('[FilePreview] DOCX 预览')
          const arrayBuffer = await requestArrayBuffer({ url: this.previewUrl, headers })
          this.docxUrl = URL.createObjectURL(
            new Blob([arrayBuffer], {
              type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            })
          )
        } else if (this.isExcel) {
          console.log('[FilePreview] Excel 预览')
          const arrayBuffer = await requestArrayBuffer({ url: this.previewUrl, headers })
          this.excelUrl = URL.createObjectURL(
            new Blob([arrayBuffer], {
              type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            })
          )
        } else if (this.isHtml) {
          console.log('[FilePreview] HTML 预览')
          const htmlText = await requestText({ url: this.previewUrl, headers })
          const blob = new Blob([htmlText], { type: 'text/html; charset=utf-8' })
          this.htmlUrl = URL.createObjectURL(blob)
        } else if (this.isMarkdown || this.isText || this.isCsv) {
          this.textContent = await requestText({ url: this.previewUrl, headers })
          if (this.textContent.length > 50000) {
            this.textContent = this.textContent.substring(0, 50000) + '\n\n... (内容过长已截断)'
          }
          console.log('[FilePreview] 文本预览加载完成, 长度:', this.textContent.length)
        } else {
          console.log('[FilePreview] 不支持预览的文件类型:', ext || '未知')
        }
      } catch (e) {
        console.error('[FilePreview] 预览加载失败:', e, { filename: this.filename, filePath: this.filePath })
        this.error = `加载失败: ${e.message}`
      }

      this.loading = false
    }
  }
}
</script>

<style scoped>
.preview-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.6);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
}

.preview-dialog {
  width: 90%;
  max-width: 1200px;
  height: 85%;
  background: white;
  border-radius: 16px;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid #e5e7eb;
  background: #f9fafb;
  flex-shrink: 0;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.download-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  color: #6b7280;
  transition: all 0.2s;
}

.download-btn:hover {
  background: #e0f2fe;
  color: #0284c7;
}

.download-btn svg {
  width: 18px;
  height: 18px;
}

.preview-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: #111827;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.close-btn {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  color: #6b7280;
  transition: all 0.2s;
}

.close-btn:hover {
  background: #f3f4f6;
  color: #111827;
}

.close-btn svg {
  width: 20px;
  height: 20px;
}

.preview-content {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.preview-loading,
.preview-error,
.preview-unsupported {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #6b7280;
  gap: 12px;
}

.preview-error {
  color: #ef4444;
}

.preview-unsupported svg {
  width: 64px;
  height: 64px;
  color: #d1d5db;
}

.download-fallback-btn {
  margin-top: 8px;
  padding: 8px 20px;
  background: #3b82f6;
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 14px;
  cursor: pointer;
  transition: background 0.2s;
}

.download-fallback-btn:hover {
  background: #2563eb;
}

.preview-image {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: #f8fafc;
}

.preview-image img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
}

.preview-pdf {
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.pdf-iframe {
  width: 100%;
  height: 100%;
  border: none;
  background: #525659;
}

.preview-html {
  width: 100%;
  height: 100%;
  overflow: hidden;
}

.html-iframe {
  width: 100%;
  height: 100%;
  border: none;
  background: #fff;
}

.preview-pptx {
  width: 100%;
  flex: 1;
  min-height: 0;
  overflow: hidden;
  background: #fff;
}

/* PPTX 经后端 LibreOffice 转为 PDF，由浏览器内置查看器渲染 */
.pptx-iframe {
  width: 100%;
  height: 100%;
  border: none;
  background: #fff;
}

.preview-docx {
  width: 100%;
  height: 100%;
  overflow: auto;
}

.preview-docx ::v-deep .docx-preview {
  width: 100%;
  height: 100%;
}

.preview-excel {
  width: 100%;
  height: 100%;
  min-height: 400px;
  overflow: auto;
}

.preview-excel ::v-deep .excel-preview,
.preview-excel ::v-deep .vue-office-excel,
.preview-excel ::v-deep .x-spreadsheet {
  width: 100%;
  min-height: 400px;
}

/* Excel 兜底：SheetJS 生成的 HTML 表格 */
.excel-fallback {
  height: 100%;
  overflow: auto;
  padding: 16px;
  background: #fff;
}

.excel-sheet {
  margin-bottom: 24px;
}

.excel-sheet-name {
  font-size: 14px;
  font-weight: 600;
  color: #374151;
  margin-bottom: 8px;
  padding-bottom: 4px;
  border-bottom: 2px solid #e5e7eb;
}

.excel-table-wrap {
  overflow: auto;
}

.excel-table-wrap table {
  border-collapse: collapse;
  font-size: 13px;
}

.excel-table-wrap td,
.excel-table-wrap th {
  border: 1px solid #d0d7de;
  padding: 4px 10px;
  min-width: 64px;
  max-width: 480px;
  white-space: normal;
  word-break: break-word;
}

.preview-text {
  width: 100%;
  height: 100%;
  overflow: auto;
  background: #0d1117;
}

.preview-text .code-block {
  display: flex;
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', 'Consolas', monospace;
  font-size: 13px;
  line-height: 1.6;
  min-height: 100%;
}

.preview-text .line-numbers {
  display: flex;
  flex-direction: column;
  padding: 16px 8px 16px 16px;
  text-align: right;
  color: #6e7681;
  user-select: none;
  border-right: 1px solid #21262d;
  background: #161b22;
  flex-shrink: 0;
}

.preview-text .line-numbers span {
  display: block;
  min-height: 1.6em;
}

.preview-text pre {
  margin: 0;
  padding: 16px;
  color: #c9d1d9;
  white-space: pre-wrap;
  word-break: break-all;
  flex: 1;
  overflow-x: auto;
  background: #0d1117;
}

.preview-csv {
  width: 100%;
  height: 100%;
  overflow: auto;
  background: #ffffff;
}

.csv-table-wrapper {
  padding: 16px;
}

.csv-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

.csv-table th,
.csv-table td {
  padding: 6px 12px;
  border: 1px solid #e2e8f0;
  text-align: left;
  white-space: nowrap;
}

.csv-table th {
  background: #f1f5f9;
  font-weight: 600;
  color: #1e293b;
  position: sticky;
  top: 0;
  z-index: 1;
}

.csv-table td {
  color: #334155;
}

.csv-table tbody tr:nth-child(even) {
  background: #f8fafc;
}

.csv-table tbody tr:hover {
  background: #e0f2fe;
}

.csv-table .row-num {
  background: #f1f5f9;
  color: #94a3b8;
  font-size: 11px;
  text-align: right;
  user-select: none;
  position: sticky;
  left: 0;
  z-index: 1;
}

.preview-markdown {
  width: 100%;
  height: 100%;
  overflow: auto;
  padding: 20px;
  background: #ffffff;
}

::v-deep .markdown-body {
  max-width: 900px;
  margin: 0 auto;
  color: #24292f;
  line-height: 1.6;
}

::v-deep .markdown-body h1,
::v-deep .markdown-body h2,
::v-deep .markdown-body h3,
::v-deep .markdown-body h4,
::v-deep .markdown-body h5,
::v-deep .markdown-body h6 {
  margin-top: 24px;
  margin-bottom: 16px;
  font-weight: 600;
  line-height: 1.25;
  border-bottom: 1px solid #e5e7eb;
  padding-bottom: 8px;
}

::v-deep .markdown-body h1 { font-size: 2em; }
::v-deep .markdown-body h2 { font-size: 1.5em; }
::v-deep .markdown-body h3 { font-size: 1.25em; }
::v-deep .markdown-body h4 { font-size: 1em; }

::v-deep .markdown-body p {
  margin-bottom: 16px;
}

/* Markdown 预览里的代码同样使用亮黑底 + github-dark 的 token 配色 */
::v-deep .markdown-body :not(pre) > code {
  padding: 0.2em 0.4em;
  margin: 0;
  font-size: 85%;
  background-color: rgba(13, 17, 23, 0.06);
  border-radius: 6px;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
}

::v-deep .markdown-body pre {
  padding: 16px;
  overflow: auto;
  font-size: 85%;
  line-height: 1.45;
  background-color: #0d1117;
  color: #c9d1d9;
  border-radius: 6px;
  margin-bottom: 16px;
  border: 1px solid #21262d;
}

::v-deep .markdown-body pre code {
  padding: 0;
  margin: 0;
  background-color: transparent;
  color: #c9d1d9;
  border-radius: 0;
  white-space: pre;
  display: block;
}

::v-deep .markdown-body ul,
::v-deep .markdown-body ol {
  padding-left: 2em;
  margin-bottom: 16px;
}

::v-deep .markdown-body li {
  margin-bottom: 4px;
}

::v-deep .markdown-body blockquote {
  padding: 0 1em;
  color: #6a737d;
  border-left: 0.25em solid #d0d7de;
  margin: 0 0 16px 0;
}

::v-deep .markdown-body table {
  border-collapse: collapse;
  width: 100%;
  margin-bottom: 16px;
  border-spacing: 0;
}

::v-deep .markdown-body thead {
  display: table-header-group;
  vertical-align: middle;
  border-color: inherit;
}

::v-deep .markdown-body tbody {
  display: table-row-group;
  vertical-align: middle;
  border-color: inherit;
}

::v-deep .markdown-body tr {
  display: table-row;
  vertical-align: inherit;
  border-color: inherit;
}

::v-deep .markdown-body tr:nth-child(2n) {
  background-color: #f6f8fa;
}

::v-deep .markdown-body table th,
::v-deep .markdown-body table td {
  padding: 6px 13px;
  border: 1px solid #d0d7de;
  display: table-cell;
  vertical-align: middle;
}

::v-deep .markdown-body table th {
  font-weight: 600;
  background-color: #f6f8fa;
}

::v-deep .markdown-body table td {
  color: #24292f;
}

::v-deep .markdown-body a {
  color: #0366d6;
  text-decoration: none;
}

::v-deep .markdown-body a:hover {
  text-decoration: underline;
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid #e5e7eb;
  border-top-color: #3b82f6;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
