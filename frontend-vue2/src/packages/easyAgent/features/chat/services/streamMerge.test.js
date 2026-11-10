import assert from 'node:assert/strict'
import test from 'node:test'
import Vue from 'vue'
import chatSessionMixin from '../mixins/chatSession.js'
import { createStreamChunkHandler, resumeUsageBase } from './chunkHandler.js'
import { createPageStreamChunkHandler } from './pageChunkHandler.js'
import { markStreamFlight, readStreamFlight } from './streamResume.js'

const plain = value => JSON.parse(JSON.stringify(value))
const message = id => ({ id, role: 'assistant', content: '', blocks: [], loading: true, usage: { cached: true } })
const usage = (step = 3, elapsed = 9) => ({
  input_tokens: 100, output_tokens: 20, reasoning_tokens: 5, context_tokens: 120,
  step_count: step, elapsed_time: elapsed,
})

function harness(createHandler, { background = false, attachMode = false } = {}) {
  // 使用真实 Vue2 响应式数据和宿主的会话切换方法，不执行页面初始化、网络或轮询。
  const vm = new Vue({
    data: () => ({
      messages: [message(background ? 'b1' : 'a1')], sessionUsage: { context_tokens: 0 },
      sessionDuration: 20, iterationCount: 5, currentTodos: [], streamingAssistantId: null,
      pendingApproval: null, error: null, attachedStreamingSessions: {}, sessions: [],
      currentSessionId: 's2', loadedSessionId: background ? 's2' : 's1',
      sessionStates: { s1: {
        messages: [message('a1')], sessionUsage: { context_tokens: 0 }, sessionDuration: 20,
        iterationCount: 5, todos: [], streamingAssistantId: 'a1', pendingApproval: null,
      } },
    }),
    methods: {
      ensureSessionState: chatSessionMixin.methods.ensureSessionState,
      runInSession: chatSessionMixin.methods.runInSession,
      loadSessions() {}, markStreaming() {},
    },
  })
  const ctx = { assistantMsgId: 'a1', streamSessionId: 's1', attachMode, preStreamDuration: 20, preStreamIterationCount: 5 }
  return { vm, ctx, onChunk: createHandler(vm, ctx).onChunk, target: () => background ? vm.sessionStates.s1 : vm }
}

