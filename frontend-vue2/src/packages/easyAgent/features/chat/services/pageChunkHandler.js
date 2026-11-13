import { createStreamChunkHandler } from '../../../shared/chat/streamChunkHandler.js'
import { setReactive } from '../../../utils/reactiveSet.js'
import { parseMCPResult } from '../../../utils/mcpResult.js'
import { markStreamFlight } from './streamResume.js'

export function createPageStreamChunkHandler(vm, ctx) {
  return createStreamChunkHandler(vm, ctx, {
    setReactive,
    parseMCPResult,
    onStreamStarted(sessionId, streamSessionId) { markStreamFlight(streamSessionId) },
  })
}
