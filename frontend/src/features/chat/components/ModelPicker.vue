<template>
  <div class="model-dropdown-wrapper" ref="modelDropdownRef">
    <button
      class="model-btn"
      :class="{ disabled: isStreaming || disabled }"
      :disabled="isStreaming || disabled"
      @click="toggleModelDropdown"
      v-tip="'切换模型'"
    >
      <svg class="model-btn-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3z"></path>
        <path d="M18.5 15.5l.7 1.9 1.9.7-1.9.7-.7 1.9-.7-1.9-1.9-.7 1.9-.7.7-1.9z"></path>
      </svg>
      <span class="model-btn-label">{{ currentModelLabel }}</span>
      <svg class="model-btn-arrow" :class="{ open: showModelDropdown }" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    </button>
    <Teleport to="body">
      <div v-if="showModelDropdown" ref="menuRef" class="model-dropdown-menu" :style="dropdownStyle" @click.stop>
        <div
          v-for="m in models"
          :key="m.name"
          class="model-dropdown-item"
          :class="{ active: m.name === selectedModel }"
          @click="selectModel(m.name)"
        >
          <span class="model-item-name">{{ m.model || m.name }}</span>
          <svg v-if="m.name === selectedModel" class="model-item-check" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12"></polyline>
          </svg>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
/**
 * 模型切换下拉（2026-10-20 从 ChatInput.vue 抽出）。
 *
 * 交互与原实现一致：按钮在输入框工具条里，菜单 Teleport 到 body 并按按钮位置
 * 计算 fixed 坐标（避免被输入框容器裁切）；点击外部（含 Teleport 出去的菜单）关闭。
 */
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  models: { type: Array, default: () => [] },
  selectedModel: { type: String, default: '' },
  isStreaming: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(['update:selectedModel'])

const showModelDropdown = ref(false)
const modelDropdownRef = ref(null)
const menuRef = ref(null)
const dropdownStyle = ref({})

const currentModelLabel = computed(() => {
  const m = props.models.find(item => item.name === props.selectedModel)
  // 显示模型名（deepseek-v4-flash / deepseek-v4-pro），而不是 provider 配置段名
  return m ? (m.model || m.name) : '选择模型'
})

function toggleModelDropdown() {
  if (props.isStreaming || props.disabled) return
  if (showModelDropdown.value) {
    showModelDropdown.value = false
    return
  }
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
}

function closeModelDropdown(event) {
  if (!showModelDropdown.value) return
  const target = event.target
  if (modelDropdownRef.value && modelDropdownRef.value.contains(target)) return
  if (menuRef.value && menuRef.value.contains(target)) return
  showModelDropdown.value = false
}

onMounted(() => document.addEventListener('click', closeModelDropdown))
onBeforeUnmount(() => document.removeEventListener('click', closeModelDropdown))
</script>

<style scoped>
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
  padding: 0 10px 0 11px;
  max-width: 220px;
  height: 36px;
  min-height: 36px;
  border: 1px solid var(--border-color);
  background: var(--bg-secondary);
  border-radius: 10px;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
  cursor: pointer;
  transition: background 0.22s ease, border-color 0.22s ease, box-shadow 0.22s ease,
    transform 0.22s cubic-bezier(0.4, 0, 0.2, 1);
  white-space: nowrap;
}

.model-btn-icon {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  color: var(--accent-color);
  transition: transform 0.25s ease;
}

.model-btn:hover:not(.disabled) {
  border-color: color-mix(in srgb, var(--accent-color) 45%, var(--border-color));
  background: color-mix(in srgb, var(--accent-color) 8%, var(--bg-secondary));
  box-shadow: 0 4px 12px color-mix(in srgb, var(--accent-color) 18%, transparent);
  transform: translateY(-1px);
}

.model-btn:hover:not(.disabled) .model-btn-icon {
  transform: rotate(-15deg) scale(1.12);
}

.model-btn:active:not(.disabled) {
  transform: translateY(0) scale(0.98);
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.05);
}

.model-btn.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.model-btn-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  transition: color 0.22s ease;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.model-btn:hover:not(.disabled) .model-btn-label {
  color: var(--accent-color);
}

.model-btn-arrow {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  color: var(--text-secondary);
  transition: transform 0.2s ease, color 0.22s ease;
}

.model-btn:hover:not(.disabled) .model-btn-arrow {
  color: var(--accent-color);
}

.model-btn-arrow.open {
  transform: rotate(180deg);
}

.model-dropdown-menu {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
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
  background: var(--bg-tertiary);
}

.model-dropdown-item.active {
  background: color-mix(in srgb, var(--accent-color) 12%, transparent);
}

.model-item-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}

.model-item-check {
  width: 16px;
  height: 16px;
  color: #0ea5e9;
  flex-shrink: 0;
}

.model-btn:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--accent-color) 65%, transparent);
  outline-offset: 2px;
}
</style>
