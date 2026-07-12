<template>
  <div class="chat-input-container">
    <div class="input-box">
      <div v-if="uploadedFiles.length > 0" class="uploaded-files">
        <div 
          v-for="(file, index) in uploadedFiles" 
          :key="index" 
          class="uploaded-file"
        >
          <div class="file-icon">
            <FileIcon :filename="file.filename" :size="36" />
          </div>
          <div class="file-info">
            <span class="file-name">{{ file.filename }}</span>
            <div class="file-meta">
              <span class="file-size">{{ formatSize(file.size) }}</span>
              <div v-if="file.uploadStatus === 'uploading'" class="upload-progress">
                <div class="progress-bar">
                  <div class="progress-fill" :style="{ width: file.uploadProgress + '%' }"></div>
                </div>
                <span class="progress-text">{{ file.uploadProgress }}%</span>
              </div>
              <span v-else-if="file.uploadStatus === 'completed'" class="upload-status completed">上传完成</span>
              <span v-else-if="file.uploadStatus === 'error'" class="upload-status error">上传失败</span>
            </div>
          </div>
          <button @click="removeFile(index)" class="remove-file-btn">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="18" y1="6" x2="6" y2="18"></line>
              <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
          </button>
        </div>
      </div>
      
      <div class="input-row">
        <div class="input-field">
          <textarea
            ref="textareaRef"
            v-model="message"
            @keydown.enter.exact.prevent="send"
            @input="autoResize"
            placeholder=""
            :disabled="disabled"
            rows="1"
          ></textarea>
        </div>
      </div>
      
      <div class="input-actions">
        <div class="left-actions">
          <div class="model-dropdown-wrapper" ref="modelDropdownRef">
            <button
              class="model-btn"
              :class="{ disabled: isStreaming || disabled }"
              :disabled="isStreaming || disabled"
              @click="toggleModelDropdown"
              title="选择模型"
            >
              <svg class="model-btn-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <rect x="3" y="3" width="18" height="18" rx="2" ry="2"/>
                <circle cx="8.5" cy="8.5" r="1.5"/>
                <polyline points="21 15 16 10 5 21"/>
              </svg>
              <span class="model-btn-label">{{ currentModelLabel }}</span>
              <svg class="model-btn-arrow" :class="{ open: showModelDropdown }" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="6 9 12 15 18 9"></polyline>
              </svg>
            </button>
            <Teleport to="body">
              <div v-if="showModelDropdown" class="model-dropdown-menu" :style="dropdownStyle" @click.stop>
                <div
                  v-for="m in models"
                  :key="m.name"
                  class="model-dropdown-item"
                  :class="{ active: m.name === localSelectedModel }"
                  @click="selectModel(m.name)"
                >
                  <div class="model-item-info">
                    <span class="model-item-name">{{ m.model || m.name }}</span>
                    <span class="model-item-provider">{{ m.provider }}</span>
                  </div>
                  <span v-if="m.is_active" class="model-item-badge">默认</span>
                  <svg v-if="m.name === localSelectedModel" class="model-item-check" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                    <polyline points="20 6 9 17 4 12"></polyline>
                  </svg>
                </div>
              </div>
            </Teleport>
          </div>

          <label class="action-btn upload-btn" :class="{ disabled: isStreaming || disabled }" title="上传文件">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M21.44 11.05l-9.19 9.19a6 6 0 0 1-8.49-8.49l9.19-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48"></path>
            </svg>
            <input
              type="file"
              @change="handleFileSelect"
              multiple
              :disabled="isStreaming || disabled"
              hidden
            />
          </label>
        </div>
        
        <div class="right-actions">
          <div 
            v-if="showTokenRing" 
            ref="ringRef"
            class="context-ring-wrapper" 
            @click.stop="toggleTokenPopup"
          >
            <svg class="context-ring" viewBox="0 0 36 36">
              <circle class="context-ring-bg" cx="18" cy="18" r="15.9" fill="none" stroke="#e5e7eb" stroke-width="3" />
              <circle class="context-ring-fill" cx="18" cy="18" r="15.9" fill="none"
                :stroke="contextColor" stroke-width="3" stroke-linecap="round"
                :stroke-dasharray="`${contextPercent} ${100 - contextPercent}`"
                transform="rotate(-90 18 18)" />
            </svg>
            <span class="context-ring-text">{{ contextPercent }}%</span>
            <Teleport to="body">
              <div v-if="showTokenPopup" class="token-popup" :style="popupStyle" @click.stop>
                <div class="token-popup-title">会话信息</div>
                <div class="token-popup-section">
                  <div class="token-popup-row">
                    <span class="token-popup-label">会话耗时</span>
                    <span class="token-popup-value duration-value">{{ formattedDuration }}</span>
                  </div>
                  <div class="token-popup-row">
                    <span class="token-popup-label">迭代次数</span>
                    <span class="token-popup-value">{{ iterationCount }}</span>
                  </div>
                </div>
                <div class="token-popup-divider"></div>
                <div class="token-popup-section">
                  <div class="token-popup-row">
                    <span class="token-popup-label">上下文占用</span>
                  </div>
                  <div class="token-popup-context-row">
                    <span class="token-popup-context-value">{{ formatTokens(sessionUsage.context_tokens || sessionUsage.total_tokens) }}/{{ formatTokens(sessionUsage.max_input_tokens) }}</span>
                    <span class="token-popup-context-percent" :style="{ color: contextColor }">{{ contextPercent }}%</span>
                  </div>
                  <div class="token-popup-bar">
                    <div class="token-popup-bar-inner">
                      <div class="token-popup-bar-fill" :style="{ width: contextPercent + '%', background: contextColor }"></div>
                    </div>
                  </div>
                </div>
                <div class="token-popup-divider"></div>
                <div class="token-popup-row">
                  <span class="token-popup-label">总输入 (Prompt)</span>
                  <span class="token-popup-value input">{{ formatTokens(sessionUsage.input_tokens) }}</span>
                </div>
                <div class="token-popup-row">
                  <span class="token-popup-label">总输出 (Completion)</span>
                  <span class="token-popup-value output">{{ formatTokens(sessionUsage.output_tokens) }}</span>
                </div>
              </div>
            </Teleport>
          </div>
          <button 
            v-if="!isStreaming"
            @click="send" 
            class="action-btn send-btn"
            :class="{ active: canSend }"
            :disabled="!canSend || disabled"
            title="发送"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <line x1="5" y1="12" x2="19" y2="12"></line>
              <polyline points="12 5 19 12 12 19"></polyline>
            </svg>
          </button>
          <button
            v-if="isStreaming"
            @click="stop"
            class="action-btn stop-btn"
            title="停止"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor">
              <rect x="6" y="6" width="12" height="12" rx="2"></rect>
            </svg>
          </button>
        </div>
      </div>
    </div>
    
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { uploadFile, deleteFile } from '../api/files.js'
import FileIcon from './FileIcon.vue'

