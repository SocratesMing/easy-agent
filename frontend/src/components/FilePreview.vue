<template>
  <Teleport to="body">
    <div v-if="visible" class="preview-overlay" @click.self="handleClose">
      <div class="preview-dialog">
        <div class="preview-header">
          <h3 class="preview-title">{{ filename }}</h3>
          <button class="close-btn" @click="handleClose">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
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
            <vue-office-pptx :src="pptxUrl" @error="handlePptxError" />
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
          <div v-else-if="isMarkdown" class="preview-markdown">
            <div class="markdown-body" v-html="renderedMarkdown"></div>
          </div>
          <div v-else-if="isText" class="preview-text">
            <pre>{{ textContent }}</pre>
          </div>
          <div v-else class="preview-unsupported">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
              <line x1="12" y1="18" x2="12" y2="12"></line>
              <line x1="9" y1="15" x2="15" y2="15"></line>
            </svg>
            <p>该文件类型暂不支持预览</p>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<script setup>
import { ref, computed, watch } from 'vue'
import { marked } from 'marked'
import hljs from 'highlight.js'
import 'highlight.js/styles/github.css'
import VueOfficeDocx from '@vue-office/docx'
import VueOfficeExcel from '@vue-office/excel'
import VueOfficePptx from '@vue-office/pptx'
import '@vue-office/docx/lib/index.css'
import '@vue-office/excel/lib/index.css'

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
const docxUrl = ref('')
const excelUrl = ref('')
const pptxUrl = ref('')

const imageExts = ['.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp', '.svg', '.ico']
const textExts = ['.txt', '.json', '.xml', '.csv', '.js', '.ts', '.vue', '.py', '.java', '.go', '.rs', '.c', '.cpp', '.h', '.sh', '.bat', '.css', '.sql', '.html', '.htm']

const isImage = computed(() => {
  const ext = props.filename.split('.').pop().toLowerCase()
  return imageExts.includes('.' + ext)
})

const isPdf = computed(() => {
  const ext = props.filename.split('.').pop().toLowerCase()
  return ext === 'pdf'
})

const isPptx = computed(() => {
  const ext = props.filename.split('.').pop().toLowerCase()
  return ext === 'pptx'
})

const isDocx = computed(() => {
  const ext = props.filename.split('.').pop().toLowerCase()
  return ext === 'docx'
})

const isExcel = computed(() => {
  const ext = props.filename.split('.').pop().toLowerCase()
  return ['xlsx', 'xls'].includes(ext)
})

const isMarkdown = computed(() => {
  const ext = props.filename.split('.').pop().toLowerCase()
  return ext === 'md'
})

const renderedMarkdown = computed(() => {
  if (!textContent.value) return ''
  return marked.parse(textContent.value)
})

const isText = computed(() => {
  const ext = props.filename.split('.').pop().toLowerCase()
  return textExts.includes('.' + ext)
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
  docxUrl.value = ''
  excelUrl.value = ''
  pptxUrl.value = ''

  const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'
  const encodedFilename = encodeURIComponent(props.filename)
  previewUrl.value = `${API_BASE_URL}/api/sessions/files/${encodedFilename}/preview`
  console.log('[FilePreview] 加载预览:', props.filename, previewUrl.value)

  const ext = props.filename.split('.').pop().toLowerCase()

  try {
    if (isPdf.value) {
      console.log('[FilePreview] PDF 预览')
    } else if (isPptx.value) {
      console.log('[FilePreview] PPTX 预览')
      const response = await fetch(previewUrl.value)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const arrayBuffer = await response.arrayBuffer()
      const blob = new Blob([arrayBuffer], { type: 'application/vnd.openxmlformats-officedocument.presentationml.presentation' })
      pptxUrl.value = URL.createObjectURL(blob)
    } else if (isDocx.value) {
      console.log('[FilePreview] DOCX 预览')
      const response = await fetch(previewUrl.value)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const arrayBuffer = await response.arrayBuffer()
      const blob = new Blob([arrayBuffer], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' })
      docxUrl.value = URL.createObjectURL(blob)
    } else if (isExcel.value) {
      console.log('[FilePreview] Excel 预览')
      const response = await fetch(previewUrl.value)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const arrayBuffer = await response.arrayBuffer()
      const blob = new Blob([arrayBuffer], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' })
      excelUrl.value = URL.createObjectURL(blob)
    } else if (isMarkdown.value || isText.value) {
      const response = await fetch(previewUrl.value)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      textContent.value = await response.text()
      if (textContent.value.length > 50000) {
        textContent.value = textContent.value.substring(0, 50000) + '\n\n... (内容过长已截断)'
      }
    }
  } catch (e) {
    error.value = `加载失败: ${e.message}`
  }

  loading.value = false
}

function handlePptxError(e) {
  console.error('PPTX error:', e)
  error.value = 'PPTX 预览加载失败'
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
  if (docxUrl.value) URL.revokeObjectURL(docxUrl.value)
  if (excelUrl.value) URL.revokeObjectURL(excelUrl.value)
  if (pptxUrl.value) URL.revokeObjectURL(pptxUrl.value)
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

.preview-pptx {
  width: 100%;
  height: 100%;
}

.preview-pptx :deep(.vue-office-pptx) {
  width: 100%;
  height: 100%;
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
  overflow: auto;
}

.preview-excel :deep(.excel-preview) {
  width: 100%;
  height: 100%;
}

.preview-text {
  width: 100%;
  height: 100%;
  overflow: auto;
  padding: 20px;
  background: #f8fafc;
}

.preview-text pre {
  margin: 0;
  font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
  font-size: 13px;
  line-height: 1.6;
  color: #334155;
  white-space: pre-wrap;
  word-break: break-all;
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
