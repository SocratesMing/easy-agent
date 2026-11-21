<template>
  <div v-if="tasks.length > 0" ref="rootRef" class="subagent-root">
    <button
      type="button"
      class="subagent-pill"
      :class="{ 'is-active': summary.hasRunning, 'is-open': expanded }"
      @click.stop="expanded = !expanded"
      :title="expanded ? '收起子智能体进度' : '查看子智能体进度'"
    >
      <svg class="subagent-pill-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"></path>
        <circle cx="9" cy="7" r="4"></circle>
        <path d="M22 21v-2a4 4 0 0 0-3-3.87"></path>
        <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
      </svg>
      <span v-if="summary.hasRunning" class="subagent-pill-dot" aria-hidden="true"></span>
      <span class="subagent-pill-text">{{ summary.pillLabel }}</span>
      <svg class="subagent-pill-caret" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="6 9 12 15 18 9"></polyline>
      </svg>
    </button>

    <Transition name="subagent-pop">
      <div v-if="expanded" class="subagent-popup" @click.stop>
        <div class="subagent-header">
          <div class="subagent-title">
            <svg class="subagent-icon" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"></path>
              <circle cx="9" cy="7" r="4"></circle>
              <path d="M22 21v-2a4 4 0 0 0-3-3.87"></path>
              <path d="M16 3.13a4 4 0 0 1 0 7.75"></path>
            </svg>
            <span class="subagent-title-name">子智能体</span>
            <span v-if="summary.statusSummary" class="subagent-summary">{{ summary.statusSummary }}</span>
          </div>
          <button type="button" class="subagent-close" @click="expanded = false" title="收起">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="18 15 12 9 6 15"></polyline>
            </svg>
          </button>
        </div>

        <div class="subagent-list">
          <div
            v-for="task in tasks"
            :key="task.id"
            class="subagent-row-wrap"
            :class="[task.status, { 'is-selected': isTaskOpen(task.id) }]"
          >
            <button
              type="button"
              class="subagent-row"
              :aria-expanded="isTaskOpen(task.id) ? 'true' : 'false'"
              @click="toggleTask(task.id)"
            >
              <span class="subagent-dot-col">
                <span class="subagent-dot" :class="task.status"></span>
              </span>
              <span class="subagent-main-col">
                <span class="subagent-row-title">{{ task.title }}</span>
                <span class="subagent-row-sub">
                  <span>{{ task.subagentType }}</span>
                  <template v-if="task.latestSummary">
                    <span class="subagent-sep">·</span>
                    <span class="subagent-latest">{{ task.latestSummary }}</span>
                  </template>
                </span>
              </span>
              <span class="subagent-meta-col">
                <span v-if="task.stepCount > 0" class="subagent-meta-line">{{ task.stepCount }} 步</span>
                <span v-if="task.durationLabel" class="subagent-meta-line">{{ task.durationLabel }}</span>
                <span v-else-if="task.status === 'running'" class="subagent-meta-line is-running">执行中</span>
              </span>
              <svg class="subagent-chevron" :class="{ rotated: isTaskOpen(task.id) }" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <polyline points="9 18 15 12 9 6"></polyline>
              </svg>
            </button>

            <div v-if="isTaskOpen(task.id)" class="subagent-detail">
              <ul v-if="task.steps && task.steps.length" class="subagent-steps">
                <li
                  v-for="(st, si) in task.steps"
                  :key="si"
                  class="subagent-step"
                  :class="st.success === false ? 'err' : (st.success === true ? 'ok' : 'running')"
                >
                  <span class="subagent-step-mark" aria-hidden="true">{{ st.success === false ? '✕' : (st.success === true ? '✓' : '⋯') }}</span>
                  <span class="subagent-step-text">{{ st.summary }}</span>
                </li>
              </ul>
              <div v-else-if="task.status === 'running'" class="subagent-step-empty">子智能体启动中…</div>
              <pre v-if="task.resultFull" class="subagent-result-body">{{ task.resultFull }}</pre>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { summarizeSubagentPill } from '../../../shared/chat/subagentMonitor.js'

const props = defineProps({
  tasks: {
    type: Array,
    default: () => [],
  },
})

const rootRef = ref(null)
const expanded = ref(false)
const openTasks = ref({})

const summary = computed(() => summarizeSubagentPill(props.tasks))

function toggleTask(id) {
  openTasks.value = { ...openTasks.value, [id]: !openTasks.value[id] }
}

function isTaskOpen(id) {
  return !!openTasks.value[id]
}

function onOutsideClick(e) {
  if (!expanded.value) return
  if (rootRef.value && !rootRef.value.contains(e.target)) {
    expanded.value = false
  }
}

function onKeydown(e) {
  if (e.key === 'Escape' && expanded.value) expanded.value = false
}

onMounted(() => {
  document.addEventListener('click', onOutsideClick)
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onOutsideClick)
  document.removeEventListener('keydown', onKeydown)
})
</script>

<style scoped>
.subagent-root {
  position: relative;
  z-index: 30;
}

.subagent-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 28px;
  max-width: 240px;
  padding: 0 10px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 999px;
  color: var(--text-secondary, #64748b);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.16s ease, color 0.16s ease, border-color 0.16s ease;
  user-select: none;
}

.subagent-pill:hover,
.subagent-pill.is-open {
  background: rgba(15, 23, 42, 0.05);
  color: var(--text-primary, #1e293b);
}

.subagent-pill-icon {
  width: 15px;
  height: 15px;
  color: inherit;
  flex-shrink: 0;
  opacity: 0.85;
}

.subagent-pill-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #22c55e;
  flex-shrink: 0;
  animation: subagent-dot-pulse 1.8s ease-in-out infinite;
}

