<template>
  <div class="assets-panel">
    <div class="assets-header">
      <h2>我的资产</h2>
      <div class="header-actions">
        <label class="upload-btn" :class="{ disabled: uploading }">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
            <polyline points="17 8 12 3 7 8"></polyline>
            <line x1="12" y1="3" x2="12" y2="15"></line>
          </svg>
          上传文件
          <input
            type="file"
            @change="handleUpload"
            multiple
            :disabled="uploading"
            hidden
          />
        </label>
        <button @click="refreshAssets" class="refresh-btn" :disabled="loading || uploading">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{ spinning: loading }">
            <polyline points="23 4 23 10 17 10"></polyline>
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
          </svg>
          刷新
        </button>
      </div>
    </div>

    <div class="tabs">
      <button
        v-for="(count, category) in categoryCounts"
        :key="category"
        class="tab"
        :class="{ active: activeTab === category }"
        @click="activeTab = category"
      >
        <span class="tab-icon">{{ getCategoryIcon(category) }}</span>
        <span class="tab-name">{{ category }}</span>
        <span class="tab-count">{{ count }}</span>
      </button>
    </div>

    <div class="assets-content">
      <div v-if="loading" class="loading-state">
        <div class="spinner"></div>
        <span>加载中...</span>
      </div>

      <div v-else-if="totalFiles === 0" class="empty-state">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
        </svg>
        <h3>暂无文件</h3>
        <p>在会话中上传文件后，将在这里显示</p>
      </div>

      <div v-else class="files-grid">
        <div
          v-for="file in currentFiles"
          :key="file.file_path"
          class="file-card"
          @dblclick="handlePreview(file)"
        >
          <div class="file-icon" :class="getCategoryClass(file.category)">
            <FileIcon :filename="file.filename" :size="48" />
          </div>
          <div class="file-info">
            <div class="file-name" :title="file.filename">{{ file.filename }}</div>
            <div class="file-meta">
              <span class="file-size">{{ formatSize(file.size) }}</span>
              <span class="file-type-badge">{{ getFileTypeLabel(file.filename) }}</span>
            </div>
          </div>
          <div class="file-actions">
            <div class="dropdown" @mouseleave="closeDropdown">
              <button class="file-action more-action" @click="toggleDropdown(file.file_path)" title="更多操作">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <circle cx="12" cy="5" r="1.5"></circle>
                  <circle cx="12" cy="12" r="1.5"></circle>
                  <circle cx="12" cy="19" r="1.5"></circle>
                </svg>
              </button>
              <div v-if="activeDropdown === file.file_path" class="dropdown-menu">
                <button class="dropdown-item" @click="handleDownload(file)">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                    <polyline points="7 10 12 15 17 10"></polyline>
                    <line x1="12" y1="15" x2="12" y2="3"></line>
                  </svg>
                  下载
                </button>
                <button class="dropdown-item" @click="handleCopyPath(file.file_path)">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
                  </svg>
                  复制路径
                </button>
                <button class="dropdown-item delete-item" @click="handleDelete(file)">
                  <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                    <polyline points="3 6 5 6 21 6"></polyline>
                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path>
                  </svg>
                  删除
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <ConfirmDialog
    ref="confirmDialog"
    title="确认删除"
    message="确定要删除此文件吗？此操作不可恢复。"
    confirm-text="删除"
    cancel-text="取消"
    type="danger"
  />

  <FilePreview
    ref="previewDialog"
    :filename="previewFile.filename"
    :file-path="previewFile.filePath"
    :visible="previewFile.visible"
    @close="previewFile.visible = false"
  />
</template>

<script setup>
import { API_BASE_URL } from '../config.js'
import { ref, computed, onMounted, watch, onActivated } from 'vue'
import { getAllFiles, deleteFile } from '../api/files.js'
import FileIcon from './FileIcon.vue'
import ConfirmDialog from './ConfirmDialog.vue'
import FilePreview from './FilePreview.vue'

const props = defineProps({
  visible: {
    type: Boolean,
    default: true
  }
})

const loading = ref(false)
const allFiles = ref([])
const confirmDialog = ref(null)
const activeTab = ref('全部')
const uploading = ref(false)
const activeDropdown = ref(null)
const previewDialog = ref(null)
const previewFile = ref({ filename: '', filePath: '', visible: false })
const emit = defineEmits(['close'])

function toggleDropdown(filePath) {
  activeDropdown.value = activeDropdown.value === filePath ? null : filePath
}

function closeDropdown() {
  activeDropdown.value = null
}

const categories = ['全部', '文档', '图片', '代码', '数据', '其他']

