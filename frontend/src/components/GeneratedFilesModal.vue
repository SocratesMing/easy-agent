<template>
  <div v-if="visible" class="modal-overlay" @click.self="close">
    <div class="modal-content generated-files-modal">
      <div class="modal-header">
        <h3>生成的文件</h3>
        <button class="close-btn" @click="close">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <line x1="18" y1="6" x2="6" y2="18"></line>
            <line x1="6" y1="6" x2="18" y2="18"></line>
          </svg>
        </button>
      </div>
      
      <div class="modal-body">
        <div class="file-list">
          <div
            v-for="file in files"
            :key="file.id"
            class="file-item"
            :class="{ active: selectedFile?.id === file.id }"
            @click="selectFile(file)"
          >
            <div class="file-icon">
              <FileIcon :filename="file.filename" :size="24" />
            </div>
            <div class="file-info">
              <div class="file-name">{{ file.filename }}</div>
              <div class="file-meta">{{ formatFileSize(file.size) }} · {{ formatTime(file.created_at) }}</div>
            </div>
          </div>
          <div v-if="files.length === 0" class="empty-list">
            暂无生成的文件
          </div>
        </div>
        
        <div class="file-preview">
          <div v-if="selectedFile" class="preview-content">
            <div class="preview-header">
              <span class="preview-filename">{{ selectedFile.filename }}</span>
            </div>
            <div class="preview-body">
              <pre><code>{{ fileContent }}</code></pre>
            </div>
          </div>
          <div v-else class="preview-empty">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
              <polyline points="14 2 14 8 20 8"></polyline>
            </svg>
            <span>选择一个文件查看内容</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import FileIcon from './FileIcon.vue'

const props = defineProps({
  visible: {
    type: Boolean,
    default: false
  },
  files: {
    type: Array,
    default: () => []
  }
})

const emit = defineEmits(['close'])

const selectedFile = ref(null)
const fileContent = ref('')

function close() {
  emit('close')
  selectedFile.value = null
  fileContent.value = ''
}

async function selectFile(file) {
  selectedFile.value = file
  try {
    const response = await fetch(`/api/files/content?file_path=${encodeURIComponent(file.file_path)}`)
    if (response.ok) {
      fileContent.value = await response.text()
    } else {
      fileContent.value = '无法加载文件内容'
    }
  } catch (e) {
    fileContent.value = '加载文件内容失败: ' + e.message
  }
}

function formatFileSize(size) {
  if (size < 1024) return size + ' B'
  if (size < 1024 * 1024) return (size / 1024).toFixed(1) + ' KB'
  return (size / 1024 / 1024).toFixed(1) + ' MB'
}

function formatTime(timeStr) {
  if (!timeStr) return ''
  const date = new Date(timeStr)
  return date.toLocaleString('zh-CN', { 
    month: '2-digit', 
    day: '2-digit', 
    hour: '2-digit', 
    minute: '2-digit' 
  })
}

watch(() => props.files, () => {
  if (props.files.length > 0 && !selectedFile.value) {
    selectFile(props.files[0])
  }
})
</script>

<style scoped>
.modal-overlay {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  background: white;
  border-radius: 12px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.2);
  max-width: 90vw;
  max-height: 80vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}

.generated-files-modal {
  width: 900px;
  height: 600px;
}

.modal-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid #e5e7eb;
}

.modal-header h3 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
  color: #1f2937;
}

.close-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  color: #6b7280;
}

.close-btn:hover {
  background: #f3f4f6;
  color: #1f2937;
}

.close-btn svg {
  width: 20px;
  height: 20px;
}

.modal-body {
  display: flex;
  flex: 1;
  overflow: hidden;
}

.file-list {
  width: 300px;
  border-right: 1px solid #e5e7eb;
  overflow-y: auto;
  padding: 8px;
}

.file-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 12px;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s;
}

.file-item:hover {
  background: #f3f4f6;
}

.file-item.active {
  background: #e0f2fe;
}

.file-icon {
  flex-shrink: 0;
}

.file-info {
  flex: 1;
  min-width: 0;
}

.file-name {
  font-size: 14px;
  font-weight: 500;
  color: #1f2937;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.file-meta {
  font-size: 12px;
  color: #6b7280;
  margin-top: 2px;
}

.empty-list {
  text-align: center;
  padding: 40px 20px;
  color: #9ca3af;
}

.file-preview {
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
  padding: 12px 16px;
  border-bottom: 1px solid #e5e7eb;
}

.preview-filename {
  font-size: 14px;
  font-weight: 500;
  color: #1f2937;
}

.preview-body {
  flex: 1;
  overflow: auto;
  padding: 16px;
}

.preview-body pre {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
}

.preview-body code {
  font-family: 'Consolas', 'Monaco', monospace;
  font-size: 13px;
  line-height: 1.5;
  color: #374151;
}

.preview-empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #9ca3af;
}

.preview-empty svg {
  width: 48px;
  height: 48px;
  margin-bottom: 12px;
}
</style>
