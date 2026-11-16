import test from 'node:test'
import assert from 'node:assert/strict'
import { createStreamChunkHandler as sidebar } from './chunkHandler.js'
import { createPageStreamChunkHandler as page } from './pageChunkHandler.js'
const factories = [['sidebar', sidebar], ['page', page]]
function harness(create, resume = false) {
  const vm = {
    messages: [{ id: 'a1', blocks: [], loading: true }],
    sessionUsage: {}, sessionDuration: 0, iterationCount: 0,
    currentSessionId: 's1', loadedSessionId: 's1', currentTodos: [],
    sessionStates: {}, attachedStreamingSessions: {}, pendingApproval: null, error: null,
    $set: (obj, key, value) => { obj[key] = value },
    markStreaming() {}, loadSessions() {}, runInSession(sid, ctx, fn) { return fn() },
  }
  const ctx = { assistantMsgId: 'a1', streamSessionId: 's1', isResume: resume, preStreamDuration: 0, preStreamIterationCount: 0 }
  return { vm, onChunk: create(vm, ctx).onChunk }
}

for (const [name, create] of factories) {
  for (const resume of [false, true]) test(`${name}: 临时工具 ID 升级，resume=${resume}`, () => {
    const { vm, onChunk } = harness(create, resume)
    const events = [
      { type: 'tool_call_delta', tool_name: 'write_file', tool_call_id: 'tool-write_file', arguments: { path: 'probe.txt' }, step: 1 },
      { type: 'tool_call', tool_name: 'write_file', tool_call_id: 'c1', arguments: { path: 'probe.txt', content: 'synthetic' }, step: 1 },
      { type: 'tool_result', tool_name: 'write_file', tool_call_id: 'c1', result: 'ok', success: true, duration: 2, step: 1 },
    ]
    for (let replay = 0; replay < 2; replay++) {
      if (replay) onChunk({ type: 'stream_replay' })
      for (const event of events) onChunk(event)
      const tools = vm.messages[0].blocks.filter(b => b.type === 'tool_call')
      assert.equal(tools.length, 1)
      assert.equal(tools[0].id, 'c1')
      assert.equal(tools[0].streaming, false)
      assert.equal(tools[0].result, 'ok')
      assert.deepEqual(tools[0].arguments, { path: 'probe.txt', content: 'synthetic' })
    }
    onChunk({ type: 'done' })
    if (!resume) {
      assert.equal(vm.messages[0].tool_calls.length, 1)
      assert.equal(vm.messages[0].tool_calls[0].tool_call_id, 'c1')
      assert.equal(vm.messages[0].tool_calls[0].result, 'ok')
    }
  })

  test(`${name}: 并行同名工具正式 ID 不互相吞并`, () => {
    const { vm, onChunk } = harness(create)
    for (const id of ['c1', 'c2']) onChunk({ type: 'tool_call_delta', tool_name: 'write_file', tool_call_id: id, arguments: { path: id }, step: 1 })
    for (const id of ['c2', 'c1']) {
      onChunk({ type: 'tool_call', tool_name: 'write_file', tool_call_id: id, arguments: { path: id }, step: 1 })
      onChunk({ type: 'tool_result', tool_name: 'write_file', tool_call_id: id, result: id, step: 1 })
    }
    onChunk({ type: 'done' })
    const tools = vm.messages[0].blocks.filter(b => b.type === 'tool_call')
    assert.equal(tools.length, 2)
    assert.deepEqual(tools.map(b => [b.id, b.result]), [['c1', 'c1'], ['c2', 'c2']])
    assert.equal(vm.messages[0].tool_calls.length, 2)
  })

  test(`${name}: 新的正式调用不吞并正在生成的同名正式调用`, () => {
    const { vm, onChunk } = harness(create)
    onChunk({ type: 'tool_call_delta', tool_name: 'write_file', tool_call_id: 'c1', step: 1 })
    onChunk({ type: 'tool_call', tool_name: 'write_file', tool_call_id: 'c2', step: 1 })
    assert.deepEqual(vm.messages[0].blocks.map(b => b.id), ['c1', 'c2'])
    assert.equal(vm.messages[0].blocks[0].streaming, true)
  })

  test(`${name}: 分片阶段取得正式 ID 后继续更新同一卡片`, () => {
    const { vm, onChunk } = harness(create)
    onChunk({ type: 'tool_call_delta', tool_name: 'write_file', arguments: { path: 'x' }, step: 1 })
    onChunk({ type: 'tool_call_delta', tool_name: 'write_file', tool_call_id: 'c1', arguments: { path: 'x', content: 'ok' }, step: 1 })
    onChunk({ type: 'tool_result', tool_name: 'write_file', tool_call_id: 'c1', result: 'ok', step: 1 })
    onChunk({ type: 'done' })
    assert.equal(vm.messages[0].blocks.length, 1)
    assert.equal(vm.messages[0].blocks[0].id, 'c1')
    assert.equal(vm.messages[0].blocks[0].result, 'ok')
    assert.equal(vm.messages[0].tool_calls.length, 1)
    assert.equal(vm.messages[0].tool_calls[0].tool_call_id, 'c1')
  })

  test(`${name}: 不同 step 的临时占位不合并`, () => {
    const { vm, onChunk } = harness(create)
    onChunk({ type: 'tool_call_delta', tool_name: 'write_file', step: 1 })
    onChunk({ type: 'tool_call', tool_name: 'write_file', tool_call_id: 'c2', step: 2 })
    assert.equal(vm.messages[0].blocks.length, 2)
  })

  test(`${name}: 缺少 tool_call 时结果仍完成临时卡片`, () => {
    const { vm, onChunk } = harness(create)
    onChunk({ type: 'tool_call_delta', tool_name: 'write_file', arguments: { path: 'x' }, step: 1 })
    onChunk({ type: 'tool_result', tool_name: 'write_file', tool_call_id: 'c1', result: 'ok', step: 1 })
    const block = vm.messages[0].blocks[0]
    assert.equal(block.id, 'c1')
    assert.equal(block.streaming, false)
    assert.equal(block.result, 'ok')
    onChunk({ type: 'done' })
    assert.equal(vm.messages[0].tool_calls.length, 1)
    assert.equal(vm.messages[0].tool_calls[0].tool_call_id, 'c1')
    assert.equal(vm.messages[0].tool_calls[0].result, 'ok')
  })
}
