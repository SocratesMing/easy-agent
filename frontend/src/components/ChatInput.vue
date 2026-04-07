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
            placeholder="请输入你的需求，按「Enter」发送"
            :disabled="disabled"
            rows="1"
          ></textarea>
        </div>
      </div>
      
      <div class="input-actions">
        <div class="left-actions">
          <label class="action-btn upload-btn" :class="{ disabled: isStreaming || disabled }">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
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
          
          <span class="divider">|</span>
          
          <button 
            class="deep-think-btn"
            :class="{ active: enableDeepThink, disabled: isStreaming || disabled }"
            :disabled="isStreaming || disabled"
            @click="enableDeepThink = !enableDeepThink"
            title="深度思考"
          >
            <svg class="deep-think-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M12 15a3 3 0 100-6 3 3 0 000 6z"></path>
              <path fill-rule="evenodd" d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 010 2.83 2 2 0 01-2.83 0l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-2 2 2 2 0 01-2-2v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83 0 2 2 0 010-2.83l.06-.06a1.65 1.65 0 00.33-1.82 1.65 1.65 0 00-1.51-1H3a2 2 0 01-2-2 2 2 0 012-2h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 010-2.83 2 2 0 012.83 0l.06.06a1.65 1.65 0 001.82.33H9a1.65 1.65 0 001-1.51V3a2 2 0 012-2 2 2 0 012 2v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 0 2 2 0 010 2.83l-.06.06a1.65 1.65 0 00-.33 1.82V9a1.65 1.65 0 001.51 1H21a2 2 0 012 2 2 2 0 01-2 2h-.09a1.65 1.65 0 00-1.51 1z" clip-rule="evenodd"></path>
            </svg>
            <span class="deep-think-label">深度思考</span>
          </button>

          <button
            class="knowledge-base-btn"
            :class="{ active: enableKnowledgeBase, disabled: isStreaming || disabled }"
            :disabled="isStreaming || disabled"
            @click="enableKnowledgeBase = !enableKnowledgeBase"
            title="知识库检索"
          >
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path>
              <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path>
            </svg>
            <span class="knowledge-base-label">知识库</span>
          </button>
        </div>
        
        <div class="right-actions">
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
            v-else
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
    
    <div class="input-footer">
      <span class="footer-text">WuKong 信息由AI生成，仅供参考</span>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, watch, nextTick, onUnmounted, reactive } from 'vue'
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
  }
})

const emit = defineEmits(['send', 'stop', 'createSession'])

const message = ref('')
const textareaRef = ref(null)
const uploadedFiles = ref([])
const enableDeepThink = ref(true)
const enableKnowledgeBase = ref(false)
let abortController = null

const canSend = computed(() => {
  return message.value.trim() || uploadedFiles.value.length > 0
})

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
  
  emit('send', message.value.trim().replace(/\s+/g, ' '), filesToSend, abortController.signal, enableDeepThink.value, enableKnowledgeBase.value)
  
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

onUnmounted(() => {
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
  background: #fefefe;
}

.left-actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.divider {
  color: #e2e8f0;
  font-size: 14px;
  user-select: none;
}

.deep-think-btn {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 6px 12px;
  height: auto;
  min-height: 32px;
  border: 1px solid #e2e8f0;
  background: #ffffff;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s ease;
  white-space: nowrap;
}

.deep-think-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.deep-think-btn svg {
  width: 16px;
  height: 16px;
  color: #64748b;
}

.deep-think-btn.active svg {
  color: white;
}

.deep-think-label {
  font-size: 12px;
  color: #64748b;
}

.deep-think-btn.active {
  background: #3b82f6;
  border-color: #3b82f6;
}

.deep-think-btn.active svg,
.deep-think-btn.active .deep-think-label {
  color: white;
}

.deep-think-btn.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.knowledge-base-btn {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 6px 12px;
  height: auto;
  min-height: 32px;
  border: 1px solid #e2e8f0;
  background: #ffffff;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s ease;
  white-space: nowrap;
}

.knowledge-base-btn svg {
  width: 16px;
  height: 16px;
  color: #64748b;
}

.knowledge-base-btn.active svg {
  color: white;
}

.knowledge-base-label {
  font-size: 12px;
  color: #64748b;
}

.knowledge-base-btn.active {
  background: #3b82f6;
  border-color: #3b82f6;
}

.knowledge-base-btn.active .knowledge-base-label {
  color: white;
}

.knowledge-base-btn.disabled {
  opacity: 0.5;
  cursor: not-allowed;
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
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s ease;
}

.stop-btn svg {
  width: 18px;
  height: 18px;
  color: white;
}

.stop-btn:hover {
  transform: scale(1.05);
  box-shadow: 0 2px 8px rgba(239, 68, 68, 0.3);
}

.upload-btn {
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

.upload-btn svg {
  width: 18px;
  height: 18px;
  color: #64748b;
  transition: all 0.2s;
}

.upload-btn:hover:not(.disabled) {
  background: #f1f5f9;
}

.upload-btn:hover:not(.disabled) svg {
  color: #0ea5e9;
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
