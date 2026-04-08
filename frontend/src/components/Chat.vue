<template>
  <div class="chat-container">
    <div class="chat-messages" ref="messagesRef">
      <div v-if="messages.length === 0" class="welcome-screen">
        <div class="welcome-icon">
          <EasyLogo :size="64" />
        </div>
        <h2>{{ displayedTitle }}</h2>
        <p>{{ displayedSubtitle }}</p>
      </div>
      
      <div
      v-for="(msg, index) in messages"
      :key="msg.id"
      :ref="el => setMessageRef(el, index)"
      class="message-wrapper"
      :class="msg.role"
    >
      <ChatMessage 
        :message="msg" 
        @removeFile="(file) => handleRemoveFile(file, index)"
        @retry="handleRetry"
      />
    </div>
    </div>
    
    <button v-if="canGoToNextUserMessage" @click="goToNextUserMessage" class="scroll-btn next" title="回到下一个用户问题">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    </button>
    <button v-if="canGoToPrevUserMessage" @click="goToPrevUserMessage" class="scroll-btn prev" title="回到上一个用户问题">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="18 15 12 9 6 15"></polyline>
      </svg>
    </button>
    
    <div v-if="hasGeneratedFiles" class="generated-files-float" :class="{ expanded: generatedFilesExpanded }">
      <div v-if="!generatedFilesExpanded" class="generated-files-btn-only" @click="toggleGeneratedFilesPanel" title="查看生成的文件">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
        </svg>
      </div>
      <div v-if="generatedFilesExpanded" class="generated-files-panel" ref="panelRef" :style="{ width: panelWidth + 'vw' }">
        <div class="resize-handle" @mousedown="startResize"></div>
        <div class="panel-toolbar">
          <span class="panel-title">生成的文件</span>
          <button class="collapse-btn" @click="toggleGeneratedFilesPanel">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>
        <div class="panel-content">
          <div class="panel-file-list">
            <div v-if="sessionGeneratedFiles.length === 0" class="empty-files">
              <span>暂无生成的文件</span>
            </div>
            <div v-else class="file-tree">
              <FileTreeNode 
                v-for="item in sessionGeneratedFiles" 
                :key="item.id" 
                :item="item" 
                :selectedId="selectedFile?.id"
                @select="selectPanelFile"
              />
            </div>
          </div>
          <div class="panel-file-preview">
            <div v-if="selectedFile" class="preview-content">
              <div class="preview-header">
                <div class="preview-title">
                  <span class="preview-filename">{{ selectedFile.name }}</span>
                  <span class="preview-size">{{ formatFileSize(selectedFile.size) }}</span>
                </div>
                <button class="download-btn" @click="downloadFile(selectedFile.file_path, selectedFile.name)" title="下载文件">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                    <polyline points="7 10 12 15 17 10"></polyline>
                    <line x1="12" y1="15" x2="12" y2="3"></line>
                  </svg>
                </button>
              </div>
              <div class="preview-body" :class="{ 'is-markdown': isMarkdownFile }">
                <div v-if="isCodeFile && !isMarkdownFile && !isDocxFile && !isExcelFile && !isPptFile && !isPdfFile" style="height:100%;">
                  <CodePreview 
                    :content="panelFileContent" 
                    :language="selectedFile?.file_type"
                  />
                </div>
                <div v-else-if="isMarkdownFile && renderedMarkdownContent" class="markdown-preview" v-html="renderedMarkdownContent"></div>
                <div v-else-if="isPdfFile" style="height:100%;">
                  <PdfPreview :fileUrl="fileBinaryUrl" />
                </div>
                <div v-else-if="isDocxFile" style="height:100%;">
                  <DocxPreview :fileUrl="fileBinaryUrl" />
                </div>
                <div v-else-if="isExcelFile" style="height:100%;">
                  <ExcelPreview :fileUrl="fileBinaryUrl" />
                </div>
                <div v-else-if="isPptFile" style="height:100%;">
                  <PptPreview :fileUrl="fileDownloadUrl" fileType="PPT" />
                </div>
                <div v-else-if="panelFileContent" class="plain-text-preview"><pre><code>{{ panelFileContent }}</code></pre></div>
                <div v-else class="preview-loading">加载中...</div>
              </div>
            </div>
            <div v-else class="preview-empty">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
                <polyline points="14 2 14 8 20 8"></polyline>
              </svg>
              <span>选择文件查看内容</span>
            </div>
          </div>
        </div>
      </div>
    </div>
    
    <ChatInput
      @send="handleSend"
      :disabled="isStreaming"
      :isStreaming="isStreaming"
      :session-id="currentSessionId"
      @stop="handleStop"
      @createSession="handleCreateSession"
    />
  </div>
