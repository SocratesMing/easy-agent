<template>
  <div v-if="item" class="tree-node">
    <div
      class="tree-item"
      :class="{
        active: item.type === 'file' && selectedId === item.id,
        'is-directory': item.type === 'directory'
      }"
      :style="{ paddingLeft: depth * 16 + 'px' }"
      @click="handleClick"
    >
      <span v-if="item.type === 'directory'" class="folder-icon" @click.stop="toggleExpand">
        <svg v-if="isLoading" class="spinner" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
          <path d="M3 7V17C3 18.1046 3.89543 19 5 19H19C20.1046 19 21 18.1046 21 17V9C21 7.89543 20.1046 7 19 7H13L11 5H5C3.89543 5 3 5.89543 3 7Z" fill="#fbbf24" stroke="#f59e0b" stroke-width="1.5"/>
        </svg>
        <svg v-else-if="expanded" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
          <path d="M3 7V17C3 18.1046 3.89543 19 5 19H19C20.1046 19 21 18.1046 21 17V9C21 7.89543 20.1046 7 19 7H13L11 5H5C3.89543 5 3 5.89543 3 7Z" fill="#eab308" stroke="#ca8a04" stroke-width="1.5"/>
          <path d="M3 10H21" stroke="#ca8a04" stroke-width="1.5"/>
        </svg>
        <svg v-else xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
          <path d="M3 7V17C3 18.1046 3.89543 19 5 19H19C20.1046 19 21 18.1046 21 17V9C21 7.89543 20.1046 7 19 7H13L11 5H5C3.89543 5 3 5.89543 3 7Z" fill="#fbbf24" stroke="#f59e0b" stroke-width="1.5"/>
        </svg>
      </span>
      <span v-else class="file-icon">
        <FileIcon :filename="item.name" :size="16" />
      </span>
      <span class="tree-item-name">{{ item.name }}</span>
    </div>
    <div v-if="item.type === 'directory' && expanded && children.length > 0" class="tree-children">
      <FileTreeNode
        v-for="child in children"
        :key="child.id"
        :item="child"
        :selectedId="selectedId"
        :depth="depth + 1"
        :sessionId="sessionId"
        :taskId="taskId"
        @select="$emit('select', $event)"
        @download="$emit('download', $event)"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, watch } from 'vue'
import FileIcon from './FileIcon.vue'
import { getWorkspaceTree } from '../api/files'
import { getScheduledTaskWorkspace } from '../api/scheduledTasks'

const props = defineProps({
  item: {
    type: Object,
    required: true
  },
  selectedId: {
    type: String,
    default: null
  },
  depth: {
    type: Number,
    default: 0
  },
  sessionId: {
    type: String,
    default: null
  },
  taskId: {
    type: String,
    default: null
  }
})
// 兼容两种文件树来源：会话工作区或定时任务工作区
const loadTree = (path, sessionId, taskId) =>
  taskId
    ? getScheduledTaskWorkspace(path, taskId)
    : getWorkspaceTree(path, sessionId)

const emit = defineEmits(['select', 'download'])

const expanded = ref(false)
const children = ref([])
const isLoading = ref(false)
let clickTimer = null

async function toggleExpand() {
  if (expanded.value) {
    expanded.value = false
    return
  }

  expanded.value = true

  // 如果已经有子节点数据，不需要再次加载
  if (children.value.length > 0) return

  // 懒加载子目录
  await loadChildren()
}

async function loadChildren() {
  if (props.item.type !== 'directory') return

  isLoading.value = true
  try {
    const response = await loadTree(props.item.file_path, props.sessionId, props.taskId)
    children.value = (response.items || []).map(item => ({
      id: item.path,
      name: item.name,
      type: item.type,
      size: item.size,
      file_type: item.type === 'file' ? item.name.split('.').pop().toLowerCase() : '',
      file_path: item.path,
    }))
  } catch (e) {
    console.error('加载子目录失败:', e)
    children.value = []
  } finally {
    isLoading.value = false
  }
}

function handleClick() {
  if (props.item.type === 'directory') {
    toggleExpand()
    return
  }
  // 文件：单击预览，双击下载
  if (clickTimer) {
    clearTimeout(clickTimer)
    clickTimer = null
    // 双击：下载
    emit('download', props.item)
  } else {
    clickTimer = setTimeout(() => {
      clickTimer = null
      // 单击：预览
      emit('select', props.item)
    }, 250)
  }
}
</script>

<style scoped>
.tree-node {
  user-select: none;
}

.tree-item {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 8px;
  cursor: pointer;
  border-radius: 4px;
  transition: background 0.15s ease;
}

.tree-item:hover {
  background: var(--bg-tertiary);
}

.tree-item.active {
  background: color-mix(in srgb, var(--accent-color) 18%, transparent);
}

.folder-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 18px;
  flex-shrink: 0;
}

.folder-icon svg {
  width: 18px;
  height: 18px;
}

.folder-icon .spinner {
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

.file-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.tree-item-name {
  font-size: 13px;
  color: #374151;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tree-children {
  margin-left: 0;
}
</style>
