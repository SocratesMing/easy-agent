import { createStreamChunkHandler as createHandler } from '../../../shared/chat/streamChunkHandler.js'
import { setExpertLastSession } from '../../../shared/utils/expertSession.js'
import { chatBus } from '../store/chatStore.js'
import { setReactive } from '../../../utils/reactiveSet.js'
import { parseMCPResult } from '../../../utils/mcpResult.js'

export { resumeUsageBase } from '../../../shared/chat/streamUsage.js'

// 侧栏只适配宿主的响应式写入、专家关联和跨组件广播。
export function createStreamChunkHandler(vm, ctx) {
  return createHandler(vm, ctx, {
    setReactive,
    parseMCPResult,
    onSessionCreated(sessionId) {
      if (ctx.expertId) setExpertLastSession(ctx.expertId, sessionId)
    },
    onStreamStarted(sessionId) {
      if (sessionId) chatBus.$emit('stream-started', { sessionId, origin: vm._uid })
    },
    onDone() { if (!ctx.isResume && typeof vm.loadSessions === 'function') vm.loadSessions() },
  })
}
