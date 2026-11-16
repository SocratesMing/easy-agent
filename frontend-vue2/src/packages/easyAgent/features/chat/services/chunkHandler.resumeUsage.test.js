import test from 'node:test'
import assert from 'node:assert/strict'

import { createStreamChunkHandler, resumeUsageBase } from './chunkHandler.js'

/**
 * 审批续跑后「会话信息」的耗时 / 迭代次数。
 *
 * 后端下发的 elapsed_time / step_count 是这条消息的累计值，续跑接着中断前往下算。
 * 此前续跑分支把每帧的累计耗时反复相加、把迭代次数直接设成这条消息的步数：
 * 前面 30 秒 / 8 步、批准后再跑 2 步 8 秒，显示成 48 秒 / 5 步（应为 38 秒 / 10 步）。
 */
function makeVm({ duration, iterations, message }) {
  return {
    messages: [message],
    sessionUsage: { input_tokens: 0, output_tokens: 0, reasoning_tokens: 0, context_tokens: 0, context_length: 128000 },
    sessionDuration: duration,
    iterationCount: iterations,
    sessionStates: {},
    attachedStreamingSessions: {},
    currentTodos: [],
    pendingApproval: null,
    currentSessionId: 's1',
    loadedSessionId: 's1',
    loadSessions() {},
    markStreaming() {},
    runInSession(sid, ctx, fn) { return fn() },
    $set(target, key, value) { target[key] = value },
  }
}

const usage = (step, elapsed) => ({
  step_count: step, elapsed_time: elapsed, input_tokens: 1000 * step, output_tokens: 50,
  reasoning_tokens: 0, context_tokens: 1000 * step, context_length: 128000,
})

// 批准后续跑：后端计时接着中断前的 9.4 秒，步数接着第 3 步
function runResume(vm) {
  const ctx = {
    isResume: true,
    assistantMsgId: 'a1',
    streamSessionId: 's1',
    ...resumeUsageBase(vm.sessionDuration, vm.iterationCount, vm.messages[0]),
  }
  const { onChunk } = createStreamChunkHandler(vm, ctx)
  onChunk({ type: 'start', session_id: 's1' })
  onChunk({ type: 'token_usage', ...usage(4, 12.4) })
  onChunk({ type: 'token_usage', ...usage(5, 16.4) })
  onChunk({ type: 'done', session_id: 's1', elapsed_time: 17.4, usage: usage(5, 17.4), blocks: [] })
}

test('不刷新页面直接批准：续跑后耗时与迭代次数 = 之前各轮 + 这条消息的累计值', () => {
  // 之前各轮共 20 秒 / 5 步；本轮跑到第 3 步、9 秒时停在审批
  const vm = makeVm({ duration: 20, iterations: 5, message: { id: 'a1', role: 'assistant', blocks: [] } })
  const { onChunk } = createStreamChunkHandler(vm, {
    assistantMsgId: 'a1', streamSessionId: 's1', preStreamDuration: 20, preStreamIterationCount: 5,
  })
  onChunk({ type: 'start', session_id: 's1' })
  for (const [step, elapsed] of [[1, 3], [2, 6], [3, 9]]) onChunk({ type: 'token_usage', ...usage(step, elapsed) })
  assert.equal(vm.sessionDuration, 29)
  assert.equal(vm.iterationCount, 8)

  runResume(vm)

  assert.equal(vm.sessionDuration, 37.4)
  assert.equal(vm.iterationCount, 10)
})

test('刷新后再批准：用历史消息落库的耗时 / 步数扣除中断前已计入的部分', () => {
  const message = { id: 'a1', role: 'assistant', blocks: [], usage: { step_count: 3, elapsed_time: 9.4 } }
  const vm = makeVm({ duration: 29.4, iterations: 8, message })

  runResume(vm)

  assert.equal(vm.sessionDuration, 37.4)
  assert.equal(vm.iterationCount, 10)
})

test('resumeUsageBase：消息没有用量时不扣，扣多了也不会出现负数', () => {
  assert.deepEqual(resumeUsageBase(30, 8, { id: 'a1' }), { preStreamDuration: 30, preStreamIterationCount: 8 })
  assert.deepEqual(resumeUsageBase(30, 8, undefined), { preStreamDuration: 30, preStreamIterationCount: 8 })
  assert.deepEqual(
    resumeUsageBase(5, 1, { usage: { elapsed_time: 9, step_count: 3 } }),
    { preStreamDuration: 0, preStreamIterationCount: 0 },
  )
})