</template>

<script setup>
import { ref, watch, nextTick, computed, onMounted, onUnmounted } from 'vue'
import ChatMessage from './ChatMessage.vue'
import ChatInput from './ChatInput.vue'
import EasyLogo from './EasyLogo.vue'
import FileIcon from './FileIcon.vue'
import FileTreeNode from './FileTreeNode.vue'
import CodePreview from './CodePreview.vue'
import DocxPreview from './DocxPreview.vue'
import ExcelPreview from './ExcelPreview.vue'
import PptPreview from './PptPreview.vue'
import PdfPreview from './PdfPreview.vue'
import MarkdownIt from 'markdown-it'

const md = new MarkdownIt({
  html: true,
  linkify: true,
  typographer: true,
  breaks: true
})
import { getSessionGeneratedFiles, getFileContent, downloadFile } from '../api/files'

const welcomeTitle = '我是 Easy Agent'
const welcomeSubtitle = '简单易用的智能助手，有什么可以帮您？'
const displayedTitle = ref('')
const displayedSubtitle = ref('')
let titleTimer = null
let subtitleTimer = null

function startTypingEffect() {
  if (titleTimer) {
    clearInterval(titleTimer)
    titleTimer = null
  }
  if (subtitleTimer) {
    clearInterval(subtitleTimer)
    subtitleTimer = null
  }

  displayedTitle.value = welcomeTitle
  displayedSubtitle.value = ''

  let subtitleIndex = 0

  subtitleTimer = setInterval(() => {
    if (subtitleIndex < welcomeSubtitle.length) {
      displayedSubtitle.value += welcomeSubtitle[subtitleIndex]
      subtitleIndex++
    } else {
      clearInterval(subtitleTimer)
      subtitleTimer = null
    }
  }, 50)
}

onMounted(() => {
  startTypingEffect()
})

onUnmounted(() => {
  if (titleTimer) clearInterval(titleTimer)
  if (subtitleTimer) clearInterval(subtitleTimer)
})

const props = defineProps({
  messages: {
    type: Array,
    default: () => []
  },
  currentSessionId: {
    type: String,
    default: null
  },
  hasFiles: {
    type: Boolean,
    default: false
  },
  isStreaming: {
    type: Boolean,
    default: false
  },
  scrollTrigger: {
    type: Number,
    default: 0
  }
})

watch(() => props.messages, (newMessages) => {
  if (newMessages.length === 0) {
    startTypingEffect()
  }
})

const emit = defineEmits(['sendMessage', 'stop', 'removeFile', 'createSession'])
const messagesRef = ref(null)
const messageRefs = ref({})
const currentUserMessageIndex = ref(-1)
const userMessageIndices = ref([])

const sessionGeneratedFiles = ref([])
const generatedFilesExpanded = ref(false)
const selectedFile = ref(null)
const panelFileContent = ref('')
const renderedMarkdownContent = ref('')
const docxBlobUrl = ref('')
const panelRef = ref(null)
const panelWidth = ref(50)
const isResizing = ref(false)

function startResize(e) {
  isResizing.value = true
  document.addEventListener('mousemove', doResize)
  document.addEventListener('mouseup', stopResize)
}

function doResize(e) {
  if (!isResizing.value) return
  const windowWidth = window.innerWidth
  const newWidth = ((windowWidth - e.clientX) / windowWidth) * 100
  panelWidth.value = Math.max(20, Math.min(80, newWidth))
}

function stopResize() {
  isResizing.value = false
  document.removeEventListener('mousemove', doResize)
  document.removeEventListener('mouseup', stopResize)
}

