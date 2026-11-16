// 聊天请求只发送一次；传输中断后用只读 live 从头回放，直到业务终态或用户停止。
const TERMINAL_TYPES = new Set(['done', 'error', 'approval_required'])
export const STREAM_RECONNECTING_MESSAGE = '连接中断，正在重新连接，可点击停止'

function waitForRetry(milliseconds, signal) {
  return new Promise(resolve => {
    if (signal && signal.aborted) return resolve()
    const finish = () => {
      clearTimeout(timer)
      if (signal) signal.removeEventListener('abort', finish)
      resolve()
    }
    const timer = setTimeout(finish, milliseconds)
    if (signal) signal.addEventListener('abort', finish, { once: true })
  })
}

function isUnavailable(error) {
  const status = error && (error.status || (error.response && error.response.status))
  return status === 401 || status === 403 || status === 404
}

export async function recoverChatStream({
  response, sessionId, onChunk, signal, readSSE, getStatus, openLive,
  wait = waitForRetry,
}) {
  let current = response
  let replay = false
  let attempt = 0
  const aborted = () => signal && signal.aborted
  try {
    while (!aborted()) {
      if (current) {
        let terminal = false
        let callbackError = null
        let firstEvent = true
        try {
          await readSSE(current, event => {
            try {
              if (event.type === 'start' && event.session_id) sessionId = event.session_id
              // 只在回放数据真正到达后重建本轮；连接失败期间保留屏幕上的部分内容。
              if (replay && firstEvent) {
                onChunk({ type: 'stream_replay' })
                onChunk({ type: 'stream_connection', status: 'connected' })
              }
              firstEvent = false
              onChunk(event)
              if (TERMINAL_TYPES.has(event.type)) terminal = true
            } catch (error) {
              callbackError = error
              throw error
            }
          }, { signal })
        } catch (error) {
          if (callbackError) throw callbackError
          if (!terminal && !aborted() && !sessionId) throw error
        }
        if (terminal || aborted()) return
        // 没有会话身份的短路拒答流无法回放；后端正常会发送 error/done。
        if (!sessionId) throw new Error('连接中断，未获取到会话标识，请刷新后检查会话')
        current = null
      }
      onChunk({ type: 'stream_connection', status: 'reconnecting' })
      await wait(Math.min(250 * (2 ** Math.min(attempt++, 5)), 5000), signal)
      if (aborted()) return
      try {
        // 查询异常不表示任务结束；即使已结束也回放最后内容和终态。
        await getStatus(sessionId, signal)
        if (aborted()) return
        current = await openLive(sessionId, signal)
        replay = true
      } catch (error) {
        if (aborted()) return
        if (isUnavailable(error)) throw error
        // 暂时离线/网关错误继续退避，保留上层 Promise 和停止按钮。
      }
    }
  } finally {
    if (aborted()) onChunk({ type: 'stream_connection', status: 'stopped' })
  }
}

// 状态机在每次从头回放前恢复本轮开始时的快照，兼容正文增量和 HITL 续跑。
export function createStreamCheckpoint(read, restore) {
  let checkpoint
  const clone = value => JSON.parse(JSON.stringify(value))
  return {
    capture() { if (!checkpoint) checkpoint = clone(read()) },
    restore() { if (checkpoint) restore(clone(checkpoint)) },
  }
}
