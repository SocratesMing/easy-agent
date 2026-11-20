/**
 * 定时任务 API 工厂（双端共享契约，由宿主注入 request 传输适配器）。
 */
export function createScheduledTasksApi(request) {
  return {
    async getScheduledTasks() {
      return request(
        { url: '/agent/scheduled-tasks', method: 'get' },
        '获取定时任务列表失败'
      )
    },

    async getScheduledTaskRuns(taskId) {
      return request(
        { url: `/agent/scheduled-tasks/${taskId}/runs`, method: 'get' },
        '获取执行记录失败'
      )
    },

    async deleteScheduledTask(taskId) {
      return request(
        { url: `/agent/scheduled-tasks/${taskId}`, method: 'delete' },
        '删除定时任务失败'
      )
    },

    async toggleScheduledTask(taskId) {
      return request(
        { url: `/agent/scheduled-tasks/${taskId}/toggle`, method: 'patch' },
        '切换任务状态失败'
      )
    },

    async runScheduledTaskNow(taskId) {
      return request(
        { url: `/agent/scheduled-tasks/${taskId}/run`, method: 'post' },
        '手动触发失败'
      )
    },

    async getScheduledTaskWorkspace(taskId, path = '') {
      return request(
        {
          url: `/agent/scheduled-tasks/${taskId}/workspace`,
          method: 'get',
          params: { path },
        },
        '获取工作目录失败'
      )
    },
  }
}