@keyframes subagent-dot-pulse {
  0%, 100% { opacity: 0.45; transform: scale(0.9); }
  50% { opacity: 1; transform: scale(1.15); }
}

.subagent-pill-text {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}

.subagent-pill-caret {
  width: 12px;
  height: 12px;
  color: inherit;
  opacity: 0.7;
  flex-shrink: 0;
  transition: transform 0.2s ease;
}

.subagent-pill.is-open .subagent-pill-caret {
  transform: rotate(180deg);
}

.subagent-popup {
  position: absolute;
  top: calc(100% + 6px);
  left: 0;
  width: 400px;
  max-width: calc(100vw - 32px);
  background: var(--bg-secondary, #ffffff);
  border: 1px solid var(--border-color, rgba(15, 23, 42, 0.08));
  border-radius: 16px;
  box-shadow: 0 12px 36px rgba(15, 23, 42, 0.1);
  overflow: hidden;
}

.subagent-pop-enter-active,
.subagent-pop-leave-active {
  transition: opacity 0.16s ease, transform 0.16s ease;
  transform-origin: top left;
}
.subagent-pop-enter-from,
.subagent-pop-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.985);
}

.subagent-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 13px 16px 8px;
}

.subagent-title {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.subagent-icon {
  width: 16px;
  height: 16px;
  color: var(--text-secondary, #64748b);
  flex-shrink: 0;
}

.subagent-title-name {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--text-primary, #111827);
  flex-shrink: 0;
}

.subagent-summary {
  font-size: 12.5px;
  font-weight: 400;
  color: var(--text-secondary, #6b7280);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.subagent-close {
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

.subagent-close:hover {
  background: rgba(15, 23, 42, 0.06);
  color: var(--text-primary, #111827);
}

.subagent-close svg {
  width: 14px;
  height: 14px;
}

.subagent-list {
  padding: 4px 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: min(56vh, 420px);
  overflow-y: auto;
}

.subagent-row-wrap {
  border-radius: 10px;
  transition: background 0.15s ease;
}

.subagent-row-wrap:hover,
.subagent-row-wrap.is-selected {
  background: rgba(15, 23, 42, 0.045);
}

.subagent-row {
  width: 100%;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 9px 10px;
  border: none;
  background: transparent;
  text-align: left;
  cursor: pointer;
  font-family: inherit;
}

.subagent-dot-col {
  width: 12px;
  padding-top: 6px;
  display: flex;
  justify-content: center;
  flex-shrink: 0;
}

.subagent-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #cbd5e1;
}

.subagent-dot.completed {
  background: #22c55e;
}

.subagent-dot.running {
  background: #3b82f6;
  animation: subagent-dot-pulse 1.5s ease-in-out infinite;
}

.subagent-dot.error {
  background: #ef4444;
}

.subagent-main-col {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.subagent-row-title {
  font-size: 13px;
  font-weight: 500;
  line-height: 1.45;
  color: var(--text-primary, #111827);
  word-break: break-word;
}

.subagent-row-sub {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 11.5px;
  line-height: 1.35;
  color: var(--text-secondary, #9ca3af);
  min-width: 0;
}

.subagent-sep {
  opacity: 0.7;
}

.subagent-latest {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.subagent-meta-col {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
  padding-top: 1px;
  flex-shrink: 0;
}

.subagent-meta-line {
  font-size: 11.5px;
  line-height: 1.35;
  color: var(--text-secondary, #9ca3af);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}

.subagent-meta-line.is-running {
  color: #3b82f6;
}

.subagent-chevron {
  width: 14px;
  height: 14px;
  margin-top: 3px;
  color: var(--text-secondary, #9ca3af);
  flex-shrink: 0;
  transition: transform 0.16s ease;
}

.subagent-chevron.rotated {
  transform: rotate(90deg);
}

.subagent-detail {
  padding: 0 12px 10px 32px;
}

.subagent-steps {
  list-style: none;
  margin: 0;
  padding: 4px 0 2px 10px;
  border-left: 1.5px solid var(--border-color, #e2e8f0);
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.subagent-step {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--text-secondary, #64748b);
}

.subagent-step.running {
  color: var(--text-primary, #1f2937);
  font-weight: 500;
}

.subagent-step.err {
  color: #dc2626;
}

.subagent-step-mark {
  flex-shrink: 0;
  width: 11px;
  text-align: center;
  font-weight: 600;
}

.subagent-step.ok .subagent-step-mark { color: #22c55e; }
.subagent-step.err .subagent-step-mark { color: #ef4444; }

.subagent-step-text {
  min-width: 0;
  overflow-wrap: anywhere;
}

.subagent-step-empty {
  font-size: 12px;
  color: var(--text-secondary, #9ca3af);
}

.subagent-result-body {
  margin: 8px 0 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--bg-primary, #ffffff);
  border: 1px solid var(--border-color, rgba(15, 23, 42, 0.08));
  color: var(--text-primary, #1e293b);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 11.5px;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
  max-height: 160px;
  overflow: auto;
}

html[data-theme="dark"] .subagent-pill:hover,
html[data-theme="dark"] .subagent-pill.is-open {
  background: rgba(255, 255, 255, 0.08);
}

html[data-theme="dark"] .subagent-popup {
  box-shadow: 0 12px 36px rgba(0, 0, 0, 0.48);
}

html[data-theme="dark"] .subagent-row-wrap:hover,
html[data-theme="dark"] .subagent-row-wrap.is-selected {
  background: rgba(255, 255, 255, 0.06);
}
</style>
