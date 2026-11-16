/**
 * 审批续跑下发的是当前消息的累计用量；会话基数先扣掉中断前已计入的部分。
 * 消息用量来自历史记录，或流式处理器保存的最近一次累计值。
 */
export function resumeUsageBase(sessionDuration, iterationCount, message) {
  const usage = (message && message.usage) || {}
  return {
    preStreamDuration: Math.max(0, (sessionDuration || 0) - (usage.elapsed_time || 0)),
    preStreamIterationCount: Math.max(0, (iterationCount || 0) - (usage.step_count || 0)),
  }
}
