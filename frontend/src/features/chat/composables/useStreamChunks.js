import { createStreamChunkHandler as createHandler } from '../../../shared/chat/streamChunkHandler.js'
import { setExpertLastSession } from '../../../shared/utils/expertSession.js'
import { setReactive } from '../../../utils/reactiveSet.js'
import { markStreamFlight } from '../services/streamResume.js'

export { resumeUsageBase } from '../../../shared/chat/streamUsage.js'

export function createStreamChunkHandler(messages, ctx) {
  const state = ctx.state || {}
  // getter 每次读取当前 ref，确保 runInSession 临时换入后台状态后仍写向正确会话。
  const host = {}
  const refs = { ...state, messages }
  for (const key of [
    'messages', 'sessionUsage', 'sessionDuration', 'iterationCount', 'sessionStates',
    'currentSessionId', 'currentTodos', 'loadedSessionId', 'attachedStreamingSessions',
    'pendingApproval', 'error',
  ]) {
    Object.defineProperty(host, key, {
      get: () => refs[key]?.value,
      set: value => { refs[key].value = value },
    })
  }
  host.loadSessions = () => state.loadSessions()
  host.markStreaming = sessionId => state.markStreaming(sessionId)
  host.runInSession = (sid, context, dispatch) => typeof ctx.runInSession === 'function'
    ? ctx.runInSession(sid, context, dispatch)
    : dispatch()
  return createHandler(host, ctx, {
    setReactive,
    parseMCPResult: raw => state.parseMCPResult(raw),
    onSessionCreated(sessionId) {
      if (ctx.expertId) setExpertLastSession(ctx.expertId, sessionId)
    },
    onStreamStarted(sessionId, streamSessionId) {
      markStreamFlight(streamSessionId)
      if (sessionId && state.onStreamStarted) state.onStreamStarted(sessionId)
    },
  })
}