function formatFileSize(bytes) {
  if (!bytes || bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
}

async function loadPanelFileContent(filePath) {
  try {
    panelFileContent.value = await getFileContent(filePath)
  } catch (e) {
    panelFileContent.value = '加载失败: ' + e.message
  }
}

function toggleGeneratedFilesPanel() {
  generatedFilesExpanded.value = !generatedFilesExpanded.value
}

function selectPanelFile(file) {
  if (file.type !== 'file') return
  selectedFile.value = file
  loadPanelFileContent(file.file_path)
}

const hasGeneratedFiles = computed(() => {
  console.log('[Chat DEBUG] props.hasFiles:', props.hasFiles, 'type:', typeof props.hasFiles)
  return props.hasFiles
})

const isCodeFile = computed(() => {
  if (!selectedFile.value) return false
  const ext = selectedFile.value.file_type?.toLowerCase() || ''
  const codeExtensions = ['js', 'ts', 'py', 'java', 'go', 'rs', 'cpp', 'c', 'h', 'hpp', 'cs', 'php', 'rb', 'swift', 'kt', 'scala', 'vue', 'jsx', 'tsx', 'html', 'css', 'scss', 'less', 'json', 'xml', 'yaml', 'yml', 'txt', 'sh', 'bash', 'sql', 'graphql', 'dockerfile', 'makefile']
  return codeExtensions.includes(ext)
})

const isMarkdownFile = computed(() => {
  if (!selectedFile.value) return false
  const ext = selectedFile.value.file_type?.toLowerCase()
  return ext === 'md' || ext === 'markdown'
})

const isDocxFile = computed(() => {
  if (!selectedFile.value) return false
  const ext = selectedFile.value.file_type?.toLowerCase()
  return ext === 'docx' || ext === 'doc'
})

const isExcelFile = computed(() => {
  if (!selectedFile.value) return false
  const ext = selectedFile.value.file_type?.toLowerCase()
  return ext === 'xlsx' || ext === 'xls' || ext === 'csv'
})

const isPptFile = computed(() => {
  if (!selectedFile.value) return false
  const ext = selectedFile.value.file_type?.toLowerCase()
  return ext === 'pptx' || ext === 'ppt'
})

const isPdfFile = computed(() => {
  if (!selectedFile.value) return false
  const ext = selectedFile.value.file_type?.toLowerCase()
  return ext === 'pdf'
})

const fileDownloadUrl = computed(() => {
  if (!selectedFile.value) return ''
  const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
  return `${API_BASE_URL}/api/files/download?file_path=${encodeURIComponent(selectedFile.value.file_path)}`
})

const fileBinaryUrl = computed(() => {
  if (!selectedFile.value) return ''
  const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
  return `${API_BASE_URL}/api/files/binary?file_path=${encodeURIComponent(selectedFile.value.file_path)}`
})

async function fetchSessionGeneratedFiles() {
  if (props.currentSessionId) {
    try {
      sessionGeneratedFiles.value = await getSessionGeneratedFiles(props.currentSessionId)
    } catch (e) {
      console.error('获取生成的文件失败:', e)
      sessionGeneratedFiles.value = []
    }
  }
}

watch(() => props.currentSessionId, () => {
  generatedFilesExpanded.value = false
  selectedFile.value = null
  panelFileContent.value = ''
  renderedMarkdownContent.value = ''
  fetchSessionGeneratedFiles()
}, { immediate: true })

watch(() => props.hasFiles, (newVal) => {
  if (newVal && props.currentSessionId) {
    fetchSessionGeneratedFiles()
  }
})

watch(panelFileContent, (newContent) => {
  if (selectedFile.value && newContent) {
    const ext = selectedFile.value.file_type?.toLowerCase()
    if (ext === 'md' || ext === 'markdown') {
      try {
        renderedMarkdownContent.value = md.render(newContent)
      } catch (e) {
        console.error('Markdown render error:', e)
        renderedMarkdownContent.value = newContent
      }
    }
  }
})

function setMessageRef(el, index) {
  if (el) {
    messageRefs.value[index] = el
  }
}

function updateUserMessageIndices() {
  userMessageIndices.value = props.messages
    .map((msg, index) => msg.role === 'user' ? index : -1)
    .filter(index => index !== -1)
    .reverse()
  currentUserMessageIndex.value = -1
}

const canGoToPrevUserMessage = computed(() => {
  return userMessageIndices.value.length > 0 && currentUserMessageIndex.value < userMessageIndices.value.length - 1
})

const canGoToNextUserMessage = computed(() => {
  return currentUserMessageIndex.value > 0
})

onMounted(() => {
  updateUserMessageIndices()
})

function goToPrevUserMessage() {
  if (userMessageIndices.value.length === 0) return
  
  if (currentUserMessageIndex.value === -1) {
    currentUserMessageIndex.value = 0
  } else if (currentUserMessageIndex.value < userMessageIndices.value.length - 1) {
    currentUserMessageIndex.value++
  }
  
  const targetIndex = userMessageIndices.value[currentUserMessageIndex.value]
  
  if (targetIndex !== undefined && messageRefs.value[targetIndex]) {
    messageRefs.value[targetIndex].scrollIntoView({ behavior: 'smooth', block: 'center' })
  }
}

function goToNextUserMessage() {
  if (currentUserMessageIndex.value > 0) {
    currentUserMessageIndex.value--
    const targetIndex = userMessageIndices.value[currentUserMessageIndex.value]
    if (targetIndex !== undefined && messageRefs.value[targetIndex]) {
      messageRefs.value[targetIndex].scrollIntoView({ behavior: 'smooth', block: 'center' })
    }
  }
}

function handleSend(message, files, signal, enableDeepThink = true, enableKnowledgeBase = false) {
  emit('sendMessage', message, files, signal, enableDeepThink, enableKnowledgeBase)
}

function handleRemoveFile(file, messageIndex) {
  // 从事件参数中获取file，然后从messages中获取对应的message
  const message = props.messages[messageIndex]
  emit('removeFile', message, messageIndex, file)
}

function handleRetry(content) {
  // 向上传递重试事件
  emit('retry', content)
}

function handleStop() {
  emit('stop')
}

function handleCreateSession() {
  emit('createSession')
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesRef.value) {
      messagesRef.value.scrollTop = messagesRef.value.scrollHeight
    }
  })
}

