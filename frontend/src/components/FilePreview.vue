<template>
  <Teleport to="body">
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
            <vue-office-pptx :src="pptxUrl" @rendered="onPptxRendered" @error="handlePptxError" />
          </div>
          <div v-else-if="isDocx" class="preview-docx">
            <vue-office-docx :src="docxUrl" @error="handleDocxError" />
          </div>
          <div v-else-if="isExcel" class="preview-excel">
            <vue-office-excel :src="excelUrl" @error="handleExcelError" />
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
  </Teleport>
</template>

<script setup>
import { API_BASE_URL } from '../config.js'
import { ref, computed, watch } from 'vue'
import { marked } from 'marked'
import { setupMarkedExtensions, normalizeMathDelimiters } from '../markdownSetup.js'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'

// 注册 KaTeX 数学公式 + emoji 短代码扩展（幂等，仅执行一次）
setupMarkedExtensions()
import VueOfficeDocx from '@vue-office/docx'
import VueOfficeExcel from '@vue-office/excel'
import VueOfficePptx from '@vue-office/pptx'
import '@vue-office/docx/lib/index.css'
import '@vue-office/excel/lib/index.css'
import { getStoredToken } from '../api/auth.js'

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

const props = defineProps({
  filename: {
    type: String,
    default: ''
  },
  filePath: {
    type: String,
    default: ''
  },
  sessionId: {
    type: String,
    default: null
  },
  taskId: {
    type: String,
    default: null
  },
  visible: {
    type: Boolean,
    default: false
  }
})

const emit = defineEmits(['close'])

const loading = ref(false)
const error = ref('')
const textContent = ref('')
const previewUrl = ref('')
const docxUrl = ref(null)
const excelUrl = ref(null)
const pptxUrl = ref(null)
const htmlUrl = ref('')

const imageExts = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg', '.ico']
const textExts = ['.txt', '.json', '.xml', '.csv', '.js', '.ts', '.vue', '.py', '.java', '.go', '.rs', '.c', '.cpp', '.h', '.hpp', '.sh', '.bat', '.css', '.scss', '.less', '.sql', '.yaml', '.yml', '.toml', '.ini', '.cfg', '.conf', '.env', '.log', '.md', '.jsx', '.tsx', '.rb', '.php', '.swift', '.kt', '.scala', '.lua', '.pl', '.r', '.dart', '.ex', '.exs', '.erl', '.hs', '.ml', '.jl', '.tf', '.proto', '.graphql', '.makefile', '.cmake', '.dockerfile', '.gitignore', '.properties', '.gradle']

// 安全获取文件扩展名（无扩展名时返回空字符串）
function getExt(name) {
  if (!name) return ''
  const parts = String(name).split('.')
  if (parts.length < 2) return ''
  return parts.pop().toLowerCase()
}

const isImage = computed(() => {
  const ext = getExt(props.filename)
  return ext && imageExts.includes('.' + ext)
})

const isPdf = computed(() => getExt(props.filename) === 'pdf')

const isPptx = computed(() => getExt(props.filename) === 'pptx')

const isDocx = computed(() => getExt(props.filename) === 'docx')

const isExcel = computed(() => ['xlsx', 'xls'].includes(getExt(props.filename)))

const isMarkdown = computed(() => getExt(props.filename) === 'md')

const isHtml = computed(() => ['html', 'htm'].includes(getExt(props.filename)))

const isCsv = computed(() => getExt(props.filename) === 'csv')

const isCode = computed(() => {
  const ext = getExt(props.filename)
  const codeExts = ['js', 'ts', 'vue', 'py', 'java', 'go', 'rs', 'c', 'cpp', 'h', 'hpp', 'sh', 'bat', 'css', 'scss', 'less', 'sql', 'html', 'htm', 'xml', 'json', 'yaml', 'yml', 'toml', 'jsx', 'tsx', 'rb', 'php', 'swift', 'kt', 'lua', 'pl', 'r', 'dart', 'tf', 'proto', 'graphql']
  return codeExts.includes(ext)
})

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
  dart: 'dart', tf: 'hcl', proto: 'protobuf', graphql: 'graphql',
}

const highlightedCode = computed(() => {
  if (!textContent.value) return ''
  const ext = getExt(props.filename)
  const lang = codeLangMap[ext]

  // 如果是代码文件且有对应语言，使用语法高亮
  if (isCode.value && lang && hljs.getLanguage(lang)) {
    try {
      return hljs.highlight(textContent.value, { language: lang }).value
    } catch (e) {
      console.warn('[FilePreview] 语法高亮失败:', ext, e)
    }
  }

  // JSON 特殊处理
  if (ext === 'json') {
    try {
      const parsed = JSON.parse(textContent.value)
      return hljs.highlight(JSON.stringify(parsed, null, 2), { language: 'json' }).value
    } catch (e) {
      // JSON 解析失败，按普通文本处理
    }
  }

  // 普通文本，转义 HTML
  return escapeHtml(textContent.value)
})

const lineCount = computed(() => {
  if (!textContent.value) return 0
  return textContent.value.split('\n').length
})

