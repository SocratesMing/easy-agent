<template>
  <div v-if="todos.length > 0" ref="rootRef" class="todo-root">
    <button
      type="button"
      class="todo-pill"
      :class="{ 'is-active': hasInProgress, 'is-open': expanded }"
      @click.stop="expanded = !expanded"
      :title="expanded ? '收起任务规划' : '展开任务规划'"
    >
      <svg class="todo-pill-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <circle cx="5" cy="7" r="1.5"></circle>
        <line x1="10" y1="7" x2="20" y2="7"></line>
        <circle cx="5" cy="12" r="1.5"></circle>
        <line x1="10" y1="12" x2="20" y2="12"></line>
        <circle cx="5" cy="17" r="1.5"></circle>
        <line x1="10" y1="17" x2="20" y2="17"></line>
      </svg>
      <span v-if="hasInProgress" class="todo-pill-spinner" aria-hidden="true"></span>
      <span class="todo-pill-text">{{ completedCount }}/{{ todos.length }}</span>
      <svg class="todo-pill-caret" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    </button>

    <Transition name="todo-pop">
      <div v-if="expanded" class="todo-popup" @click.stop>
        <div class="todo-header">
          <div class="todo-title">
            <svg class="todo-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <circle cx="5" cy="7" r="1.5"></circle>
              <line x1="10" y1="7" x2="20" y2="7"></line>
              <circle cx="5" cy="12" r="1.5"></circle>
              <line x1="10" y1="12" x2="20" y2="12"></line>
              <circle cx="5" cy="17" r="1.5"></circle>
              <line x1="10" y1="17" x2="20" y2="17"></line>
            </svg>
            <span class="todo-title-name">任务</span>
            <span v-if="statusSummary" class="todo-summary">{{ statusSummary }}</span>
          </div>
          <button type="button" class="todo-close" @click="expanded = false" title="收起">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="18 15 12 9 6 15"></polyline>
            </svg>
          </button>
        </div>
        <div class="todo-list">
          <div
            v-for="(todo, index) in todos"
            :key="index"
            class="todo-item"
            :class="todo.status"
          >
            <div class="todo-status-icon">
              <span v-if="todo.status === 'completed'" class="todo-done-dot" aria-hidden="true"></span>
              <span v-else-if="todo.status === 'in_progress'" class="todo-ring-spinner" aria-hidden="true"></span>
              <span v-else class="todo-pending-dot" aria-hidden="true"></span>
            </div>
            <span class="todo-content">{{ todo.content }}</span>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { computed, ref, onMounted, onBeforeUnmount } from 'vue'

const props = defineProps({
  todos: {
    type: Array,
    default: () => [],
  },
})

const expanded = ref(false)
const rootRef = ref(null)

const completedCount = computed(() => props.todos.filter(t => t.status === 'completed').length)
const inProgressCount = computed(() => props.todos.filter(t => t.status === 'in_progress').length)
const pendingCount = computed(() => props.todos.filter(t => t.status !== 'completed' && t.status !== 'in_progress').length)
const hasInProgress = computed(() => inProgressCount.value > 0)
const statusSummary = computed(() => {
  const parts = []
  if (inProgressCount.value > 0) parts.push(`${inProgressCount.value} 进行中`)
  if (pendingCount.value > 0) parts.push(`${pendingCount.value} 待处理`)
  if (completedCount.value > 0 && parts.length === 0) parts.push(`${completedCount.value} 已完成`)
  else if (completedCount.value > 0 && inProgressCount.value === 0) parts.push(`${completedCount.value} 已完成`)
  return parts.join(' · ')
})

function onDocClick(e) {
  if (expanded.value && rootRef.value && !rootRef.value.contains(e.target)) {
    expanded.value = false
  }
}
onMounted(() => document.addEventListener('click', onDocClick))
onBeforeUnmount(() => document.removeEventListener('click', onDocClick))
</script>

<style scoped>
.todo-root {
  position: relative;
  display: flex;
  align-items: center;
}

.todo-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  padding: 0 10px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 999px;
  cursor: pointer;
  color: var(--text-secondary, #64748b);
  transition: background 0.16s ease, color 0.16s ease;
  user-select: none;
}