for (const [name, createHandler] of [['sidebar', createStreamChunkHandler], ['page', createPageStreamChunkHandler]]) {
  for (const background of [false, true]) {
    for (const attachMode of [false, true]) {
      test(`${name}: token_usage 和 done 保留消息字段，background=${background}, attachMode=${attachMode}`, (t) => {
        const h = harness(createHandler, { background, attachMode })
        t.after(() => h.vm.$destroy())
        const before = plain({ messages: h.vm.messages, usage: h.vm.sessionUsage, duration: h.vm.sessionDuration, iterations: h.vm.iterationCount })
        h.onChunk({ type: 'token_usage', ...usage() })
        assert.deepEqual(plain(h.target().messages[0].usage), { cached: true, ...usage() })
        h.onChunk({ type: 'done', elapsed_time: 10, usage: usage(4, 9), blocks: [] })
        const target = h.target()
        assert.deepEqual(plain(target.messages[0].usage), { cached: true, ...usage(4, 10) })
        assert.equal(target.sessionUsage.context_tokens, 120)
        assert.equal(target.sessionDuration, attachMode ? 10 : 30)
        assert.equal(target.iterationCount, attachMode ? 4 : 9)
        if (background) {
          assert.deepEqual(plain({ messages: h.vm.messages, usage: h.vm.sessionUsage, duration: h.vm.sessionDuration, iterations: h.vm.iterationCount }), before)
          assert.equal(h.ctx.stateSwapped, false)
          assert.equal(h.ctx.assistantMsgId, 'a1')
        }
      })
    }
  }

  test(`${name}: Vue2 观察者在首个用量事件后收到新属性`, async (t) => {
    const h = harness(createHandler)
    t.after(() => h.vm.$destroy())
    const seen = []
    h.vm.$watch(() => h.vm.messages[0].usage.context_tokens, value => seen.push(value))
    h.onChunk({ type: 'token_usage', ...usage() })
    await Vue.nextTick()
    assert.deepEqual(seen, [120])
    h.onChunk({ type: 'token_usage', step_count: 0 })
    assert.equal(h.vm.messages[0].usage.elapsed_time, 9, '缺失耗时不应清空已有累计值')
    h.onChunk({ type: 'token_usage', elapsed_time: 0 })
    assert.equal(h.vm.messages[0].usage.step_count, 0)
    assert.equal(h.vm.messages[0].usage.elapsed_time, 0)
  })

  for (const refreshed of [false, true]) {
    test(`${name}: 审批续跑只计入本消息的最新累计值，refreshed=${refreshed}`, (t) => {
      const h = harness(createHandler)
      t.after(() => h.vm.$destroy())
      if (refreshed) {
        h.vm.messages[0].usage = usage()
        h.vm.sessionDuration = 29
        h.vm.iterationCount = 8
      } else {
        h.onChunk({ type: 'token_usage', ...usage() })
      }
      const ctx = { ...h.ctx, isResume: true, ...resumeUsageBase(h.vm.sessionDuration, h.vm.iterationCount, h.vm.messages[0]) }
      const { onChunk } = createHandler(h.vm, ctx)
      onChunk({ type: 'token_usage', ...usage(4, 12.4) })
      onChunk({ type: 'token_usage', ...usage(5, 16.4) })
      onChunk({ type: 'done', elapsed_time: 17.4, usage: usage(5, 17.4), blocks: [{ type: 'tool_call', streaming: true }] })
      assert.equal(h.vm.sessionDuration, 37.4)
      assert.equal(h.vm.iterationCount, 10)
      assert.equal(h.vm.messages[0].usage.elapsed_time, 17.4)
      assert.equal(h.vm.messages[0].blocks[0].streaming, false)
    })
  }

  test(`${name}: done 正文从全部内容块拼回`, (t) => {
    const h = harness(createHandler)
    t.after(() => h.vm.$destroy())
    for (const step of [1, 2]) {
      h.onChunk({ type: 'content', step, content: `段落${step}` })
      h.onChunk({ type: 'content_end' })
    }
    h.onChunk({ type: 'done', session_id: 's1' })
    assert.equal(h.vm.messages[0].content, '段落1\n段落2')
    assert.equal(h.vm.messages[0].loading, false)
  })

  for (const timeout of [false, true]) {
    test(`${name}: ${timeout ? '超时显示灰条' : '普通错误保留错误提示'}`, async (t) => {
      const h = harness(createHandler)
      t.after(() => h.vm.$destroy())
      const seen = []
      h.vm.$watch(() => h.vm.messages[0].fallback_note, value => seen.push(value))
      h.onChunk({ type: 'content', content: '已生成正文', step: 1 })
      h.onChunk({ type: 'error', code: timeout ? 'timeout' : undefined, content: '执行中断' })
      await Vue.nextTick()
      const msg = h.vm.messages[0]
      assert.equal(msg.loading, false)
      assert.ok(msg.blocks.some(b => b.content === '已生成正文'))
      if (timeout) {
        assert.equal(msg.fallback_note, '执行中断')
        assert.equal(msg.error, undefined)
        assert.equal(h.vm.error, null)
        assert.deepEqual(seen, ['执行中断'])
      } else {
        assert.equal(msg.error, '执行中断')
        assert.equal(h.vm.error, '执行中断')
        assert.equal(msg.fallback_note, undefined)
      }
    })
  }
}

test('page: start 补全刷新记录，回放不覆盖新流的记录', (t) => {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')
  const values = new Map()
  Object.defineProperty(globalThis, 'sessionStorage', { configurable: true, value: {
    getItem: key => values.get(key) ?? null,
    setItem: (key, value) => values.set(key, value),
  } })
  t.after(() => {
    if (original) Object.defineProperty(globalThis, 'sessionStorage', original)
    else delete globalThis.sessionStorage
  })
  let now = 1000
  t.mock.method(Date, 'now', () => now)
  const h = harness(createPageStreamChunkHandler)
  t.after(() => h.vm.$destroy())
  markStreamFlight(null)
  h.onChunk({ type: 'start', session_id: 's1' })
  assert.deepEqual(readStreamFlight(now), { sessionId: 's1', at: 1000 })
  now = 2000
  markStreamFlight('s2')
  h.onChunk({ type: 'stream_replay' })
  h.onChunk({ type: 'start', session_id: 's1' })
  assert.deepEqual(readStreamFlight(now), { sessionId: 's2', at: 2000 })
})