const props = defineProps({
  disabled: {
    type: Boolean,
    default: false
  },
  sessionId: {
    type: String,
    default: null
  },
  isStreaming: {
    type: Boolean,
    default: false
  },
  sessionUsage: {
    type: Object,
    default: () => ({ input_tokens: 0, output_tokens: 0, total_tokens: 0, max_input_tokens: null, auto_compress_tokens: null, context_tokens: 0 })
  },
  sessionDuration: {
    type: Number,
    default: 0
  },
  iterationCount: {
    type: Number,
    default: 0
  },
  models: {
    type: Array,
    default: () => []
  },
  selectedModel: {
    type: String,
    default: null
  }
})

const emit = defineEmits(['send', 'stop', 'createSession', 'update:selectedModel'])

const message = ref('')
const textareaRef = ref(null)
const uploadedFiles = ref([])
let abortController = null

// 模型选择：本地双向绑定，变化时同步父组件
const localSelectedModel = computed({
  get: () => props.selectedModel,
  set: (val) => emit('update:selectedModel', val)
})

// 自定义下拉菜单
const showModelDropdown = ref(false)
const modelDropdownRef = ref(null)
const dropdownStyle = ref({})

const currentModelLabel = computed(() => {
  const m = props.models.find(m => m.name === props.selectedModel)
  return m ? (m.model || m.name) : '选择模型'
})

