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
      <span v-if="item.type === 'directory'" class="folder-icon" @click.stop="expanded = !expanded">
        <svg v-if="expanded" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none">
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
    <div v-if="item.type === 'directory' && expanded && item.children" class="tree-children">
      <FileTreeNode 
        v-for="child in item.children" 
        :key="child.id" 
        :item="child" 
        :selectedId="selectedId"
        :depth="depth + 1"
        @select="$emit('select', $event)"
      />
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import FileIcon from './FileIcon.vue'

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
  }
})

const emit = defineEmits(['select'])

const expanded = ref(true)

function handleClick() {
  if (props.item.type === 'file') {
    emit('select', props.item)
  } else {
    expanded.value = !expanded.value
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
  background: #f3f4f6;
}

.tree-item.active {
  background: #dbeafe;
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
