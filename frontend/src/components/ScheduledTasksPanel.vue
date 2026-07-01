<template>
  <div class="scheduled-tasks-panel">
    <div class="panel-nav">
      <span class="nav-title">定时任务</span>
      <button class="refresh-btn" @click="refresh" :disabled="loading" title="刷新">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" :class="{ spinning: loading }">
          <polyline points="23 4 23 10 17 10"></polyline>
          <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path>
        </svg>
      </button>
    </div>

    <div class="panel-content">
      <div v-if="loading && tasks.length === 0" class="loading-state">
        <div class="spinner"></div>
        <span>加载中...</span>
      </div>

      <div v-else-if="tasks.length === 0" class="empty-state">
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" class="empty-icon">
          <circle cx="12" cy="12" r="10"></circle>
          <polyline points="12 6 12 12 16 14"></polyline>
        </svg>
        <h3>暂无定时任务</h3>
        <p>在对话中输入定时需求（如"每天8点检查文件"），<br/>AI 将自动创建定时任务。</p>
      </div>

      <div v-else class="tasks-list">
        <div
          v-for="task in tasks"
          :key="task.task_id"
          class="task-card"
          :class="{ disabled: !task.enabled }"
        >
          <div class="task-header" @click="toggleExpand(task.task_id)">
            <div class="task-icon" :class="{ active: task.enabled }">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="12" r="10"></circle>
                <polyline points="12 6 12 12 16 14"></polyline>
              </svg>
            </div>
            <div class="task-info">
              <div class="task-name">{{ task.name }}</div>
              <div class="task-meta">
                <span class="task-cron">{{ task.schedule_cron }}</span>
                <span class="task-next" v-if="task.enabled && task.next_run_at">下次: {{ formatTime(task.next_run_at) }}</span>
                <span class="task-last" v-if="task.last_run_at">上次: {{ formatTime(task.last_run_at) }}</span>
              </div>
            </div>
            <div class="task-status">
              <span class="status-badge" :class="task.enabled ? 'active' : 'inactive'">
                {{ task.enabled ? '运行中' : '已暂停' }}
              </span>
            </div>
            <button class="expand-btn" :class="{ expanded: expandedTaskId === task.task_id }">
              <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="6 9 12 15 18 9"></polyline>
              </svg>
            </button>
          </div>

          <div v-if="expandedTaskId === task.task_id" class="task-detail">
            <div class="detail-section">
              <div class="detail-label">描述</div>
              <div class="detail-value">{{ task.description || '无描述' }}</div>
            </div>
            <div class="detail-section">
              <div class="detail-label">执行内容</div>
              <div class="detail-value prompt-text">{{ task.task_prompt }}</div>
            </div>

            <div class="detail-actions">
              <button class="action-btn toggle-btn" @click.stop="handleToggle(task)">
                {{ task.enabled ? '暂停' : '启用' }}
              </button>
              <button class="action-btn run-btn" @click.stop="handleRun(task)" :disabled="runLoading === task.task_id">
                {{ runLoading === task.task_id ? '触发中...' : '立即执行' }}
              </button>
              <button class="action-btn delete-btn" @click.stop="handleDelete(task)">
                删除
              </button>
            </div>

            <div class="runs-section">
              <div class="runs-header" @click.stop="toggleRunsCollapse">
                <svg class="runs-chevron" :class="{ expanded: !runsCollapsed }" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                  <polyline points="9 18 15 12 9 6"></polyline>
                </svg>
                <span>执行记录</span>
                <button class="refresh-runs-btn" @click.stop="loadRuns(expandedTaskId)">
                  刷新
                </button>
              </div>
              <div v-if="!runsCollapsed">
              <div v-if="runsLoading" class="runs-loading">加载中...</div>
              <div v-else-if="runs.length === 0" class="runs-empty">暂无执行记录</div>
              <div v-else class="runs-list">
                <div
                  v-for="run in runs"
                  :key="run.run_id"
                  class="run-item"
                  :class="run.status"
                >
                  <div class="run-header" @click.stop="toggleRunExpand(run.run_id)">
                    <svg class="run-chevron" :class="{ expanded: expandedRuns.has(run.run_id) }" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                      <polyline points="9 18 15 12 9 6"></polyline>
                    </svg>
                    <span class="run-status" :class="run.status">
                      {{ run.status === 'succeeded' ? '成功' : run.status === 'failed' ? '失败' : '运行中' }}
                    </span>
                    <span class="run-time">{{ formatTime(run.started_at) }}</span>
                    <span v-if="run.finished_at" class="run-duration">
                      耗时 {{ calcDuration(run.started_at, run.finished_at) }}
                    </span>
                  </div>
                  <div v-if="expandedRuns.has(run.run_id)" class="run-detail">
                    <div v-if="run.result_summary" class="run-result-text">
                      {{ run.result_summary }}
                    </div>
                    <div v-if="run.error_message" class="run-error">{{ run.error_message }}</div>
                  </div>
                </div>
              </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <Transition name="toast">
      <div v-if="toast" class="toast" :class="toast.type">
        {{ toast.message }}
      </div>
    </Transition>

    <ConfirmDialog
      ref="confirmDialog"
      title="确认删除"
      :message="`确定要删除定时任务“${pendingDeleteTaskName}”吗？此操作不可撤销。`"
      confirm-text="删除"
      cancel-text="取消"
      type="danger"
    />
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import {
  getScheduledTasks,
  getScheduledTaskRuns,
  deleteScheduledTask,
  toggleScheduledTask,
  runScheduledTaskNow,
} from '../api/scheduledTasks.js'
import ConfirmDialog from './ConfirmDialog.vue'

