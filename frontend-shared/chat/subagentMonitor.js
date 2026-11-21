/**
 * 子智能体顶部监控面板的纯数据派生（双端共享）。
 *
 * 从会话 messages 中提取所有 `task` 工具调用及其 `subagent_steps`，
 * 供标题栏左侧的 SubagentMonitorPanel 渲染收起态胶囊与展开态浮层。
 */

function firstLine(text) {
  const s = String(text == null ? '' : text).trim()
  return s ? s.split('\n')[0].trim() : ''
}

function clip(text, max = 64) {
  const s = String(text == null ? '' : text).trim()
  return s.length > max ? `${s.slice(0, max)}…` : s
}

export function formatSubagentDuration(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds))) return ''
  const total = Math.max(0, Number(seconds))
  if (total < 60) {
    const rounded = total < 10 ? Math.round(total * 10) / 10 : Math.round(total)
    return `${rounded}秒`
  }
  const mins = Math.floor(total / 60)
  const secs = Math.round(total % 60)
  if (mins < 60) {
    return secs > 0 ? `${mins}分${secs}秒` : `${mins}分钟`
  }
  const hours = Math.floor(mins / 60)
  const remMins = mins % 60
  return `${hours}小时${remMins}分`
}

function normalizeSteps(raw) {
  if (!Array.isArray(raw)) return []
  return raw
    .filter(item => item && typeof item === 'object')
    .map(item => ({
      stage: String(item.stage || 'tool_call'),
      toolName: String(item.tool_name || item.toolName || ''),
      summary: String(item.summary || item.tool_name || '执行中'),
      success: item.success === false ? false : (item.success === true ? true : null),
      step: Number(item.step || 0),
    }))
}

/**
 * 从会话消息列表提取所有子智能体任务（按出现顺序）。
 */
export function extractSubagentTasks(messages) {
  const list = Array.isArray(messages) ? messages : []
  const tasks = []
  for (let mi = 0; mi < list.length; mi += 1) {
    const msg = list[mi]
    if (!msg || msg.role !== 'assistant') continue
    const blocks = Array.isArray(msg.blocks) ? msg.blocks : []
    for (let bi = 0; bi < blocks.length; bi += 1) {
      const b = blocks[bi]
      if (!b || b.type !== 'tool_call' || b.tool_name !== 'task') continue
      const args = b.arguments && typeof b.arguments === 'object' ? b.arguments : {}
      const steps = normalizeSteps(b.subagent_steps)
      const latestStep = steps.length ? steps[steps.length - 1] : null
      const resultText = typeof b.result === 'string'
        ? b.result.trim()
        : (b.result == null ? '' : JSON.stringify(b.result))
      const isError = b.success === false
      const isRunning = !isError && b.duration == null && !b.pending_approval && msg.loading === true
      const status = isError ? 'error' : (isRunning ? 'running' : 'completed')
      const rawId = String(b.tool_call_id || b.id || `task-${mi}-${bi}`)
      tasks.push({
        id: rawId,
        title: clip(firstLine(args.description) || '执行子任务', 72),
        subagentType: String(args.subagent_type || 'general-purpose'),
        status,
        duration: b.duration != null ? b.duration : null,
        durationLabel: formatSubagentDuration(b.duration),
        steps,
        stepCount: steps.length,
        latestSummary: latestStep ? latestStep.summary : '',
        resultPreview: clip(resultText, 240),
        resultFull: resultText,
      })
    }
  }
  return tasks
}

/**
 * 计算标题栏左侧胶囊与浮层 Header 的汇总状态与文案。
 */
export function summarizeSubagentPill(tasks) {
  const list = Array.isArray(tasks) ? tasks : []
  const total = list.length
  const completed = list.filter(t => t.status === 'completed').length
  const failed = list.filter(t => t.status === 'error').length
  const runningTasks = list.filter(t => t.status === 'running')
  const running = runningTasks.length
  const hasRunning = running > 0
  const finished = completed + failed
  const progressPercent = total > 0 ? Math.round((finished / total) * 100) : 0

  const parts = []
  if (running > 0) parts.push(`${running} 进行中`)
  if (completed > 0) parts.push(`${completed} 已完成`)
  if (failed > 0) parts.push(`${failed} 失败`)
  const statusSummary = parts.join(' · ')

  let pillLabel = `${total} 个子智能体`
  if (hasRunning && running === 1 && runningTasks[0].latestSummary) {
    pillLabel = `${total} 个子智能体 · ${clip(runningTasks[0].latestSummary, 14)}`
  }

  return {
    total,
    completed,
    failed,
    finished,
    running,
    hasRunning,
    progressPercent,
    statusSummary,
    pillLabel,
  }
}