function toggleModelDropdown() {
  if (props.isStreaming || props.disabled) return
  if (showModelDropdown.value) {
    showModelDropdown.value = false
    return
  }
  // 计算下拉菜单位置
  if (modelDropdownRef.value) {
    const rect = modelDropdownRef.value.getBoundingClientRect()
    dropdownStyle.value = {
      position: 'fixed',
      bottom: `${window.innerHeight - rect.top + 6}px`,
      left: `${rect.left}px`,
      minWidth: `${Math.max(rect.width, 200)}px`,
    }
  }
  showModelDropdown.value = true
}

function selectModel(name) {
  emit('update:selectedModel', name)
  showModelDropdown.value = false
  console.log(
    `[${new Date().toISOString()}] [模型选择] 切换为: ${name}`
  )
}

function closeModelDropdown(event) {
  if (showModelDropdown.value && modelDropdownRef.value && !modelDropdownRef.value.contains(event.target)) {
    showModelDropdown.value = false
  }
}

const canSend = computed(() => {
  return message.value.trim() || uploadedFiles.value.length > 0
})

const showTokenPopup = ref(false)

// ========== 会话耗时格式化 ==========
const formattedDuration = computed(() => {
  const total = Math.floor(props.sessionDuration || 0)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) {
    return `${h}小时${m}分${s}秒`
  }
  if (m > 0) {
    return `${m}分${s}秒`
  }
  return `${s}秒`
})

function toggleTokenPopup() {
  showTokenPopup.value = !showTokenPopup.value
}

function closeTokenPopup() {
  if (showTokenPopup.value) {
    showTokenPopup.value = false
  }
}

const showTokenRing = computed(() => {
  return props.sessionUsage.total_tokens > 0 || props.sessionUsage.context_tokens > 0 || props.sessionDuration > 0 || props.iterationCount > 0
})

const contextPercent = computed(() => {
  const u = props.sessionUsage
  if (!u.max_input_tokens || u.max_input_tokens <= 0) return 0
  // 优先使用 context_tokens（当前上下文窗口占用），否则用 total_tokens 兜底
  const ctxTokens = u.context_tokens || u.total_tokens || 0
  return Math.min(100, Math.round(ctxTokens / u.max_input_tokens * 100))
})

const contextColor = computed(() => {
  const p = contextPercent.value
  if (p >= 80) return '#ef4444'
  if (p >= 50) return '#f59e0b'
  return '#22c55e'
})

const ringRef = ref(null)

const popupStyle = computed(() => {
  if (!ringRef.value) return {}
  const rect = ringRef.value.getBoundingClientRect()
  return {
    position: 'fixed',
    bottom: `${window.innerHeight - rect.top + 10}px`,
    right: `${window.innerWidth - rect.right - 8}px`,
  }
})

