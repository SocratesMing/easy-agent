import { requestJson } from './request.js'

export async function getScheduledTasks() {
  return requestJson(
    { url: '/api/scheduled-tasks', method: 'get' },
    '获取定时任务列表失败'
  )
}

export async function getScheduledTaskRuns(taskId) {
  return requestJson(
    { url: `/api/scheduled-tasks/${taskId}/runs`, method: 'get' },
    '获取执行记录失败'
  )
}

export async function deleteScheduledTask(taskId) {
  return requestJson(
    { url: `/api/scheduled-tasks/${taskId}`, method: 'delete' },
    '删除定时任务失败'
  )
}

export async function toggleScheduledTask(taskId) {
  return requestJson(
    { url: `/api/scheduled-tasks/${taskId}/toggle`, method: 'patch' },
    '切换任务状态失败'
  )
}

export async function runScheduledTaskNow(taskId) {
  return requestJson(
    { url: `/api/scheduled-tasks/${taskId}/run`, method: 'post' },
    '手动触发失败'
  )
}

export async function getScheduledTaskWorkspace(taskId, path = '') {
  return requestJson(
    {
      url: `/api/scheduled-tasks/${taskId}/workspace`,
      method: 'get',
      params: { path },
    },
    '获取工作目录失败'
  )
}