watch(() => props.messages, (newMessages, oldMessages) => {
  if (props.isStreaming) {
    scrollToBottom()
  }
  nextTick(() => {
    updateUserMessageIndices()
  })
}, { deep: true })

watch(() => props.scrollTrigger, () => {
  scrollToBottom()
})
</script>

<style scoped>
.chat-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: #ffffff;
  overflow: hidden;
}

.chat-header {
  padding: 12px 24px;
  border-bottom: 1px solid #e5e7eb;
  background: #f9fafb;
}

.generated-files-header-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  background: #f0fdf4;
  border: 1px solid #86efac;
  border-radius: 8px;
  color: #166534;
  font-size: 14px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.generated-files-header-btn:hover {
  background: #dcfce7;
  border-color: #4ade80;
}

.generated-files-header-btn svg {
  width: 18px;
  height: 18px;
}

.chat-messages {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
  scroll-behavior: smooth;
  display: flex;
  flex-direction: column;
}

.welcome-screen {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #64748b;
}

.welcome-icon {
  margin-bottom: 20px;
}

.welcome-screen h2 {
  font-size: 24px;
  font-weight: 600;
  color: #1e293b;
  margin: 0 0 8px 0;
}

.welcome-screen p {
  font-size: 14px;
  color: #94a3b8;
  margin: 0;
}

.scroll-btn {
  position: fixed;
  right: 24px;
  width: 40px;
  height: 40px;
  border: none;
  background: white;
  border-radius: 50%;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s;
  z-index: 10;
}

.scroll-btn.prev {
  bottom: 120px;
}

.scroll-btn.next {
  bottom: 170px;
}

.scroll-btn:hover {
  background: #f1f5f9;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.scroll-btn svg {
  width: 20px;
  height: 20px;
  color: #64748b;
}

.scroll-btn:hover svg {
  color: #0ea5e9;
}

.loading-container {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  padding: 12px 0;
  gap: 8px;
  width: 80%;
  margin: 0 auto;
}

.loading-animation {
  display: flex;
  gap: 4px;
  align-items: center;
}

.loading-dot {
  width: 6px;
  height: 6px;
  background: linear-gradient(135deg, #0ea5e9, #06b6d4);
  border-radius: 50%;
  animation: bounce 1.4s infinite ease-in-out;
}

.loading-dot:nth-child(1) {
  animation-delay: -0.32s;
}

.loading-dot:nth-child(2) {
  animation-delay: -0.16s;
}

.loading-dot:nth-child(3) {
  animation-delay: 0s;
}

@keyframes bounce {
  0%, 80%, 100% {
    transform: scale(0);
    opacity: 0.5;
  }
  40% {
    transform: scale(1);
    opacity: 1;
  }
}

.loading-text {
  font-size: 12px;
  color: #94a3b8;
  animation: pulse 2s infinite;
}

@keyframes pulse {
  0%, 100% {
    opacity: 1;
  }
  50% {
    opacity: 0.5;
  }
}

.generated-files-float {
  position: fixed;
  right: 20px;
  top: 20%;
  z-index: 1000;
}

.generated-files-btn-only {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  height: 44px;
  padding: 0;
  background: #f0fdf4;
  border: 1px solid #86efac;
  border-radius: 10px;
  color: #166534;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.generated-files-btn-only:hover {
  background: #dcfce7;
  border-color: #4ade80;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.generated-files-btn-only svg {
  width: 22px;
  height: 22px;
}

.generated-files-panel {
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.1);
  width: 50vw;
  height: 60vh;
  min-width: 400px;
  min-height: 300px;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  position: relative;
}