defineEmits(['close'])

const tasks = ref([])
const loading = ref(false)
const expandedTaskId = ref(null)
const runs = ref([])
const runsLoading = ref(false)
const runLoading = ref(null)
const expandedRuns = ref(new Set())
const runsCollapsed = ref(true)
const toast = ref(null)
const confirmDialog = ref(null)
const pendingDeleteTaskName = ref('')

function showToast(message, type = 'success') {
  toast.value = { message, type }
  setTimeout(() => { toast.value = null }, 3000)
}

async function refresh() {
  loading.value = true
  try {
    tasks.value = await getScheduledTasks()
  } catch (e) {
    showToast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

async function toggleExpand(taskId) {
  if (expandedTaskId.value === taskId) {
    expandedTaskId.value = null
    return
  }
  expandedTaskId.value = taskId
  expandedRuns.value = new Set()
  runsCollapsed.value = true
  await loadRuns(taskId)
}

async function loadRuns(taskId) {
  runsLoading.value = true
  try {
    runs.value = await getScheduledTaskRuns(taskId)
  } catch (e) {
    showToast(e.message, 'error')
    runs.value = []
  } finally {
    runsLoading.value = false
  }
}

function toggleRunExpand(runId) {
  const next = new Set(expandedRuns.value)
  if (next.has(runId)) {
    next.delete(runId)
  } else {
    next.add(runId)
  }
  expandedRuns.value = next
}

function toggleRunsCollapse() {
  runsCollapsed.value = !runsCollapsed.value
}

async function handleToggle(task) {
  try {
    await toggleScheduledTask(task.task_id)
    showToast(task.enabled ? '已暂停' : '已启用')
    await refresh()
  } catch (e) {
    showToast(e.message, 'error')
  }
}

async function handleRun(task) {
  runLoading.value = task.task_id
  try {
    await runScheduledTaskNow(task.task_id)
    showToast('已触发执行')
    setTimeout(() => loadRuns(task.task_id), 2000)
  } catch (e) {
    showToast(e.message, 'error')
  } finally {
    runLoading.value = null
  }
}

async function handleDelete(task) {
  pendingDeleteTaskName.value = task.name
  const confirmed = await confirmDialog.value.show()
  if (!confirmed) return
  try {
    await deleteScheduledTask(task.task_id)
    showToast('已删除')
    if (expandedTaskId.value === task.task_id) {
      expandedTaskId.value = null
    }
    await refresh()
  } catch (e) {
    showToast(e.message, 'error')
  }
}

function formatTime(ts) {
  if (!ts) return ''
  try {
    const d = new Date(ts)
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
  } catch {
    return ts
  }
}

function calcDuration(start, end) {
  try {
    const ms = new Date(end) - new Date(start)
    if (ms < 60000) return `${Math.round(ms / 1000)}秒`
    return `${Math.round(ms / 60000)}分钟`
  } catch {
    return ''
  }
}

onMounted(() => {
  refresh()
})
</script>

<style scoped>
.scheduled-tasks-panel {
  flex: 1;
  min-width: 0;
  position: relative;
  display: flex;
  flex-direction: column;
  height: 100%;
  background: var(--bg-primary, #f5f5f5);
}

.panel-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 16px;
  border-bottom: 1px solid var(--border-color, #e2e8f0);
  flex-shrink: 0;
}

.nav-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary, #1e293b);
}

.refresh-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--text-secondary, #666);
  cursor: pointer;
  transition: all 0.2s;
}

.refresh-btn:hover {
  background: var(--bg-hover, #f1f5f9);
  color: var(--text-primary, #333);
}

.refresh-btn svg {
  width: 18px;
  height: 18px;
}

.refresh-btn svg.spinning {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

.panel-content {
  flex: 1;
  overflow-y: auto;
  padding: 12px 16px;
}

.loading-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 60px 0;
  color: var(--text-secondary, #999);
}

.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid var(--border-color, #e5e5e5);
  border-top-color: var(--accent, #6c5ce7);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 100%;
  text-align: center;
  color: var(--text-secondary, #999);
}

.empty-icon {
  width: 64px;
  height: 64px;
  opacity: 0.4;
}

.empty-state h3 {
  margin: 8px 0 4px;
  font-size: 18px;
  color: var(--text-primary, #666);
}

.empty-state p {
  margin: 0;
  font-size: 14px;
  line-height: 1.6;
}

.tasks-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.task-card {
  background: var(--bg-card, #fff);
  border: 1px solid var(--border-color, #e5e5e5);
  border-radius: 12px;
  overflow: hidden;
  transition: border-color 0.2s, box-shadow 0.2s;
}

.task-card:hover {
  border-color: var(--accent, #6c5ce7);
  box-shadow: 0 2px 8px rgba(108, 92, 231, 0.1);
}

.task-card.disabled {
  opacity: 0.6;
}

.task-header {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px 16px;
  cursor: pointer;
}

.task-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: var(--bg-hover, #f0f0f0);
  color: var(--text-tertiary, #aaa);
  flex-shrink: 0;
}

.task-icon.active {
  background: rgba(108, 92, 231, 0.1);
  color: #6c5ce7;
}

.task-icon svg {
  width: 22px;
  height: 22px;
}

.task-info {
  flex: 1;
  min-width: 0;
}

.task-name {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary, #333);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.task-meta {
  display: flex;
  gap: 12px;
  margin-top: 4px;
  font-size: 12px;
  color: var(--text-secondary, #999);
}

.task-cron {
  font-family: 'Fira Code', Consolas, monospace;
  color: var(--accent, #6c5ce7);
}

.task-status {
  flex-shrink: 0;
}

.status-badge {
  padding: 3px 10px;
  border-radius: 12px;
  font-size: 12px;
  font-weight: 500;
}

.status-badge.active {
  background: rgba(34, 197, 94, 0.1);
  color: #16a34a;
}

.status-badge.inactive {
  background: rgba(156, 163, 175, 0.15);
  color: #6b7280;
}

.expand-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  border: none;
  background: transparent;
  color: var(--text-tertiary, #aaa);
  cursor: pointer;
  transition: transform 0.2s;
}

.expand-btn svg {
  width: 18px;
  height: 18px;
}

.expand-btn.expanded {
  transform: rotate(180deg);
}

.task-detail {
  padding: 0 16px 16px;
  border-top: 1px solid var(--border-color, #e5e5e5);
}

.detail-section {
  margin-top: 12px;
}

.detail-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-tertiary, #999);
  margin-bottom: 4px;
}

.detail-value {
  font-size: 14px;
  color: var(--text-primary, #555);
  line-height: 1.5;
}

.detail-value.prompt-text {
  font-family: 'Fira Code', Consolas, monospace;
  font-size: 13px;
  background: var(--bg-hover, #f9f9f9);
  padding: 8px 12px;
  border-radius: 8px;
  white-space: pre-wrap;
  word-break: break-all;
  max-height: 120px;
  overflow-y: auto;
}

.detail-actions {
  display: flex;
  gap: 8px;
  margin-top: 16px;
}

.action-btn {
  padding: 6px 16px;
  border: 1px solid var(--border-color, #e5e5e5);
  border-radius: 8px;
  background: var(--bg-card, #fff);
  font-size: 13px;
  color: var(--text-secondary, #666);
  cursor: pointer;
  transition: all 0.2s;
}

.action-btn:hover:not(:disabled) {
  border-color: var(--accent, #6c5ce7);
  color: var(--accent, #6c5ce7);
}

.action-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.delete-btn:hover:not(:disabled) {
  border-color: #ef4444;
  color: #ef4444;
}

.runs-section {
  margin-top: 20px;
}

.runs-header {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-tertiary, #999);
  margin-bottom: 8px;
  cursor: pointer;
  user-select: none;
}

.runs-chevron {
  width: 14px;
  height: 14px;
  transition: transform 0.2s;
}

.runs-chevron.expanded {
  transform: rotate(90deg);
}

.runs-header span {
  flex: 1;
}

.refresh-runs-btn {
  border: none;
  background: transparent;
  color: var(--accent, #6c5ce7);
  font-size: 12px;
  cursor: pointer;
}

.runs-loading, .runs-empty {
  padding: 12px;
  text-align: center;
  font-size: 13px;
  color: var(--text-tertiary, #aaa);
}

.runs-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.run-item {
  padding: 10px 12px;
  background: var(--bg-hover, #f9f9f9);
  border-radius: 8px;
  border-left: 3px solid transparent;
}

.run-item.succeeded {
  border-left-color: #22c55e;
}

.run-item.failed {
  border-left-color: #ef4444;
}

.run-item.running {
  border-left-color: #f59e0b;
}

.run-header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  cursor: pointer;
  user-select: none;
}

.run-chevron {
  width: 14px;
  height: 14px;
  color: var(--text-tertiary, #aaa);
  transition: transform 0.2s;
  flex-shrink: 0;
}

.run-chevron.expanded {
  transform: rotate(90deg);
}

.run-status {
  font-weight: 600;
}

.run-status.succeeded {
  color: #16a34a;
}

.run-status.failed {
  color: #ef4444;
}

.run-status.running {
  color: #f59e0b;
}

.run-time {
  color: var(--text-secondary, #999);
}

.run-duration {
  color: var(--text-tertiary, #aaa);
  margin-left: auto;
}

.run-detail {
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px solid var(--border-color, rgba(0, 0, 0, 0.06));
}

.run-result-text {
  font-size: 13px;
  color: var(--text-primary, #555);
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-all;
}

.run-error {
  margin-top: 6px;
  padding: 6px 10px;
  background: rgba(239, 68, 68, 0.06);
  border-radius: 6px;
  font-size: 12px;
  color: #dc2626;
  white-space: pre-wrap;
  word-break: break-all;
}

.toast {
  position: fixed;
  bottom: 24px;
  left: 50%;
  transform: translateX(-50%);
  padding: 10px 24px;
  border-radius: 8px;
  font-size: 14px;
  z-index: 9999;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
}

.toast.success {
  background: #22c55e;
  color: white;
}

.toast.error {
  background: #ef4444;
  color: white;
}

.toast-enter-active, .toast-leave-active {
  transition: opacity 0.3s, transform 0.3s;
}

.toast-enter-from, .toast-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(20px);
}
</style>

<style>
html[data-theme="dark"] .scheduled-tasks-panel {
  --bg-primary: #1a1a2e;
  --bg-card: #16213e;
  --bg-hover: #233;
  --border-color: #2a2a4a;
  --text-primary: #e0e0e0;
  --text-secondary: #a0a0b0;
  --text-tertiary: #707080;
  --accent: #7c6aef;
}
</style>
