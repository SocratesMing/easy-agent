import test from 'node:test'
import assert from 'node:assert/strict'

import { createStreamChunkHandler } from './chunkHandler.js'

/**
 * 「会话信息」（输入框内的上下文占用环/弹窗）必须每个 step 刷新一次，
 * 而不是等 done 事件一次性跳值。后端每个 step 边界都会下发一帧 token_usage，
 * 这里锁死前端收到即应用的行为。
 */
function makeVm() {
  return {
    messages: [{ id: 'a1', blocks: [] }],
    sessionUsage: {
      input_tokens: 0,
      output_tokens: 0,
      reasoning_tokens: 0,
      context_tokens: 0,
      context_length: null,
    },
    sessionDuration: 0,
    iterationCount: 0,
    sessionStates: {},
    attachedStreamingSessions: {},
    currentTodos: [],
    currentSessionId: 's1',
    loadedSessionId: 's1',
    loadSessions() {},
    markStreaming() {},
    // 展示中的会话（sid === loadedSessionId）直接用组件数据，测试里等价于直接执行
    runInSession(sid, ctx, fn) {
      return fn()
    },
    $set(target, key, value) {
      target[key] = value
    },
  }
}

function makeCtx() {
  return {
    assistantMsgId: 'a1',
    streamSessionId: 's1',
    preStreamDuration: 0,
    preStreamIterationCount: 0,
    isResume: false,
    attachMode: false,
  }
}

test('token_usage 逐帧立即刷新会话信息（不等 done）', () => {
  const vm = makeVm()
  const { onChunk } = createStreamChunkHandler(vm, makeCtx())

  onChunk({ type: 'start', session_id: 's1' })

  const seen = []
  for (const step of [1, 2, 3]) {
    onChunk({
      type: 'token_usage',
      step_count: step,
      input_tokens: 100 * step,
      output_tokens: 5,
      reasoning_tokens: 0,
      context_tokens: 100 * step,
      context_length: 1000000,
      elapsed_time: step * 2,
    })
    seen.push({
      step: vm.iterationCount,
      input: vm.sessionUsage.input_tokens,
      context: vm.sessionUsage.context_tokens,
      duration: vm.sessionDuration,
    })
  }

  assert.deepEqual(seen, [
    { step: 1, input: 100, context: 100, duration: 2 },
    { step: 2, input: 200, context: 200, duration: 4 },
    { step: 3, input: 300, context: 300, duration: 6 },
  ])
})

test('重挂载（attachMode）下 token_usage 也逐帧 SET 而非累加', () => {
  const vm = makeVm()
  const ctx = makeCtx()
  ctx.attachMode = true
  const { onChunk } = createStreamChunkHandler(vm, ctx)

  for (const step of [1, 2]) {
    onChunk({
      type: 'token_usage',
      step_count: step,
      input_tokens: 100 * step,
      output_tokens: 5,
      context_tokens: 100 * step,
      elapsed_time: step * 2,
    })
  }

  assert.equal(vm.iterationCount, 2)
  assert.equal(vm.sessionUsage.context_tokens, 200)
  assert.equal(vm.sessionDuration, 4)
})
