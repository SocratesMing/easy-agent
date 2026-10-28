<template>
  <div class="model-dropdown-wrapper" ref="modelDropdownRef">
    <button
      class="model-btn"
      :class="{ disabled: isStreaming || disabled }"
      :disabled="isStreaming || disabled"
      @click="toggleModelDropdown"
      v-tip="'切换模型'"
    >
      <span class="model-btn-label">{{ currentModelLabel }}</span>
      <svg class="model-btn-arrow" :class="{ open: showModelDropdown }" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    </button>
    <div v-if="showModelDropdown" class="model-dropdown-menu" :style="dropdownStyle" @click.stop>
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
  </div>
</template>

<script>
/**
 * 模型切换下拉（2026-10-20 从 ChatInput.vue 抽出，交互与原实现一致）。
 */
export default {
  name: 'ModelPicker',
  props: {
    models: { type: Array, default: () => [] },
    selectedModel: { type: String, default: '' },
    isStreaming: { type: Boolean, default: false },
    disabled: { type: Boolean, default: false },
  },
  emits: ['update:selectedModel'],
  data() {
    return {
      showModelDropdown: false,
      dropdownStyle: {},
    }
  },
  computed: {
    currentModelLabel() {
      const m = this.models.find(item => item.name === this.selectedModel)
      // 显示模型名（deepseek-v4-flash / deepseek-v4-pro），而不是 provider 配置段名
      return m ? (m.model || m.name) : '选择模型'
    },
  },
  mounted() {
    document.addEventListener('click', this.closeModelDropdown)
  },
  beforeDestroy() {
    document.removeEventListener('click', this.closeModelDropdown)
  },
  methods: {
    toggleModelDropdown() {
      if (this.isStreaming || this.disabled) return
      if (this.showModelDropdown) {
        this.showModelDropdown = false
        return
      }
      const anchor = this.$refs.modelDropdownRef
      if (anchor) {
        const rect = anchor.getBoundingClientRect()
        this.dropdownStyle = {
          position: 'fixed',
          bottom: `${window.innerHeight - rect.top + 6}px`,
          left: `${rect.left}px`,
          minWidth: `${Math.max(rect.width, 200)}px`,
        }
      }
      this.showModelDropdown = true
    },
    selectModel(name) {
      this.$emit('update:selectedModel', name)
      this.showModelDropdown = false
    },
    closeModelDropdown(event) {
      const anchor = this.$refs.modelDropdownRef
      const target = event.target || event.srcElement
      if (this.showModelDropdown && anchor && !anchor.contains(target)) {
        this.showModelDropdown = false
      }
    },
  },
}
</script>

<style scoped>
.model-dropdown-wrapper {
  position: relative;
  display: inline-flex;
  align-items: center;
}

.plus-menu-wrapper {
  display: inline-flex;
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
  border: 1px solid var(--border-color);
  background: var(--bg-secondary);
  border-radius: 10px;
  cursor: pointer;
  transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
  white-space: nowrap;
}

.model-btn:hover:not(.disabled) {
  border-color: rgba(14, 165, 233, 0.4);
  background: color-mix(in srgb, var(--accent-color) 10%, transparent);
  transform: translateY(-1px);
}

.model-btn.disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.model-btn-label {
  font-size: 12px;
  font-weight: 500;
  color: var(--text-secondary);
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
  color: var(--text-secondary);
  transition: transform 0.2s ease;
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
</style>
