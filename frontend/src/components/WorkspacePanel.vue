<template>
  <div
    class="workspace-panel"
    :class="{ collapsed: !visible, 'is-resizing': isResizing }"
    :style="panelStyle"
  >
    <div
      v-if="visible"
      class="wp-resizer"
      title="拖动调整宽度"
      @mousedown="startResize"
    ></div>
    <template v-if="renderContent">
      <div class="wp-header">
        <div class="wp-title">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" class="wp-header-icon">
            <path d="M3 7V17C3 18.1046 3.89543 19 5 19H19C20.1046 19 21 18.1046 21 17V9C21 7.89543 20.1046 7 19 7H13L11 5H5C3.89543 5 3 5.89543 3 7Z" fill="#eab308" stroke="#ca8a04" stroke-width="1.5"></path>
            <path d="M3 10H21" stroke="#ca8a04" stroke-width="1.5"></path>
          </svg>
          <span>工作区</span>
        </div>
        <!-- 两个按钮成组靠右：header 用 space-between，若各自作为直接子元素，
             刷新会被挤到「标题」与「收起」之间的正中位置 -->
        <div class="wp-actions">
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
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" class="wp-empty-icon">
            <path d="M3 7V17C3 18.1046 3.89543 19 5 19H19C20.1046 19 21 18.1046 21 17V9C21 7.89543 20.1046 7 19 7H13L11 5H5C3.89543 5 3 5.89543 3 7Z" fill="#fbbf24" stroke="#f59e0b" stroke-width="1.5"></path>
          </svg>
          <span class="wp-center-text">工作区为空</span>
          <span class="wp-empty-hint">Agent 生成或修改的文件会显示在这里</span>
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
import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
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

// ── 面板宽度（可拖拽，宽度持久化到 localStorage）──────────────────
const WIDTH_KEY = 'workspace_panel_width'
const WIDTH_MIN = 220
const WIDTH_MAX = 640
const WIDTH_DEFAULT = 280

const panelWidth = ref(
  Number(localStorage.getItem(WIDTH_KEY)) || WIDTH_DEFAULT
)
const isResizing = ref(false)

const panelStyle = computed(() => ({
  width: props.visible ? `${panelWidth.value}px` : '0px',
}))

function startResize(e) {
  isResizing.value = true
  e.preventDefault()
  document.addEventListener('mousemove', onResize)
  document.addEventListener('mouseup', stopResize)
  document.body.style.cursor = 'col-resize'
  document.body.style.userSelect = 'none'
}

function onResize(e) {
  if (!isResizing.value) return
  // 面板贴右侧，宽度 = 视口右边缘到鼠标的距离
  const next = window.innerWidth - e.clientX
  panelWidth.value = Math.min(WIDTH_MAX, Math.max(WIDTH_MIN, next))
}

function stopResize() {
  if (!isResizing.value) return
  isResizing.value = false
  document.removeEventListener('mousemove', onResize)
  document.removeEventListener('mouseup', stopResize)
  document.body.style.cursor = ''
  document.body.style.userSelect = ''
  localStorage.setItem(WIDTH_KEY, String(Math.round(panelWidth.value)))
}

onBeforeUnmount(stopResize)

// 折叠时延迟卸载内容：让内容跟着宽度一起被 overflow 裁进去，
// 否则会变成「内容先消失、再收起一条空白面板」，观感很生硬
const ANIM_MS = 250
const renderContent = ref(props.visible)
let unloadTimer = null
watch(() => props.visible, (v) => {
  clearTimeout(unloadTimer)
  if (v) {
    renderContent.value = true
  } else {
    unloadTimer = setTimeout(() => { renderContent.value = false }, ANIM_MS)
  }
})
onBeforeUnmount(() => clearTimeout(unloadTimer))

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
  const url = `${API_BASE_URL}/agent/files/preview?${params.toString()}`
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
/* 宽度由 JS 内联 style 控制（可拖拽 + 记忆），这里的 width 仅作兜底 */
.workspace-panel {
  position: relative;
  width: 280px;
  display: flex;
  flex-direction: column;
  background: #ffffff;
  border-left: 1px solid var(--border-color, #e2e8f0);
  height: 100%;
  overflow: hidden;
  /* 展开/折叠动画：宽度由 JS 内联 style 设置，这里负责补间 */
  transition: width 0.25s ease;
}

.workspace-panel.collapsed {
  width: 0;
  border-left: none;
  overflow: hidden;
}

/* 拖拽把手：贴在面板左边缘，hover 时高亮提示可拖动 */
.wp-resizer {
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 5px;
  cursor: col-resize;
  z-index: 2;
  background: transparent;
  transition: background 0.15s ease;
}

.wp-resizer:hover,
.workspace-panel.is-resizing .wp-resizer {
  background: #0ea5e9;
}

/* 拖拽中：禁止整页选中文字；同时关掉宽度过渡，否则拖动会有明显的延迟跟随感 */
.workspace-panel.is-resizing {
  user-select: none;
  transition: none;
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
  min-width: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.wp-title span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* 右侧按钮组：与文件树的 6px 圆角保持同一套视觉语言 */
.wp-actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.wp-header-icon {
  width: 20px;
  height: 20px;
  /* 彩色文件夹图标与文件树保持同一套视觉语言，不再跟随文字色 */
  flex-shrink: 0;
}

.wp-collapse-btn {
  background: none;
  border: none;
  padding: 5px;
  cursor: pointer;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  transition: all 0.15s;
}

.wp-collapse-btn:hover {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.wp-collapse-btn svg {
  width: 18px;
  height: 18px;
}

.wp-refresh-btn {
  background: none;
  border: none;
  padding: 5px;
  cursor: pointer;
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  transition: all 0.15s;
}

.wp-refresh-btn:hover:not(:disabled) {
  background: var(--bg-tertiary);
  color: var(--text-secondary);
}

.wp-refresh-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}

/* 与收起按钮同尺寸：原 16px 比收起的 18px 小一圈，两个按钮并排时不配套 */
.wp-refresh-btn svg {
  width: 18px;
  height: 18px;
}

.wp-refresh-btn svg.spinning {
  animation: spin 0.8s linear infinite;
}

.wp-content {
  flex: 1;
  overflow-y: auto;
  padding: 6px 8px;
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
  color: var(--text-secondary);
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
  width: 56px;
  height: 56px;
  /* 彩色图标用透明度淡化，空态不喧宾夺主 */
  opacity: 0.45;
}

.wp-empty-hint {
  max-width: 200px;
  font-size: 12px;
  line-height: 1.5;
  text-align: center;
  color: var(--text-secondary);
  opacity: 0.75;
}
/* 展开时内容淡入，避免宽度到位后内容「啪」地一下出现 */
@keyframes wp-fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

.wp-header,
.wp-content {
  animation: wp-fade-in 0.22s ease both;
}

/* 尊重系统的「减少动态效果」设置 */
@media (prefers-reduced-motion: reduce) {
  .workspace-panel {
    transition: none;
  }

  .wp-header,
  .wp-content {
    animation: none;
  }
}
</style>
