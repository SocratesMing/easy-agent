import { API_BASE_URL } from '../config.js'
import { authFetch } from './auth.js'

export async function getScheduledTasks() {
  const response = await authFetch(`${API_BASE_URL}/api/scheduled-tasks`)
  if (!response.ok) throw new Error('获取定时任务列表失败')
  return await response.json()
}

export async function getScheduledTaskRuns(taskId) {
  const response = await authFetch(`${API_BASE_URL}/api/scheduled-tasks/${taskId}/runs`)
  if (!response.ok) throw new Error('获取执行记录失败')
  return await response.json()
}

export async function deleteScheduledTask(taskId) {
  const response = await authFetch(`${API_BASE_URL}/api/scheduled-tasks/${taskId}`, {
    method: 'DELETE',
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || '删除定时任务失败')
  }
  return await response.json()
}

export async function toggleScheduledTask(taskId) {
  const response = await authFetch(`${API_BASE_URL}/api/scheduled-tasks/${taskId}/toggle`, {
    method: 'PATCH',
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || '切换任务状态失败')
  }
  return await response.json()
}

export async function runScheduledTaskNow(taskId) {
  const response = await authFetch(`${API_BASE_URL}/api/scheduled-tasks/${taskId}/run`, {
    method: 'POST',
  })
  if (!response.ok) {
    const data = await response.json().catch(() => ({}))
    throw new Error(data.detail || '手动触发失败')
  }
  return await response.json()
}

export async function getScheduledTaskWorkspace(taskId, path = '') {
  const response = await authFetch(
    `${API_BASE_URL}/api/scheduled-tasks/${taskId}/workspace?path=${encodeURIComponent(path)}`
  )
  if (!response.ok) throw new Error('获取工作目录失败')
  return await response.json()
}
