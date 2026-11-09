<template>
  <div
      v-if="open"
      class="plus-menu"
      :style="menuStyle"
      @click.stop
      @mouseenter="$emit('hover-in')"
      @mouseleave="$emit('hover-out')"
    >
      <button type="button" class="plus-menu-item" @mouseenter="$emit('hover', 'file')" @click="$emit('add-file')">
        <svg class="plus-menu-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path>
          <polyline points="14 2 14 8 20 8"></polyline>
          <line x1="12" y1="18" x2="12" y2="12"></line>
          <line x1="9" y1="15" x2="15" y2="15"></line>
        </svg>
        <span class="plus-menu-label">添加文件</span>
        <svg class="plus-menu-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 18 15 12 9 6"></polyline>
        </svg>
      </button>
      <button v-if="knowledgeEnabled" type="button" class="plus-menu-item plus-menu-item-knowledge" @mouseenter="$emit('hover', 'knowledge')" @click="$emit('knowledge')">
        <svg class="plus-menu-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path>
          <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path>
        </svg>
        <span class="plus-menu-label">知识库</span>
        <svg class="plus-menu-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 18 15 12 9 6"></polyline>
        </svg>
      </button>
      <button type="button" class="plus-menu-item" @mouseenter="$emit('hover', 'experts')" @click="$emit('experts', $event)">
        <svg class="plus-menu-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"></path>
          <circle cx="12" cy="7" r="4"></circle>
        </svg>
        <span class="plus-menu-label">专家</span>
        <svg class="plus-menu-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 18 15 12 9 6"></polyline>
        </svg>
      </button>
      <button type="button" class="plus-menu-item" @mouseenter="$emit('hover', 'skills')" @click="$emit('skills', $event)">
        <svg class="plus-menu-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"></polygon>
        </svg>
        <span class="plus-menu-label">技能</span>
        <svg class="plus-menu-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 18 15 12 9 6"></polyline>
        </svg>
      </button>
      <button type="button" class="plus-menu-item" data-category="connectors" @mouseenter="$emit('hover', 'connectors')" @click="$emit('connectors', $event)">
        <svg class="plus-menu-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 22v-5"></path>
          <path d="M9 8V2"></path>
          <path d="M15 8V2"></path>
          <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8z"></path>
        </svg>
        <span class="plus-menu-label">连接器</span>
        <svg class="plus-menu-arrow" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="9 18 15 12 9 6"></polyline>
        </svg>
      </button>
    </div>
</template>

<script>
/**
 * 「+」主菜单（不含按钮）（2026-10-20 从 ChatInput.vue 抽出，行为不变）。
 *
 * 子面板（专家 / 技能 / 连接器）仍由父组件渲染与定位，这里只负责按钮、
 * 主菜单项与 hover 事件上抛，保持两套技能入口（输入框 `/` 与「+」面板）不变。
 */
export default {
  name: 'PlusMenu',
  props: {
    disabled: { type: Boolean, default: false },
    isStreaming: { type: Boolean, default: false },
    open: { type: Boolean, default: false },
    knowledgeEnabled: { type: Boolean, default: false },
    menuStyle: { type: Object, default: () => ({}) },
  },
  emits: [
    'toggle', 'add-file', 'knowledge', 'experts', 'skills', 'connectors',
    'hover', 'hover-in', 'hover-out',
  ],
}
</script>

<style scoped>
.plus-menu {
  background: var(--bg-secondary);
  border: 1px solid var(--border-color);
  border-radius: 12px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);
  padding: 6px;
  z-index: 9999;
}

.plus-menu-item {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 10px 12px;
  border: none;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: background 0.15s ease;
}

.plus-menu-item:hover {
  background: var(--bg-tertiary);
}

.plus-menu-item:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--accent-color) 65%, transparent);
  outline-offset: -2px;
}

.plus-menu-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: var(--text-secondary);
}

.plus-menu-label {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-primary);
  white-space: nowrap;
}

.plus-menu-item:hover .plus-menu-icon {
  color: var(--accent-color);
}

.plus-menu-arrow {
  width: 14px;
  height: 14px;
  flex-shrink: 0;
  color: var(--text-secondary);
  opacity: 0.7;
}

.plus-menu.is-list {
  width: 300px;
}

.plus-menu-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 4px 6px 8px;
  border-bottom: 1px solid var(--border-color);
  margin-bottom: 4px;
}

.plus-menu-back {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.plus-menu-back:hover {
  background: var(--bg-tertiary);
  color: var(--text-primary);
}

.plus-menu-back svg {
  width: 14px;
  height: 14px;
}

.plus-menu-back:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--accent-color) 65%, transparent);
  outline-offset: 1px;
}

.plus-menu-title {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  font-weight: 700;
  color: var(--text-primary);
}

.plus-menu-count {
  font-size: 12px;
  color: var(--text-secondary);
  font-variant-numeric: tabular-nums;
}

.plus-menu-list {
  max-height: 280px;
  overflow-y: auto;
}

.plus-menu-footer {
  display: block;
  width: 100%;
  margin-top: 6px;
  padding: 8px 10px 2px;
  border: none;
  border-top: 1px solid var(--border-color);
  background: transparent;
  font-size: 12px;
  color: var(--text-secondary);
  text-align: center;
  cursor: pointer;
  transition: color 0.15s ease;
}

.plus-menu-footer:hover {
  color: var(--accent-color);
}

.plus-menu-footer:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--accent-color) 65%, transparent);
  outline-offset: -1px;
  border-radius: 6px;
}
</style>
