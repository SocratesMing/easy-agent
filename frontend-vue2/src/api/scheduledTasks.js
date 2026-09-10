import request from '@/utils/request'

/**
 * 获取定时任务列表
 *
 * @returns {Promise<Array>} 定时任务列表
 * @example
 * getScheduledTasks()
 */
export async function getScheduledTasks() {
  return request(
    {
      url: '/agent/scheduled-tasks',
      method: 'get',
    },
    '获取定时任务列表失败'
  )
}

/**
 * 获取指定定时任务的执行记录
 *
 * @param {string} taskId 任务 ID
 * @returns {Promise<Array>} 执行记录列表
 * @example
 * getScheduledTaskRuns('task-1')
 */
export async function getScheduledTaskRuns(taskId) {
  return request(
    {
      url: `/agent/scheduled-tasks/${taskId}/runs`,
      method: 'get',
    },
    '获取执行记录失败'
  )
}

/**
 * 删除指定定时任务
 *
 * @param {string} taskId 任务 ID
 * @returns {Promise<Object>} 删除结果
 * @example
 * deleteScheduledTask('task-1')
 */
export async function deleteScheduledTask(taskId) {
  return request(
    {
      url: `/agent/scheduled-tasks/${taskId}`,
      method: 'delete',
    },
    '删除定时任务失败'
  )
}

/**
 * 启用 / 停用指定定时任务
 *
 * @param {string} taskId 任务 ID
 * @returns {Promise<Object>} 切换后的任务信息
 * @example
 * toggleScheduledTask('task-1')
 */
export async function toggleScheduledTask(taskId) {
  return request(
    {
      url: `/agent/scheduled-tasks/${taskId}/toggle`,
      method: 'patch',
    },
    '切换任务状态失败'
  )
}

/**
 * 立即执行一次指定定时任务
 *
 * @param {string} taskId 任务 ID
 * @returns {Promise<Object>} 触发结果
 * @example
 * runScheduledTaskNow('task-1')
 */
export async function runScheduledTaskNow(taskId) {
  return request(
    {
      url: `/agent/scheduled-tasks/${taskId}/run`,
      method: 'post',
    },
    '手动触发失败'
  )
}

/**
 * 获取定时任务的工作目录文件树
 *
 * @param {string} taskId 任务 ID
 * @param {string} [path=''] 子目录路径，为空表示根目录
 * @returns {Promise<Object>} 文件树节点
 * @example
 * getScheduledTaskWorkspace('task-1', '')
 */
export async function getScheduledTaskWorkspace(taskId, path = '') {
  return request(
    {
      url: `/agent/scheduled-tasks/${taskId}/workspace`,
      method: 'get',
      params: { path },
    },
    '获取工作目录失败'
  )
}