const categoryIcons = {
  '全部': '📁',
  '文档': '📄',
  '图片': '🖼️',
  '代码': '💻',
  '数据': '📊',
  '其他': '📎'
}

function getCategoryIcon(category) {
  return categoryIcons[category] || '📎'
}

function getCategoryClass(category) {
  const classes = {
    '文档': 'doc',
    '图片': 'image',
    '代码': 'code',
    '数据': 'data',
    '其他': 'other'
  }
  return classes[category] || 'other'
}

function getFileTypeLabel(filename) {
  const ext = filename.split('.').pop().toUpperCase()
  return ext
}

function getFileCategory(filename) {
  const ext = filename.split('.').pop().toLowerCase()
  const categoryMap = {
    '文档': ['pdf', 'doc', 'docx', 'txt', 'md', 'xls', 'xlsx', 'ppt', 'pptx', 'rtf', 'odt'],
    '图片': ['jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg', 'webp', 'ico', 'tiff'],
    '代码': ['py', 'js', 'ts', 'vue', 'html', 'css', 'json', 'xml', 'java', 'go', 'rs', 'c', 'cpp', 'h', 'sh', 'bat'],
    '数据': ['csv', 'sql', 'db', 'sqlite', 'parquet', 'avro'],
  }
  
  for (const [cat, exts] of Object.entries(categoryMap)) {
    if (exts.includes(ext)) return cat
  }
  return '其他'
}

const filesWithCategory = computed(() => {
  return allFiles.value.map(file => ({
    ...file,
    category: getFileCategory(file.filename)
  }))
})

const categoryCounts = computed(() => {
  const counts = { '全部': filesWithCategory.value.length }
  for (const file of filesWithCategory.value) {
    counts[file.category] = (counts[file.category] || 0) + 1
  }
  return counts
})

const totalFiles = computed(() => filesWithCategory.value.length)

const currentFiles = computed(() => {
  if (activeTab.value === '全部') {
    return filesWithCategory.value
  }
  return filesWithCategory.value.filter(f => f.category === activeTab.value)
})

async function refreshAssets() {
  loading.value = true
  try {
    const { getAuthHeaders } = await import('../api/auth.js')
    const response = await fetch(`${API_BASE_URL}/api/files/list`, {
      headers: {
        ...getAuthHeaders()
      }
    })
    const data = await response.json()
    allFiles.value = data.files || []
  } catch (e) {
    console.error('获取资产失败:', e)
  } finally {
    loading.value = false
  }
}

async function handleUpload(event) {
  const files = Array.from(event.target.files)
  if (files.length === 0) return

  uploading.value = true

  const { getAuthHeaders } = await import('../api/auth.js')

  for (const file of files) {
    try {
      const formData = new FormData()
      formData.append('file', file)

      const response = await fetch(`${API_BASE_URL}/api/files/upload`, {
        method: 'POST',
        headers: {
          ...getAuthHeaders()
        },
        body: formData
      })

      if (response.ok) {
        console.log('文件上传成功:', file.name)
      } else {
        console.error('文件上传失败:', file.name)
      }
    } catch (e) {
      console.error('上传文件失败:', e)
    }
  }

  uploading.value = false
  event.target.value = ''

  await refreshAssets()
}

function formatSize(bytes) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

async function handleCopyPath(path) {
  try {
    await navigator.clipboard.writeText(path)
  } catch (e) {
    console.error('复制失败:', e)
  }
  closeDropdown()
}

function handleDownload(file) {
  const url = `${API_BASE_URL}/api/files/download/${file.file_path}`
  const link = document.createElement('a')
  link.href = url
  link.download = file.filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  closeDropdown()
}

async function handleDelete(file) {
  closeDropdown()

  const confirmed = await confirmDialog.value.show()
  if (!confirmed) {
    return
  }

  try {
    const { getAuthHeaders } = await import('../api/auth.js')
    const response = await fetch(`${API_BASE_URL}/api/files/users/files/${encodeURIComponent(file.id)}`, {
      method: 'DELETE',
      headers: {
        ...getAuthHeaders()
      }
    })

    if (response.ok) {
      console.log('文件删除成功:', file.filename)
      await refreshAssets()
    } else {
      const error = await response.json()
      console.error('文件删除失败:', error)
      alert('删除失败: ' + (error.detail || '未知错误'))
    }
  } catch (e) {
    console.error('删除文件失败:', e)
    alert('删除失败: ' + e.message)
  }
}

function handlePreview(file) {
  previewFile.value = {
    filename: file.filename,
    filePath: file.file_path,
    visible: true
  }
}

onMounted(() => {
  refreshAssets()
})

watch(() => props.visible, (newVal) => {
  if (newVal) {
    refreshAssets()
  }
})
</script>

