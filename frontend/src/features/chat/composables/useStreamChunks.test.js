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
import { markStreamFlight, readStreamFlight } from '../services/streamResume.js'

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
  return { messages, state, ctx, handler }
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
  const { messages, ctx, handler } = makeHarness()
  delete ctx.runInSession
  handler.onChunk({ type: 'content', content: 'ok' })
  assert.ok(messages.value[0])
})

test('主页面回放重建本轮内容，不重复正文和累计用量', () => {
  const { messages, state, handler } = makeHarness()
  const events = [{ type: 'content', content: '风险分析', step: 1 }, { type: 'token_usage', elapsed_time: 2, step_count: 1 }]
  for (const event of events) handler.onChunk(event)
  const expected = JSON.stringify(messages.value)
  handler.onChunk({ type: 'stream_replay' })
  for (const event of events) handler.onChunk(event)
  assert.equal(JSON.stringify(messages.value), expected)
  assert.equal(state.sessionDuration.value, 2)
  assert.equal(state.iterationCount.value, 1)
})

test('knowledge_evidence 写入证据与告警，供 ChatMessage 渲染', () => {
  const { messages, handler } = makeHarness()
  handler.onChunk({ type: 'knowledge_evidence', evidence: [{ id: 'doc-1' }], warnings: ['低相关度'] })
  assert.deepEqual(messages.value[0].knowledge_evidence, [{ id: 'doc-1' }])
  assert.deepEqual(messages.value[0].knowledge_warnings, ['低相关度'])
})

test('done 不带 content/title：正文由各段 content 块拼回，未完成的 streaming 标记被清掉', () => {
  const { messages, state, handler } = makeHarness()
  handler.onChunk({ type: 'content', content: '第一段', step: 1 })
  handler.onChunk({ type: 'content_end' })
  handler.onChunk({ type: 'content', content: '第二段', step: 2 })
  handler.onChunk({ type: 'content_end' })
  handler.onChunk({ type: 'tool_call_delta', tool_name: 'write_file', tool_call_id: 'c1', arguments: {}, step: 3 })
  handler.onChunk({ type: 'done', session_id: 's1', elapsed_time: 1.2 })

  const msg = messages.value[0]
  assert.equal(msg.content, '第一段\n第二段')
  assert.equal(msg.loading, false)
  assert.equal(msg.blocks.find(b => b.type === 'tool_call').streaming, false)
  assert.deepEqual(state.sessions.value, [], '会话标题由收尾时的 loadSessions() 回填，done 不改会话列表')
})

test('start 补全在飞记录；回放不覆盖其它新流的记录，也不重复广播', (t) => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
  const storage = new Map()
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
  } })
  t.after(() => {
    if (original) Object.defineProperty(globalThis, 'sessionStorage', original)
    else delete globalThis.sessionStorage
  })
  let now = 1000
  t.mock.method(Date, 'now', () => now)
  const started = []
  const { messages, ctx } = makeHarness()
  ctx.state.onStreamStarted = sid => started.push(sid)
  const stream = createStreamChunkHandler(messages, ctx)
  markStreamFlight(null)
  stream.onChunk({ type: 'start', session_id: 's1' })
  assert.deepEqual(readStreamFlight(now), { sessionId: 's1', at: 1000 })

  now = 2000
  markStreamFlight('s2')
  stream.onChunk({ type: 'stream_replay' })
  stream.onChunk({ type: 'start', session_id: 's1' })
  assert.deepEqual(readStreamFlight(now), { sessionId: 's2', at: 2000 })
  assert.deepEqual(started, ['s1'])
})