function formatTokens(n) {
  if (!n) return '0'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k'
  return n.toString()
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

async function handleFileSelect(event) {
  const files = Array.from(event.target.files)
  
  for (const file of files) {
    // 创建文件项
    const fileItem = {
      file: file,
      filename: file.name,
      size: file.size,
      uploadStatus: 'uploading',
      uploadProgress: 0
    }
    
    // 添加到数组
    uploadedFiles.value.push(fileItem)
    
    // 强制触发初始渲染
    uploadedFiles.value = [...uploadedFiles.value]
    
    try {
      // 确保会话存在
      let sessionId = props.sessionId
      if (!sessionId) {
        // 通知父组件创建会话
        const sessionIdPromise = new Promise((resolve) => {
          // 创建一个临时的监听器来等待会话创建完成
          const unwatch = watch(
            () => props.sessionId,
            (newSessionId) => {
              if (newSessionId) {
                unwatch()
                resolve(newSessionId)
              }
            }
          )
          // 触发会话创建
          emit('createSession')
        })
        
        // 等待会话创建完成
        sessionId = await sessionIdPromise
        
        if (!sessionId) {
          throw new Error('会话创建失败')
        }
      }
      
      // 调用实际的上传接口
      const response = await uploadFile(sessionId, file, (progress) => {
        // 找到对应的文件项并更新进度
        const index = uploadedFiles.value.findIndex(f => f.file === file)
        if (index !== -1) {
          uploadedFiles.value[index].uploadProgress = progress
          // 强制触发更新
          uploadedFiles.value = [...uploadedFiles.value]
        }
      })
      
      // 找到对应的文件项并更新状态
      const index = uploadedFiles.value.findIndex(f => f.file === file)
      if (index !== -1) {
        uploadedFiles.value[index].uploadStatus = 'completed'
        uploadedFiles.value[index].uploadProgress = 100
        uploadedFiles.value[index].filePath = response.file_path
        uploadedFiles.value[index].id = response.id
        // 强制触发更新
        uploadedFiles.value = [...uploadedFiles.value]
      }
      
      console.log('文件上传成功:', response)
    } catch (error) {
      // 找到对应的文件项并更新状态
      const index = uploadedFiles.value.findIndex(f => f.file === file)
      if (index !== -1) {
        uploadedFiles.value[index].uploadStatus = 'error'
        // 强制触发更新
        uploadedFiles.value = [...uploadedFiles.value]
      }
      console.error('文件上传失败:', error)
    }
  }
  
  event.target.value = ''
}

async function removeFile(index) {
  const file = uploadedFiles.value[index]
  
  // 如果文件已经上传成功，调用后台的删除接口
  if (file && file.uploadStatus === 'completed' && file.id && props.sessionId) {
    try {
      await deleteFile(props.sessionId, file)
      console.log('文件删除成功:', file.filename)
    } catch (error) {
      console.error('文件删除失败:', error)
    }
  }
  
  // 从前端列表中移除文件
  uploadedFiles.value.splice(index, 1)
  // 强制触发更新
  uploadedFiles.value = [...uploadedFiles.value]
}

async function send() {
  if (!canSend.value || props.disabled) return
  
  // 等待所有文件上传完成
  const uploadingFiles = uploadedFiles.value.filter(f => f.uploadStatus === 'uploading')
  if (uploadingFiles.length > 0) {
    // 显示上传中提示
    console.log('文件正在上传中，请稍候...')
    // 可以添加一个loading状态或提示信息
    return
  }
  
  abortController = new AbortController()
  
  const filesToSend = uploadedFiles.value.map(f => ({
    id: f.id,
    filename: f.filename,
    size: f.size,
    file: f.file,
    file_path: f.filePath || null,
    type: f.file?.type || f.fileType || ''
  }))

  // 详细日志：记录发送操作、文件上传
  const ts = new Date().toISOString()
  console.log(`[${ts}] [发送消息] 内容: "${message.value.substring(0, 100)}" | 文件数: ${filesToSend.length}`)
  if (filesToSend.length > 0) {
    console.log(`[${ts}] [文件上传] ${filesToSend.map(f => `${f.filename}(${f.size}B)`).join(', ')}`)
  }

  emit('send', message.value.trim().replace(/\s+/g, ' '), filesToSend, abortController.signal, true)
  
  message.value = ''
  uploadedFiles.value = []
  nextTick(() => autoResize())
}

function stop() {
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  emit('stop')
}

function autoResize() {
  if (textareaRef.value) {
    textareaRef.value.style.height = 'auto'
    textareaRef.value.style.height = Math.min(textareaRef.value.scrollHeight, 150) + 'px'
  }
}

watch(() => props.disabled, (val) => {
  if (!val && textareaRef.value) {
    textareaRef.value.focus()
  }
})

onMounted(() => {
  document.addEventListener('click', closeTokenPopup)
  document.addEventListener('click', closeModelDropdown)
})

onUnmounted(() => {
  document.removeEventListener('click', closeTokenPopup)
  document.removeEventListener('click', closeModelDropdown)
  if (abortController) {
    abortController.abort()
  }
})
</script>

<style scoped>
.chat-input-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 16px 24px 24px;
  background: transparent;
}

.input-box {
  width: 80%;
  max-width: 900px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 16px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.05);
  transition: all 0.3s ease;
  overflow: hidden;
}

.input-box:focus-within {
  border-color: #0ea5e9;
  box-shadow: 0 2px 12px rgba(14, 165, 233, 0.15);
}

.uploaded-files {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 12px 16px;
  background: #f8fafc;
  border-bottom: 1px solid #f1f5f9;
}

