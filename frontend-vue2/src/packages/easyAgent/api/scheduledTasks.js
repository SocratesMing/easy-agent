import request from '../utils/request.js'
import { createScheduledTasksApi } from '../shared/api/scheduledTasks.js'

export const {
  getScheduledTasks,
  getScheduledTaskRuns,
  deleteScheduledTask,
  toggleScheduledTask,
  runScheduledTaskNow,
  getScheduledTaskWorkspace,
} = createScheduledTasksApi(request)
