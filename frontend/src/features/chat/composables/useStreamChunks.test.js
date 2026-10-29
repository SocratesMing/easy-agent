/**
 * 回归测试：流式状态机抽取成模块后，不能依赖 App.vue 作用域里的裸变量。
 *
 * 背景：2026-10-20 抽出 useStreamChunks 后，模块里仍直接引用 runInSession /
 * sessionUsage / sessions 等页面级 refs，构建与单测都发现不了，直到用户发消息
 * 才在运行期抛 `runInSession is not defined`。这里用最小桩驱动一次事件流，
 * 保证这类「未定义标识符」在 CI 就能暴露。
 */

import assert from 'node:assert/strict'
import test from 'node:test'
import { createStreamChunkHandler } from './useStreamChunks.js'

const ref = (value) => ({ value })

function makeHarness() {
  const messages = ref([
    { id: 'a1', role: 'assistant', content: '', blocks: [], loading: true },
  ])
  const state = {
    sessionUsage: ref({ context_tokens: 0 }),
    sessionDuration: ref(0),
    iterationCount: ref(0),
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
  const ctx = {
    assistantMsgId: 'a1',
    streamSessionId: 's1',
    isResume: false,
    initialBlockOrder: 0,
    preStreamDuration: 0,
    preStreamIterationCount: 0,
    state,
    runInSession: (_sid, _ctx, fn) => fn(),
  }
  const handler = createStreamChunkHandler(messages, ctx)
  return { messages, state, handler }
}

test('content 事件写入消息且不抛未定义标识符', () => {
  const { messages, handler } = makeHarness()
  handler.onChunk({ type: 'content', content: '你好' })
  const msg = messages.value[0]
  assert.equal(msg.loading, true)
  assert.ok(JSON.stringify(msg).includes('你好'), '正文应写入 blocks')
})

test('error 事件落错误信息且清 loading', () => {
  const { messages, handler } = makeHarness()
  handler.onChunk({ type: 'error', content: '模型调用失败' })
  const msg = messages.value[0]
  assert.equal(msg.loading, false)
  assert.ok(JSON.stringify(msg).includes('模型调用失败'))
})

test('meta/usage 事件更新会话用量', () => {
  const { state, handler } = makeHarness()
  handler.onChunk({ type: 'token_usage', context_tokens: 123, step_count: 2, elapsed_time: 1.5 })
  assert.equal(state.sessionUsage.value.context_tokens, 123)
  assert.equal(state.iterationCount.value, 2)
})

test('没有注入 runInSession 时也能派发（防御分支）', () => {
  const { messages, handler } = makeHarness()
  handler.onChunk({ type: 'content', content: 'ok' })
  assert.ok(messages.value[0])
})
