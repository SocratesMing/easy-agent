<template>
  <!-- Collapsed badge -->
  <Transition name="todo-badge">
    <div
      v-if="todos.length > 0 && !expanded"
      class="todo-badge"
      @click="expanded = true"
      title="展开任务计划"
    >
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="todo-badge-icon">
        <path d="M9 11l3 3L22 4"></path>
        <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path>
      </svg>
      <span class="todo-badge-text">todo {{ completedCount }}/{{ todos.length }}</span>
    </div>
  </Transition>

  <!-- Expanded floating panel -->
  <Transition name="todo-slide">
    <div v-if="todos.length > 0 && expanded" class="todo-panel">
      <div class="todo-header">
        <div class="todo-title">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" class="todo-icon">
            <path d="M9 11l3 3L22 4"></path>
            <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"></path>
          </svg>
          <span>Task Plan</span>
          <span class="todo-count">{{ completedCount }}/{{ todos.length }}</span>
        </div>
        <button class="todo-close" @click="expanded = false" title="收起">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="15 18 9 12 15 6"></polyline>
          </svg>
        </button>
      </div>
      <div class="todo-progress">
        <div class="todo-progress-bar" :style="{ width: progressPercent + '%' }"></div>
      </div>
      <div class="todo-list">
        <div
          v-for="(todo, index) in todos"
          :key="index"
          class="todo-item"
          :class="todo.status"
        >
          <div class="todo-status-icon">
            <svg v-if="todo.status === 'completed'" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
              <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
              <polyline points="22 4 12 14.01 9 11.01"></polyline>
            </svg>
            <div v-else-if="todo.status === 'in_progress'" class="todo-spinner">
              <span></span><span></span><span></span>
            </div>
            <div v-else class="todo-pending-dot"></div>
          </div>
          <span class="todo-content" :class="{ 'line-through': todo.status === 'completed' }">
            {{ todo.content }}
          </span>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup>
import { computed, ref } from 'vue'

const props = defineProps({
  todos: {
    type: Array,
    default: () => []
  }
})

const expanded = ref(true)

const completedCount = computed(() => props.todos.filter(t => t.status === 'completed').length)
const progressPercent = computed(() => {
  if (props.todos.length === 0) return 0
  return Math.round((completedCount.value / props.todos.length) * 100)
})
</script>

<style scoped>
/* Collapsed badge */
.todo-badge {
  position: absolute;
  left: 0;
  top: 12px;
  z-index: 20;
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  background: #f8f9fa;
  border: 1px solid #e5e7eb;
  border-left: none;
  border-radius: 0 8px 8px 0;
  cursor: pointer;
  transition: all 0.2s ease;
  box-shadow: 2px 2px 8px rgba(0, 0, 0, 0.06);
  user-select: none;
}

.todo-badge:hover {
  background: #7c6aef;
  border-color: #7c6aef;
  box-shadow: 2px 2px 12px rgba(124, 106, 239, 0.25);
}

.todo-badge:hover .todo-badge-text {
  color: #fff;
}

.todo-badge:hover .todo-badge-icon {
  color: #fff;
}

.todo-badge-icon {
  width: 14px;
  height: 14px;
  color: #7c6aef;
  flex-shrink: 0;
  transition: color 0.2s;
}

.todo-badge-text {
  font-size: 12px;
  font-weight: 600;
  color: #4b5563;
  white-space: nowrap;
  transition: color 0.2s;
}

/* Badge transition */
.todo-badge-enter-active,
.todo-badge-leave-active {
  transition: all 0.3s ease;
}

.todo-badge-enter-from {
  opacity: 0;
  transform: translateX(-20px);
}

.todo-badge-leave-to {
  opacity: 0;
  transform: translateX(-20px);
}

/* Expanded floating panel */
.todo-panel {
  position: absolute;
  left: 0;
  top: 0;
  z-index: 20;
  width: 260px;
  background: #f8f9fa;
  border-right: 1px solid #e5e7eb;
  border-bottom: 1px solid #e5e7eb;
  border-radius: 0 12px 12px 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  box-shadow: 4px 0 16px rgba(0, 0, 0, 0.06);
}

.todo-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 14px;
  border-bottom: 1px solid #e5e7eb;
}

.todo-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: #1f2937;
}

.todo-icon {
  width: 16px;
  height: 16px;
  color: #7c6aef;
}

.todo-count {
  font-size: 11px;
  font-weight: 500;
  color: #6b7280;
  background: #e5e7eb;
  padding: 1px 6px;
  border-radius: 8px;
}

.todo-close {
  background: none;
  border: none;
  color: #6b7280;
  cursor: pointer;
  padding: 2px;
  border-radius: 4px;
  display: flex;
  align-items: center;
}

.todo-close:hover {
  color: #1f2937;
  background: #e5e7eb;
}

.todo-close svg {
  width: 14px;
  height: 14px;
}

.todo-progress {
  height: 3px;
  background: #e5e7eb;
}

.todo-progress-bar {
  height: 100%;
  background: #7c6aef;
  transition: width 0.4s ease;
  border-radius: 0 2px 2px 0;
}

.todo-list {
  padding: 8px 12px 12px;
  overflow-y: auto;
  flex: 1;
  max-height: 70vh;
}

.todo-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 8px 6px;
  border-radius: 6px;
  transition: background 0.2s;
}

.todo-item:hover {
  background: #e5e7eb;
}

.todo-item.in_progress {
  background: rgba(124, 106, 239, 0.08);
}

.todo-status-icon {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 1px;
}

.todo-status-icon svg {
  width: 16px;
  height: 16px;
  color: #4ade80;
}

.todo-spinner {
  width: 14px;
  height: 14px;
  display: flex;
  align-items: center;
  gap: 2px;
}

.todo-spinner span {
  width: 4px;
  height: 4px;
  border-radius: 50%;
  background: #7c6aef;
  animation: todo-bounce 1.4s infinite ease-in-out both;
}

.todo-spinner span:nth-child(1) { animation-delay: -0.32s; }
.todo-spinner span:nth-child(2) { animation-delay: -0.16s; }

@keyframes todo-bounce {
  0%, 80%, 100% { transform: scale(0); }
  40% { transform: scale(1); }
}

.todo-pending-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 2px solid #9ca3af;
}

.todo-content {
  font-size: 12.5px;
  line-height: 1.5;
  color: #4b5563;
  word-break: break-word;
}

.todo-content.line-through {
  text-decoration: line-through;
  color: #9ca3af;
}

.todo-item.completed .todo-content {
  color: #9ca3af;
}

.todo-item.in_progress .todo-content {
  color: #1f2937;
  font-weight: 500;
}

/* Panel slide transition */
.todo-slide-enter-active,
.todo-slide-leave-active {
  transition: all 0.3s ease;
}

.todo-slide-enter-from {
  opacity: 0;
  transform: translateX(-260px);
}

.todo-slide-leave-to {
  opacity: 0;
  transform: translateX(-260px);
}

.todo-slide-enter-to,
.todo-slide-leave-from {
  transform: translateX(0);
}

/* Scrollbar */
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
</style>