<style scoped>
.assets-panel {
  flex: 1;
  display: flex;
  flex-direction: column;
  background: transparent;
  height: 100%;
}

.assets-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20px 24px;
  background: transparent;
  border-bottom: 1px solid #e2e8f0;
}

.assets-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: #1e293b;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.upload-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border: 1px solid #e2e8f0;
  background: #f1f5f9;
  border-radius: 8px;
  font-size: 14px;
  color: #475569;
  cursor: pointer;
  transition: all 0.2s;
}

.upload-btn:hover:not(.disabled) {
  background: #e2e8f0;
}

.upload-btn.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.upload-btn svg {
  width: 16px;
  height: 16px;
}

.refresh-btn {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border: 1px solid #e2e8f0;
  background: #f1f5f9;
  border-radius: 8px;
  font-size: 14px;
  color: #475569;
  cursor: pointer;
  transition: all 0.2s;
}

.refresh-btn:hover:not(:disabled) {
  background: #e2e8f0;
}

.refresh-btn svg {
  width: 16px;
  height: 16px;
}

.refresh-btn svg.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.tabs {
  display: flex;
  gap: 8px;
  padding: 16px 24px;
  background: transparent;
  border-bottom: 1px solid #e2e8f0;
  overflow-x: auto;
}

.tab {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  border: 1px solid #e2e8f0;
  background: transparent;
  border-radius: 8px;
  font-size: 14px;
  color: #64748b;
  cursor: pointer;
  transition: all 0.2s;
  white-space: nowrap;
}

.tab:hover {
  background: #f1f5f9;
  border-color: #cbd5e1;
}

.tab.active {
  background: #0ea5e9;
  border-color: #0ea5e9;
  color: white;
}

.tab-icon {
  font-size: 16px;
}

.tab-name {
  font-weight: 500;
}

.tab-count {
  background: rgba(0, 0, 0, 0.1);
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
}

.tab.active .tab-count {
  background: rgba(255, 255, 255, 0.2);
}

.assets-content {
  flex: 1;
  overflow-y: auto;
  padding: 24px;
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  color: #94a3b8;
  gap: 16px;
}

.spinner {
  width: 40px;
  height: 40px;
  border: 3px solid #e2e8f0;
  border-top-color: #0ea5e9;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  color: #94a3b8;
}

.empty-state svg {
  width: 64px;
  height: 64px;
  margin-bottom: 16px;
  opacity: 0.5;
}

.empty-state h3 {
  margin: 0 0 8px 0;
  font-size: 18px;
  font-weight: 500;
  color: #64748b;
}

.empty-state p {
  margin: 0;
  font-size: 14px;
}

.files-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 16px;
}

.file-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px;
  background: white;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  transition: all 0.2s;
  cursor: pointer;
}

.file-card:hover {
  border-color: #cbd5e1;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05);
}

.file-icon {
  width: 56px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.file-icon.doc {
  background: transparent;
}

.file-icon.image {
  background: transparent;
}

.file-icon.code {
  background: transparent;
}

.file-icon.data {
  background: transparent;
}

.file-icon.other {
  background: transparent;
}

.file-info {
  flex: 1;
  min-width: 0;
}

.file-name {
  font-size: 14px;
  font-weight: 500;
  color: #1e293b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  margin-bottom: 4px;
}

.file-meta {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: #94a3b8;
  align-items: center;
}

.file-type-badge {
  background: transparent;
  color: #475569;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 10px;
  font-weight: 600;
  text-transform: uppercase;
}

.file-session {
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.file-actions {
  position: relative;
}

.dropdown {
  position: relative;
}

.file-action {
  width: 32px;
  height: 32px;
  border: none;
  background: transparent;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.2s;
}

.file-action:hover {
  background: #f1f5f9;
}

.file-action.more-action {
  opacity: 1;
}

.file-action.more-action svg {
  width: 20px;
  height: 20px;
}

.file-action svg {
  width: 16px;
  height: 16px;
  color: #64748b;
}

.dropdown-menu {
  position: absolute;
  right: 0;
  top: 100%;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  min-width: 120px;
  z-index: 100;
  padding: 4px;
}

.dropdown-item {
  width: 100%;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  border: none;
  background: transparent;
  border-radius: 6px;
  font-size: 14px;
  color: #334155;
  cursor: pointer;
  transition: all 0.2s;
}

.dropdown-item:hover {
  background: #f1f5f9;
}

.dropdown-item.delete-item {
  color: #ef4444;
}

.dropdown-item.delete-item:hover {
  background: #fee2e2;
}

.dropdown-item svg {
  width: 16px;
  height: 16px;
}
</style>
