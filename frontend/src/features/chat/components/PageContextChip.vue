<template>
  <div class="page-context-chip" :title="text">
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
      <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"></path>
      <circle cx="12" cy="10" r="3"></circle>
    </svg>
    <span>{{ label }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { pageContextLabel } from '../../../utils/pageContext.js'

// AI 侧边栏发起的用户消息：展示随消息附带的页面上下文（悬浮看全文）
const props = defineProps({
  text: { type: String, default: '' },
})
const label = computed(() => pageContextLabel(props.text))
</script>

<style scoped>
.page-context-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 100%;
  margin-bottom: 4px;
  padding: 2px 9px 2px 7px;
  border: 1px solid var(--border-color, #e2e8f0);
  border-radius: 999px;
  background: var(--bg-primary, #f8fafc);
  color: var(--text-secondary, #64748b);
  font-size: 12px;
  line-height: 1.6;
  cursor: help;
}

.page-context-chip svg {
  width: 12px;
  height: 12px;
  flex-shrink: 0;
  color: var(--accent-color, #0ea5e9);
}

.page-context-chip span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>
