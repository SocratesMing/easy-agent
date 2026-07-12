<template>
  <div class="workspace-panel" :class="{ collapsed: !visible }">
    <template v-if="visible">
      <div class="wp-header">
        <div class="wp-title">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="wp-header-icon">
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
          </svg>
          <span>工作区</span>
        </div>
        <button class="wp-refresh-btn" @click="refresh" title="刷新工作区" :disabled="isLoading">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{ spinning: isLoading }">
            <polyline points="23 4 23 10 17 10"></polyline>
            <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
          </svg>
        </button>
        <button class="wp-collapse-btn" @click="$emit('toggle')" title="收起工作区">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="15 18 9 12 15 6"></polyline>
          </svg>
        </button>
      </div>

      <div class="wp-content">
        <div v-if="isLoading" class="wp-center">
          <div class="wp-spinner"></div>
          <span class="wp-center-text">加载中...</span>
        </div>

        <div v-else-if="error" class="wp-center">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="wp-error-icon">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="12" y1="8" x2="12" y2="12"></line>
            <line x1="12" y1="16" x2="12.01" y2="16"></line>
          </svg>
          <span class="wp-center-text wp-error-text">{{ error }}</span>
          <button class="wp-retry-btn" @click="refresh">重试</button>
        </div>

        <div v-else-if="workspaceTreeData.length === 0" class="wp-center">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1" class="wp-empty-icon">
            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"></path>
          </svg>
          <span class="wp-center-text">工作区为空</span>
        </div>

        <div v-else class="wp-file-tree">
          <FileTreeNode
            v-for="item in workspaceTreeData"
            :key="item.id"
            :item="item"
            :selectedId="selectedFile?.id"
            :depth="0"
            :sessionId="currentSessionId"
            @select="handleSelectFile"
          @download="handleDownloadFile"
          />
        </div>
      </div>
    </template>

    <FilePreview
      :filename="previewFile?.name || ''"
      :filePath="previewFile?.file_path || previewFile?.path || ''"
      :sessionId="currentSessionId"
      :visible="showPreview"
      @close="showPreview = false"
    />
  </div>
</template>

<script setup>
import { API_BASE_URL } from '../config.js'
import { ref, watch, onMounted } from 'vue'
import FileTreeNode from './FileTreeNode.vue'
import FilePreview from './FilePreview.vue'
import { getWorkspaceTree } from '../api/files'
import { getStoredToken } from '../api/auth.js'

const props = defineProps({
  username: { type: String, default: '' },
  currentSessionId: { type: String, default: null },
  isStreaming: { type: Boolean, default: false },
  visible: { type: Boolean, default: true },
})

const emit = defineEmits(['toggle'])

const workspaceTreeData = ref([])
const selectedFile = ref(null)
const previewFile = ref(null)
const showPreview = ref(false)
const isLoading = ref(false)
const error = ref(null)

async function buildWorkspaceTree() {
  if (!props.currentSessionId) {
    workspaceTreeData.value = []
    return
  }
  isLoading.value = true
  error.value = null
  try {
    const response = await getWorkspaceTree('', props.currentSessionId)
    workspaceTreeData.value = (response.items || []).map(item => ({
      id: item.path,
      name: item.name,
      type: item.type,
      size: item.size,
      file_type: item.type === 'file' ? item.name.split('.').pop().toLowerCase() : '',
      file_path: item.path,
    }))
  } catch (e) {
    error.value = '加载工作区失败: ' + e.message
    workspaceTreeData.value = []
  } finally {
    isLoading.value = false
  }
}

function handleSelectFile(file) {
  selectedFile.value = file
  previewFile.value = file
  showPreview.value = true
}

function handleDownloadFile(file) {
  const filePath = file.file_path || file.path
  const token = getStoredToken()
  const params = new URLSearchParams()
  params.set('file_path', filePath)
  params.set('session_id', props.currentSessionId)
  params.set('download', 'true')
  if (token) params.set('token', token)
  const url = `${API_BASE_URL}/api/files/preview?${params.toString()}`
  const link = document.createElement('a')
  link.href = url
  link.download = file.name
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
}

function refresh() {
  buildWorkspaceTree()
}

watch(() => props.isStreaming, (newVal, oldVal) => {
  if (oldVal === true && newVal === false) {
    setTimeout(() => refresh(), 300)
  }
})

watch(() => props.currentSessionId, () => {
  refresh()
})

watch(() => props.visible, (newVal) => {
  if (newVal) {
    refresh()
  }
})

onMounted(() => {
  buildWorkspaceTree()
})
</script>

<style scoped>
.workspace-panel {
  width: 260px;
  display: flex;
  flex-direction: column;
  background: #ffffff;
  border-left: 1px solid #e2e8f0;
  height: 100vh;
  overflow: hidden;
  box-shadow: 4px 0 16px rgba(0, 0, 0, 0.06);
}

.workspace-panel.collapsed {
  width: 0;
  border-left: none;
  overflow: hidden;
}

.wp-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid #e2e8f0;
  flex-shrink: 0;
}

.wp-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
}

.wp-header-icon {
  width: 18px;
  height: 18px;
  color: #64748b;
}

.wp-collapse-btn {
  background: none;
  border: none;
  padding: 4px;
  cursor: pointer;
  color: #94a3b8;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  transition: all 0.15s;
}

.wp-collapse-btn:hover {
  background: #f1f5f9;
  color: #475569;
}

.wp-collapse-btn svg {
  width: 18px;
  height: 18px;
}

.wp-refresh-btn {
  background: none;
  border: none;
  padding: 4px;
  cursor: pointer;
  color: #94a3b8;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  transition: all 0.15s;
}

.wp-refresh-btn:hover:not(:disabled) {
  background: #f1f5f9;
  color: #475569;
}

.wp-refresh-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

.wp-refresh-btn svg {
  width: 16px;
  height: 16px;
}

.wp-refresh-btn svg.spinning {
  animation: spin 0.8s linear infinite;
}

.wp-content {
  flex: 1;
  overflow-y: auto;
  padding: 4px;
}

.wp-file-tree {
  width: 100%;
}

.wp-center {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 24px;
  gap: 12px;
  height: 100%;
}

.wp-spinner {
  width: 24px;
  height: 24px;
  border: 3px solid #e2e8f0;
  border-top-color: #166534;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.wp-center-text {
  font-size: 13px;
  color: #94a3b8;
  text-align: center;
}

.wp-error-icon {
  width: 32px;
  height: 32px;
  color: #ef4444;
}

.wp-error-text {
  color: #ef4444;
}

.wp-retry-btn {
  padding: 6px 16px;
  font-size: 12px;
  color: #166534;
  background: #f0fdf4;
  border: 1px solid #86efac;
  border-radius: 6px;
  cursor: pointer;
  transition: all 0.2s;
}

.wp-retry-btn:hover {
  background: #dcfce7;
}

.wp-empty-icon {
  width: 40px;
  height: 40px;
  color: #cbd5e1;
}
</style>