.resize-handle {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 6px;
  cursor: ew-resize;
  background: transparent;
  z-index: 10;
}

.resize-handle:hover {
  background: #86efac;
}

.panel-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: #f0fdf4;
  border-bottom: 1px solid #86efac;
  flex-shrink: 0;
}

.panel-title {
  font-size: 14px;
  font-weight: 500;
  color: #166534;
}

.collapse-btn {
  background: none;
  border: none;
  padding: 4px;
  cursor: pointer;
  color: #166534;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
}

.collapse-btn:hover {
  background: #dcfce7;
}

.collapse-btn svg {
  width: 18px;
  height: 18px;
}

.panel-content {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.panel-file-list {
  width: 200px;
  border-right: 1px solid #e5e7eb;
  overflow-y: auto;
  padding: 8px;
  flex-shrink: 0;
}

.file-tree {
  width: 100%;
}

.empty-files {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #9ca3af;
  font-size: 13px;
}

.panel-file-preview {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.preview-content {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.preview-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-bottom: 1px solid #e5e7eb;
  background: #f9fafb;
  flex-shrink: 0;
}

.preview-title {
  display: flex;
  align-items: center;
  gap: 8px;
  overflow: hidden;
}

.download-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  padding: 0;
  margin-top: -4px;
  background: none;
  border: none;
  border-radius: 4px;
  cursor: pointer;
  color: #6b7280;
  transition: all 0.15s ease;
  flex-shrink: 0;
}

.download-btn:hover {
  background: #f3f4f6;
  color: #374151;
}

.download-btn svg {
  width: 18px;
  height: 18px;
}

.preview-filename {
  font-size: 13px;
  font-weight: 500;
  color: #374151;
}

.preview-size {
  font-size: 12px;
  color: #9ca3af;
}

.preview-body {
  flex: 1;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.preview-body.is-markdown {
  overflow-y: auto;
}

.preview-body .code-preview-container {
  flex: 1;
}

.preview-body pre {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}

.preview-body code {
  font-family: 'Consolas', 'Monaco', monospace;
  color: #374151;
}

.markdown-preview {
  padding: 12px;
  overflow-y: auto;
  flex: 1;
  background: #fafafa;
  color: #333;
  font-size: 14px;
  line-height: 1.6;
}

.plain-text-preview {
  flex: 1;
  overflow: auto;
  background: #1e1e1e;
  padding: 12px;
}

.plain-text-preview pre {
  margin: 0;
  color: #d4d4d4;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 13px;
  white-space: pre-wrap;
  word-wrap: break-word;
}

.plain-text-preview code {
  font-family: inherit;
}

.markdown-preview h1,
.markdown-preview h2,
.markdown-preview h3,
.markdown-preview h4,
.markdown-preview h5,
.markdown-preview h6 {
  margin-top: 16px;
  margin-bottom: 8px;
  font-weight: 600;
}

.markdown-preview h1 { font-size: 24px; }
.markdown-preview h2 { font-size: 20px; }
.markdown-preview h3 { font-size: 18px; }

.markdown-preview p {
  margin: 8px 0;
}

.markdown-preview code {
  background: #f0f0f0;
  padding: 2px 6px;
  border-radius: 3px;
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 13px;
}

.markdown-preview pre {
  background: #1e1e1e;
  color: #d4d4d4;
  padding: 12px;
  border-radius: 6px;
  overflow-x: auto;
}

.markdown-preview pre code {
  background: transparent;
  padding: 0;
  color: inherit;
}

.markdown-preview ul,
.markdown-preview ol {
  padding-left: 24px;
  margin: 8px 0;
}

.markdown-preview li {
  margin: 4px 0;
}

.markdown-preview blockquote {
  border-left: 4px solid #86efac;
  padding-left: 12px;
  margin: 8px 0;
  color: #666;
}

.markdown-preview a {
  color: #166534;
  text-decoration: none;
}

.markdown-preview a:hover {
  text-decoration: underline;
}

.preview-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #9ca3af;
  gap: 8px;
}

.preview-loading {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: #9ca3af;
  font-size: 13px;
}

.preview-empty svg {
  width: 40px;
  height: 40px;
}

.preview-empty span {
  font-size: 13px;
}

.panel-file-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.panel-file-item:hover {
  background: #f3f4f6;
}

.panel-file-item.active {
  background: #dbeafe;
}

.panel-file-name {
  font-size: 13px;
  color: #374151;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