.uploaded-file {
  display: flex;
  align-items: center;
  gap: 10px;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  padding: 10px 14px;
  border-radius: 12px;
  max-width: 250px;
  flex-shrink: 0;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
  transition: all 0.2s ease;
}

.uploaded-file:hover {
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.08);
  border-color: #cbd5e1;
}

.file-icon {
  width: 40px;
  height: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.file-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.file-name {
  font-size: 13px;
  font-weight: 500;
  color: #1e293b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-meta {
  display: flex;
  align-items: center;
  gap: 8px;
}

.file-size {
  font-size: 11px;
  color: #64748b;
}

.upload-progress {
  display: flex;
  align-items: center;
  gap: 6px;
}

.progress-bar {
  width: 60px;
  height: 4px;
  background: #f1f5f9;
  border-radius: 2px;
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #0ea5e9, #06b6d4);
  border-radius: 2px;
  transition: width 0.3s ease;
}

.progress-text {
  font-size: 10px;
  color: #64748b;
  min-width: 35px;
}

.upload-status {
  font-size: 10px;
  font-weight: 500;
  padding: 2px 6px;
  border-radius: 8px;
}

.upload-status.completed {
  color: #15803d;
  background: #dcfce7;
}

.upload-status.error {
  color: #b91c1c;
  background: #fee2e2;
}

.remove-file-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  background: #f1f5f9;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s ease;
  flex-shrink: 0;
}

.remove-file-btn:hover {
  background: #e2e8f0;
  transform: scale(1.05);
}

.remove-file-btn svg {
  width: 14px;
  height: 14px;
  color: #64748b;
}

.remove-file-btn:hover svg {
  color: #dc2626;
}

.input-row {
  padding: 12px 16px;
  display: flex;
  align-items: flex-end;
  gap: 12px;
}

.input-field {
  width: 100%;
}

.input-field textarea {
  width: 100%;
  border: none;
  background: transparent;
  resize: none;
  font-size: 15px;
  line-height: 1.6;
  color: #1e293b;
  max-height: 150px;
  min-height: 24px;
  padding: 0;
  outline: none;
}

.input-field textarea::placeholder {
  color: #94a3b8;
  font-size: 14px;
}

.input-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 16px 12px;
  border-top: none;
  background: transparent;
}

.left-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.model-dropdown-wrapper {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.model-btn {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 6px 12px;
  height: 36px;
  min-height: 36px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  white-space: nowrap;
}

.model-btn:hover:not(.disabled) {
  border-color: rgba(14, 165, 233, 0.4);
  background: rgba(14, 165, 233, 0.06);
  transform: translateY(-1px);
}

.model-btn.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.model-btn-icon {
  width: 16px;
  height: 16px;
  color: #64748b;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}

.model-btn:hover:not(.disabled) .model-btn-icon {
  color: #0ea5e9;
  transform: scale(1.08);
}

.model-btn-label {
  font-size: 12px;
  font-weight: 500;
  color: #64748b;
  transition: color 0.25s ease;
  max-width: 140px;
  overflow: hidden;
  text-overflow: ellipsis;
}

.model-btn:hover:not(.disabled) .model-btn-label {
  color: #0ea5e9;
}

.model-btn-arrow {
  width: 12px;
  height: 12px;
  color: #94a3b8;
  transition: transform 0.2s ease;
}

.model-btn-arrow.open {
  transform: rotate(180deg);
}

.model-dropdown-menu {
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  padding: 6px;
  z-index: 9999;
  max-height: 320px;
  overflow-y: auto;
}

.model-dropdown-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease;
}

.model-dropdown-item:hover {
  background: #f1f5f9;
}

.model-dropdown-item.active {
  background: rgba(14, 165, 233, 0.08);
}

.model-item-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  flex: 1;
  min-width: 0;
}

.model-item-name {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  overflow: hidden;
  text-overflow: ellipsis;
}

.model-item-provider {
  font-size: 11px;
  color: #94a3b8;
}

.model-item-badge {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 4px;
  background: #dcfce7;
  color: #16a34a;
  flex-shrink: 0;
}

.model-item-check {
  width: 16px;
  height: 16px;
  color: #0ea5e9;
  flex-shrink: 0;
}