.todo-pill:hover,
.todo-pill.is-open {
  background: rgba(15, 23, 42, 0.05);
  color: var(--text-primary, #1e293b);
}

.todo-pill-icon {
  width: 15px;
  height: 15px;
  color: inherit;
  flex-shrink: 0;
  opacity: 0.85;
}

.todo-pill-spinner {
  width: 11px;
  height: 11px;
  border-radius: 50%;
  border: 1.5px solid rgba(15, 23, 42, 0.16);
  border-top-color: var(--text-primary, #475569);
  animation: todo-spin 0.8s linear infinite;
  flex-shrink: 0;
}

@keyframes todo-spin {
  to { transform: rotate(360deg); }
}

.todo-pill-text {
  font-size: 12.5px;
  font-weight: 500;
  color: inherit;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.todo-pill-caret {
  width: 12px;
  height: 12px;
  color: inherit;
  opacity: 0.7;
  flex-shrink: 0;
  transition: transform 0.2s ease;
}

.todo-pill.is-open .todo-pill-caret {
  transform: rotate(180deg);
}

.todo-popup {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  width: 340px;
  max-width: calc(100vw - 32px);
  max-height: min(46vh, 420px);
  display: flex;
  flex-direction: column;
  background: var(--bg-secondary, #ffffff);
  border: 1px solid var(--border-color, rgba(15, 23, 42, 0.08));
  border-radius: 16px;
  box-shadow: 0 12px 36px rgba(15, 23, 42, 0.1);
  overflow: hidden;
}

.todo-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 13px 16px 8px;
}

.todo-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.todo-icon {
  width: 16px;
  height: 16px;
  color: var(--text-secondary, #64748b);
  flex-shrink: 0;
}

.todo-title-name {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-primary, #111827);
  flex-shrink: 0;
}

.todo-summary {
  font-size: 12.5px;
  font-weight: 400;
  color: var(--text-secondary, #6b7280);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.todo-close {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--text-secondary, #9ca3af);
  cursor: pointer;
  flex-shrink: 0;
}

.todo-close:hover {
  color: var(--text-primary, #111827);
  background: rgba(15, 23, 42, 0.06);
}

.todo-close svg {
  width: 14px;
  height: 14px;
}

.todo-list {
  padding: 2px 10px 12px;
  overflow-y: auto;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.todo-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 7px 8px;
  border-radius: 8px;
  transition: background 0.15s ease;
}

.todo-item:hover {
  background: rgba(15, 23, 42, 0.04);
}

.todo-status-icon {
  flex-shrink: 0;
  width: 16px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  padding-top: 2px;
}

.todo-ring-spinner {
  width: 13px;
  height: 13px;
  border-radius: 50%;
  border: 1.8px solid rgba(15, 23, 42, 0.14);
  border-top-color: var(--text-primary, #374151);
  animation: todo-spin 0.85s linear infinite;
}

.todo-done-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #22c55e;
}

.todo-pending-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #cbd5e1;
}

.todo-content {
  font-size: 13px;
  line-height: 1.5;
  color: var(--text-secondary, #6b7280);
  word-break: break-word;
}

.todo-item.in_progress .todo-content {
  color: var(--text-primary, #111827);
  font-weight: 500;
}

.todo-item.completed .todo-content {
  color: var(--text-secondary, #6b7280);
}

.todo-pop-enter-active,
.todo-pop-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
  transform-origin: top right;
}

.todo-pop-enter-from,
.todo-pop-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.985);
}

@media (prefers-reduced-motion: reduce) {
  .todo-pill-spinner,
  .todo-ring-spinner {
    animation: none;
  }
  .todo-pop-enter-active,
  .todo-pop-leave-active {
    transition-duration: 0.01ms;
  }
}

.todo-list::-webkit-scrollbar {
  width: 4px;
}

.todo-list::-webkit-scrollbar-track {
  background: transparent;
}

.todo-list::-webkit-scrollbar-thumb {
  background: #d1d5db;
  border-radius: 2px;
}

html[data-theme="dark"] .todo-pill:hover,
html[data-theme="dark"] .todo-pill.is-open {
  background: rgba(255, 255, 255, 0.08);
}

html[data-theme="dark"] .todo-popup {
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.48);
}

html[data-theme="dark"] .todo-item:hover {
  background: rgba(255, 255, 255, 0.06);
}

html[data-theme="dark"] .todo-ring-spinner,
html[data-theme="dark"] .todo-pill-spinner {
  border-color: rgba(255, 255, 255, 0.18);
  border-top-color: var(--text-primary, #e2e8f0);
}
</style>
