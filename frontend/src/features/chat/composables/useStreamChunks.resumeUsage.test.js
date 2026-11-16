/**
 * 审批续跑后「会话信息」的耗时 / 迭代次数（与 Vue2 chunkHandler.resumeUsage.test.js 同一场景）。
 *
 * 后端下发的 elapsed_time / step_count 是这条消息的累计值，续跑接着中断前往下算。
 * 此前续跑分支把每帧的累计耗时反复相加、把迭代次数直接设成这条消息的步数。
 */

import assert from 'node:assert/strict'
import test from 'node:test'
import { createStreamChunkHandler, resumeUsageBase } from './useStreamChunks.js'

const ref = (value) => ({ value })

function makeState({ duration, iterations }) {
  return {
    sessionUsage: ref({ context_tokens: 0 }),
    sessionDuration: ref(duration),
    iterationCount: ref(iterations),
    sessionStates: ref({}),
    currentSessionId: ref('s1'),
    currentTodos: ref([]),
    loadedSessionId: ref('s1'),
    attachedStreamingSessions: ref({}),
    sessions: ref([]),
    pendingApproval: ref(null),
    error: ref(''),
    markStreaming: () => {},
    loadSessions: () => {},
    parseMCPResult: (raw) => raw,
  }
}

const usage = (step, elapsed) => ({
  step_count: step, elapsed_time: elapsed, input_tokens: 1000 * step, output_tokens: 50,
  reasoning_tokens: 0, context_tokens: 1000 * step, context_length: 128000,
})

const runInSession = (_sid, _ctx, fn) => fn()

// 批准后续跑：后端计时接着中断前的 9.4 秒，步数接着第 3 步
function runResume(messages, state) {
  const { onChunk } = createStreamChunkHandler(messages, {
    isResume: true,
    assistantMsgId: 'a1',
    streamSessionId: 's1',
    ...resumeUsageBase(state.sessionDuration.value, state.iterationCount.value, messages.value[0]),
    runInSession,
    state,
  })
  onChunk({ type: 'token_usage', ...usage(4, 12.4) })
  onChunk({ type: 'token_usage', ...usage(5, 16.4) })
  onChunk({ type: 'done', session_id: 's1', elapsed_time: 17.4, usage: usage(5, 17.4), blocks: [] })
}

test('不刷新页面直接批准：续跑后耗时与迭代次数 = 之前各轮 + 这条消息的累计值', () => {
  const messages = ref([{ id: 'a1', role: 'assistant', content: '', blocks: [], loading: true }])
  const state = makeState({ duration: 20, iterations: 5 })
  const { onChunk } = createStreamChunkHandler(messages, {
    assistantMsgId: 'a1', streamSessionId: 's1', isResume: false,
    preStreamDuration: 20, preStreamIterationCount: 5, runInSession, state,
  })
  for (const [step, elapsed] of [[1, 3], [2, 6], [3, 9]]) onChunk({ type: 'token_usage', ...usage(step, elapsed) })
  assert.equal(state.sessionDuration.value, 29)
  assert.equal(state.iterationCount.value, 8)

  runResume(messages, state)

  assert.equal(state.sessionDuration.value, 37.4)
  assert.equal(state.iterationCount.value, 10)
})

test('刷新后再批准：用历史消息落库的耗时 / 步数扣除中断前已计入的部分', () => {
  const messages = ref([{ id: 'a1', role: 'assistant', content: '', blocks: [], usage: { step_count: 3, elapsed_time: 9.4 } }])
  const state = makeState({ duration: 29.4, iterations: 8 })

  runResume(messages, state)

  assert.equal(state.sessionDuration.value, 37.4)
  assert.equal(state.iterationCount.value, 10)
})