.right-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: none;
  background: transparent;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
  flex-shrink: 0;
}

.action-btn svg {
  width: 18px;
  height: 18px;
  color: #64748b;
}

.action-btn:hover {
  background: #f1f5f9;
}

.action-btn:hover svg {
  color: #0ea5e9;
}

.send-btn {
  width: 36px;
  height: 36px;
  border: none;
  background: #f1f5f9;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s ease;
}

.send-btn svg {
  width: 18px;
  height: 18px;
  color: #64748b;
  transition: all 0.2s;
}

.send-btn.active {
  background: #0ea5e9;
}

.send-btn.active svg {
  color: white;
}

.send-btn.active:hover:not(:disabled) {
  transform: scale(1.05);
  box-shadow: 0 2px 8px rgba(14, 165, 233, 0.3);
}

.send-btn:disabled {
  cursor: not-allowed;
  opacity: 0.6;
}

.stop-btn {
  width: 36px;
  height: 36px;
  border: none;
  background: #ef4444;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s ease;
}

.stop-btn svg {
  width: 16px;
  height: 16px;
  color: white;
}

.stop-btn:hover {
  transform: scale(1.05);
  box-shadow: 0 2px 8px rgba(239, 68, 68, 0.3);
}

.context-ring-wrapper {
  position: relative;
  width: 36px;
  height: 36px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  transition: transform 0.2s;
}

.context-ring-wrapper:hover {
  transform: scale(1.1);
}

.context-ring {
  width: 36px;
  height: 36px;
}

.context-ring-text {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  font-size: 9px;
  font-weight: 600;
  color: #475569;
  line-height: 1;
  pointer-events: none;
}

.token-popup {
  position: fixed;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  padding: 14px 16px;
  min-width: 200px;
  z-index: 9999;
  cursor: default;
}

.token-popup-title {
  font-size: 12px;
  font-weight: 600;
  color: #1e293b;
  margin-bottom: 10px;
}

.token-popup-section {
  margin-bottom: 2px;
}

.token-popup-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 3px 0;
}

.token-popup-label {
  font-size: 11px;
  color: #64748b;
}

.token-popup-value {
  font-size: 11px;
  font-weight: 600;
  color: #334155;
}

.token-popup-value.input {
  color: #6366f1;
}

.token-popup-value.output {
  color: #06b6d4;
}

.token-popup-value.duration-value {
  color: #6366f1;
  font-weight: 600;
}

.token-popup-context-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 2px 0 4px;
}

.token-popup-context-value {
  font-size: 13px;
  font-weight: 700;
  color: #1e293b;
  font-variant-numeric: tabular-nums;
}

.token-popup-context-percent {
  font-size: 13px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.token-popup-divider {
  height: 1px;
  background: #f1f5f9;
  margin: 6px 0;
}

.token-popup-bar {
  margin-top: 4px;
}

.token-popup-bar-inner {
  height: 5px;
  border-radius: 3px;
  overflow: hidden;
  background: #f1f5f9;
}

.token-popup-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.4s ease;
}

.upload-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}

.upload-btn svg {
  width: 18px;
  height: 18px;
  color: #64748b;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
}

.upload-btn:hover:not(.disabled) {
  border-color: rgba(124, 106, 239, 0.4);
  background: rgba(124, 106, 239, 0.06);
  transform: translateY(-1px);
}

.upload-btn:hover:not(.disabled) svg {
  color: #7c6aef;
  transform: scale(1.08);
}

.upload-btn:active:not(.disabled) {
  transform: translateY(0);
}

.upload-btn.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.copy-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border: none;
  background: transparent;
  border-radius: 8px;
  cursor: pointer;
  transition: all 0.2s ease;
}

.copy-btn svg {
  width: 18px;
  height: 18px;
  color: #64748b;
  transition: all 0.2s;
}

.copy-btn:hover:not(.disabled) {
  background: #f1f5f9;
}

.copy-btn:hover:not(.disabled) svg {
  color: #0ea5e9;
}

.copy-btn.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.input-footer {
  margin-top: 12px;
  text-align: center;
}

.footer-text {
  font-size: 12px;
  color: #94a3b8;
}
</style>
