import test from 'node:test'
import assert from 'node:assert/strict'
import mixin from '../mixins/chatSession.js'
import { createStreamChunkHandler } from './chunkHandler.js'
import { createPageStreamChunkHandler } from './pageChunkHandler.js'
import { archiveChanged, resetSessions } from '../../../shared/sessionManagement/state.js'
import { connectSessionView } from '../../../shared/sessionManagement/viewLifecycle.js'
for (const makeHandler of [createStreamChunkHandler, createPageStreamChunkHandler]) {
  test(`${makeHandler.name}: 归档后重连、回放、结束与错误只写原会话缓存`, async () => {
    resetSessions()
    globalThis.localStorage = { setItem() {} }
    const vm = { ...mixin.data(), $set: (o,k,v) => { o[k]=v }, $nextTick: fn => fn(),
      sessions: [{ session_id: 'A' }, { session_id: 'B' }], streamingSessions: ['A'],
      refreshSessionFiles: async () => {}, syncExpertChipFromMessages() {}, loadSessions() {},
    }
    for (const [key, fn] of Object.entries(mixin.methods)) {
      if (!vm[key]) vm[key] = fn.bind(vm)
    }
    Object.defineProperty(vm, 'isStreaming', { get: () => vm.streamingSessions.includes(vm.currentSessionId) })
    vm.currentSessionId = vm.loadedSessionId = 'A'
    vm.messages = [{ id: 'answer', role: 'assistant', content: '', blocks: [], loading: true }]
    const ctx = { streamSessionId: 'A', assistantMsgId: 'answer', preStreamDuration: 0, preStreamIterationCount: 0 }
    const { onChunk } = makeHandler(vm, ctx)
    onChunk({ type: 'content', content: '前半段' })
    const off = connectSessionView(vm)
    archiveChanged('A', true)
    assert.equal(vm.currentSessionId, null)
    assert.equal(vm.loadedSessionId, null)
    assert.deepEqual(vm.messages, [])
    assert.deepEqual(vm.streamingSessions, ['A'])
    onChunk({ type: 'stream_connection', status: 'reconnecting' })
    onChunk({ type: 'content', content: '后半段' })
    assert.equal(vm.sessionStates.A.messages[0].blocks[0].content, '前半段后半段')
    const replay = [{ type: 'content', content: '前半段后半段' }, { type: 'token_usage', input_tokens: 99, output_tokens: 8, elapsed_time: 3, step_count: 2 }]
    onChunk({ type: 'stream_replay' }); replay.forEach(onChunk)
    const snapshot = JSON.stringify(vm.sessionStates.A)
    onChunk({ type: 'stream_replay' }); replay.forEach(onChunk)
    assert.equal(JSON.stringify(vm.sessionStates.A), snapshot)
    onChunk({ type: 'done', title: '任务已完成', content: '前半段后半段' })
    onChunk({ type: 'error', content: '后台错误' })
    assert.equal(vm.error, null)
    assert.equal(vm.sessionUsage.input_tokens, 0)
    assert.deepEqual(vm.messages, [])
    assert.equal(vm.sessionStates.A.sessionUsage.input_tokens, 99)
    assert.equal(vm.sessionStates.A.messages[0].error, '后台错误')
    assert.equal(vm.currentSessionId, null)
    assert.ok(vm.sessions.every(s => s.session_id))
    off()
  })
}