const csvData = computed(() => {
  if (!textContent.value) return { headers: [], rows: [] }
  const lines = textContent.value.split('\n').filter(l => l.trim())
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
})

function escapeHtml(text) {
  const div = document.createElement('div')
  div.textContent = text
  return div.innerHTML
}

// 预览/下载基础 URL：定时任务工作目录走独立端点，否则走会话文件端点
const previewBaseUrl = computed(() => props.taskId
  ? `${API_BASE_URL}/api/scheduled-tasks/${props.taskId}/workspace/file`
  : `${API_BASE_URL}/api/files/preview`)

function handleDownload() {
  const token = getStoredToken()
  const params = new URLSearchParams()
  params.set('file_path', props.filePath)
  if (props.sessionId) params.set('session_id', props.sessionId)
  if (token) params.set('token', token)
  params.set('download', 'true')
  const url = `${previewBaseUrl.value}?${params.toString()}`
  const link = document.createElement('a')
  link.href = url
  link.download = props.filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

const renderedMarkdown = computed(() => {
  if (!textContent.value) return ''
  return marked.parse(normalizeMathDelimiters(textContent.value))
})

const isText = computed(() => {
  const ext = getExt(props.filename)
  return ext && textExts.includes('.' + ext)
})

watch(() => props.visible, async (newVal) => {
  if (newVal && props.filename) {
    await loadPreview()
  }
})

async function loadPreview() {
  loading.value = true
  error.value = ''
  textContent.value = ''
  docxUrl.value = null
  excelUrl.value = null
  pptxUrl.value = null
  if (htmlUrl.value) {
    URL.revokeObjectURL(htmlUrl.value)
    htmlUrl.value = ''
  }

  const ts = new Date().toISOString()
  // 统一打印预览请求日志：文件名、路径、会话ID
  console.log(
    `[${ts}] [FilePreview] 预览请求 | 文件名: ${props.filename} | 路径: ${props.filePath} | 会话: ${props.sessionId || '无'}`
  )

  // 校验必要参数
  if (!props.filePath) {
    error.value = '文件路径为空，无法预览'
    console.error(
      `[${ts}] [FilePreview] loadPreview 失败: filePath 为空`,
      { filename: props.filename, sessionId: props.sessionId }
    )
    loading.value = false
    return
  }

  const token = getStoredToken()

  // 构建预览 URL：定时任务工作目录走独立端点，否则走会话文件端点
  const params = new URLSearchParams()
  params.set('file_path', props.filePath)
  if (props.sessionId) params.set('session_id', props.sessionId)
  if (token) params.set('token', token)
  previewUrl.value = `${previewBaseUrl.value}?${params.toString()}`
  console.log(
    `[${ts}] [FilePreview] 加载预览 | 文件: ${props.filename} | 类型: ${getExt(props.filename) || '无扩展名'} | URL: ${previewUrl.value}`
  )

  const ext = getExt(props.filename)

  // 构建 auth headers
  const headers = token ? { 'Authorization': `Bearer ${token}` } : {}

  try {
    if (isPdf.value) {
      console.log('[FilePreview] PDF 预览')
    } else if (isPptx.value) {
      console.log('[FilePreview] PPTX 预览')
      const response = await fetch(previewUrl.value, { headers })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const arrayBuffer = await response.arrayBuffer()
      // 直接传 ArrayBuffer，避免 Blob URL 在 @vue-office/pptx 下的解析问题
      pptxUrl.value = arrayBuffer
    } else if (isDocx.value) {
      console.log('[FilePreview] DOCX 预览')
      const response = await fetch(previewUrl.value, { headers })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const arrayBuffer = await response.arrayBuffer()
      // 直接传 ArrayBuffer，避免 Blob URL 解析问题（与 excel/pptx 一致）
      docxUrl.value = arrayBuffer
    } else if (isExcel.value) {
      console.log('[FilePreview] Excel 预览')
      const response = await fetch(previewUrl.value, { headers })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const arrayBuffer = await response.arrayBuffer()
      // @vue-office/excel 直接传 ArrayBuffer，避免 Blob URL 解析问题
      excelUrl.value = arrayBuffer
    } else if (isHtml.value) {
      console.log('[FilePreview] HTML 预览')
      const response = await fetch(previewUrl.value, { headers })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const htmlText = await response.text()
      const blob = new Blob([htmlText], { type: 'text/html; charset=utf-8' })
      htmlUrl.value = URL.createObjectURL(blob)
    } else if (isMarkdown.value || isText.value || isCsv.value) {
      const response = await fetch(previewUrl.value, { headers })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      textContent.value = await response.text()
      if (textContent.value.length > 50000) {
        textContent.value = textContent.value.substring(0, 50000) + '\n\n... (内容过长已截断)'
      }
      console.log('[FilePreview] 文本预览加载完成, 长度:', textContent.value.length)
    } else {
      console.log('[FilePreview] 不支持预览的文件类型:', ext || '未知')
    }
  } catch (e) {
    console.error('[FilePreview] 预览加载失败:', e, { filename: props.filename, filePath: props.filePath })
    error.value = `加载失败: ${e.message}`
  }

  loading.value = false
}

function onPptxRendered() {
  console.log('[FilePreview] PPTX 渲染完成')
}

function handlePptxError(e) {
  console.error('[FilePreview] PPTX error:', e)
  error.value = 'PPTX 预览加载失败，请尝试下载后查看'
}

function handleDocxError(e) {
  console.error('DOCX error:', e)
  error.value = 'DOCX 预览加载失败'
}

function handleExcelError(e) {
  console.error('Excel error:', e)
  error.value = 'Excel 预览加载失败'
}

function handleClose() {
  // docxUrl/pptxUrl/excelUrl 现为 ArrayBuffer，无需 revoke；仅 htmlUrl 是 Blob URL
  if (htmlUrl.value) URL.revokeObjectURL(htmlUrl.value)
  emit('close')
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
  overflow: auto;
  background: #f5f5f5;
}

.preview-pptx :deep(.vue-office-pptx) {
  width: 100%;
  /* 不强制 height，让幻灯片按自然高度堆叠，容器滚动 */
}

.preview-docx {
  width: 100%;
  height: 100%;
  overflow: auto;
}

.preview-docx :deep(.docx-preview) {
  width: 100%;
  height: 100%;
}

.preview-excel {
  width: 100%;
  height: 100%;
  min-height: 400px;
  overflow: auto;
}

.preview-excel :deep(.excel-preview),
.preview-excel :deep(.vue-office-excel),
.preview-excel :deep(.x-spreadsheet) {
  width: 100%;
  min-height: 400px;
}

.preview-text {
  width: 100%;
  height: 100%;
  overflow: auto;
  background: #ffffff;
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
  color: #94a3b8;
  user-select: none;
  border-right: 1px solid #e2e8f0;
  background: #f8fafc;
  flex-shrink: 0;
}

.preview-text .line-numbers span {
  display: block;
  min-height: 1.6em;
}

.preview-text pre {
  margin: 0;
  padding: 16px;
  color: #24292e;
  white-space: pre-wrap;
  word-break: break-all;
  flex: 1;
  overflow-x: auto;
  background: #ffffff;
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

:deep(.markdown-body) {
  max-width: 900px;
  margin: 0 auto;
  color: #24292f;
  line-height: 1.6;
}

:deep(.markdown-body h1),
:deep(.markdown-body h2),
:deep(.markdown-body h3),
:deep(.markdown-body h4),
:deep(.markdown-body h5),
:deep(.markdown-body h6) {
  margin-top: 24px;
  margin-bottom: 16px;
  font-weight: 600;
  line-height: 1.25;
  border-bottom: 1px solid #e5e7eb;
  padding-bottom: 8px;
}

:deep(.markdown-body h1) { font-size: 2em; }
:deep(.markdown-body h2) { font-size: 1.5em; }
:deep(.markdown-body h3) { font-size: 1.25em; }
:deep(.markdown-body h4) { font-size: 1em; }

:deep(.markdown-body p) {
  margin-bottom: 16px;
}

:deep(.markdown-body code) {
  padding: 0.2em 0.4em;
  margin: 0;
  font-size: 85%;
  background-color: #f6f8fa;
  border-radius: 6px;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
}

:deep(.markdown-body pre) {
  padding: 16px;
  overflow: auto;
  font-size: 85%;
  line-height: 1.45;
  background-color: #f6f8fa;
  border-radius: 6px;
  margin-bottom: 16px;
  border: 1px solid #e1e4e8;
}

:deep(.markdown-body pre code) {
  padding: 0;
  margin: 0;
  background-color: transparent;
  border-radius: 0;
  white-space: pre;
  display: block;
}

:deep(.markdown-body ul),
:deep(.markdown-body ol) {
  padding-left: 2em;
  margin-bottom: 16px;
}

:deep(.markdown-body li) {
  margin-bottom: 4px;
}

:deep(.markdown-body blockquote) {
  padding: 0 1em;
  color: #6a737d;
  border-left: 0.25em solid #d0d7de;
  margin: 0 0 16px 0;
}

:deep(.markdown-body table) {
  border-collapse: collapse;
  width: 100%;
  margin-bottom: 16px;
  border-spacing: 0;
}

:deep(.markdown-body thead) {
  display: table-header-group;
  vertical-align: middle;
  border-color: inherit;
}

:deep(.markdown-body tbody) {
  display: table-row-group;
  vertical-align: middle;
  border-color: inherit;
}

:deep(.markdown-body tr) {
  display: table-row;
  vertical-align: inherit;
  border-color: inherit;
}

:deep(.markdown-body tr:nth-child(2n)) {
  background-color: #f6f8fa;
}

:deep(.markdown-body table th),
:deep(.markdown-body table td) {
  padding: 6px 13px;
  border: 1px solid #d0d7de;
  display: table-cell;
  vertical-align: middle;
}

:deep(.markdown-body table th) {
  font-weight: 600;
  background-color: #f6f8fa;
}

:deep(.markdown-body table td) {
  color: #24292f;
}

:deep(.markdown-body a) {
  color: #0366d6;
  text-decoration: none;
}

:deep(.markdown-body a:hover) {
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
