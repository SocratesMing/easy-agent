import test from 'node:test'
import assert from 'node:assert/strict'

import { createStreamChunkHandler } from './chunkHandler.js'

/**
 * AI 侧边栏的流式状态机（与 frontend-vue2 同名文件同源）：后端的 tool_call_delta、
 * knowledge_evidence 两类事件必须在这里被消费，done 帧也不再依赖从未下发的
 * title/content，正文由 content 块拼回。
 */
function makeVm() {
  return {
    messages: [{ id: 'a1', blocks: [] }],
    sessions: [],
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
    pendingApproval: null,
    loadSessions() {},
    markStreaming() {},
    runInSession(sid, ctx, fn) { return fn() },
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

const toolBlock = vm => vm.messages[0].blocks.find(b => b.type === 'tool_call')

test('tool_call_delta 先建流式工具块，tool_call 到达后覆盖参数并清除 streaming', () => {
  const vm = makeVm()
  const { onChunk } = createStreamChunkHandler(vm, makeCtx())

  onChunk({ type: 'tool_call_delta', tool_name: 'write_file', tool_call_id: 'c1', arguments: { path: 'a.txt' }, step: 1 })
  assert.equal(toolBlock(vm).streaming, true)
  assert.deepEqual(toolBlock(vm).arguments, { path: 'a.txt' })

  onChunk({ type: 'tool_call_delta', tool_name: 'write_file', tool_call_id: 'c1', arguments: { path: 'a.txt', content: 'o' }, step: 1 })
  assert.equal(vm.messages[0].blocks.filter(b => b.type === 'tool_call').length, 1, '同一 tool_call_id 的快照合进同一张卡片')

  onChunk({ type: 'tool_call', tool_name: 'write_file', tool_call_id: 'c1', arguments: { path: 'a.txt', content: 'ok' }, step: 1 })
  assert.equal(toolBlock(vm).streaming, false)
  assert.deepEqual(toolBlock(vm).arguments, { path: 'a.txt', content: 'ok' })
})

test('knowledge_evidence 写入证据与告警，供 ChatMessage 渲染', () => {
  const vm = makeVm()
  const { onChunk } = createStreamChunkHandler(vm, makeCtx())

  onChunk({ type: 'knowledge_evidence', evidence: [{ id: 'doc-1' }], warnings: ['低相关度'] })
  assert.deepEqual(vm.messages[0].knowledge_evidence, [{ id: 'doc-1' }])
  assert.deepEqual(vm.messages[0].knowledge_warnings, ['低相关度'])
})

test('done 不带 title/content：正文由 content 块拼接，未完成的 streaming 标记被清掉', () => {
  const vm = makeVm()
  const { onChunk } = createStreamChunkHandler(vm, makeCtx())

  onChunk({ type: 'content', content: '第一段', step: 1 })
  onChunk({ type: 'content_end' })
  onChunk({ type: 'content', content: '第二段', step: 2 })
  onChunk({ type: 'tool_call_delta', tool_name: 'write_file', tool_call_id: 'c1', arguments: {}, step: 3 })
  onChunk({ type: 'done', session_id: 's1' })

  assert.equal(vm.messages[0].content, '第一段\n第二段')
  assert.equal(vm.messages[0].loading, false)
  assert.equal(toolBlock(vm).streaming, false, 'done 应兜底清掉未完成的 streaming 标记')
  assert.deepEqual(vm.sessions, [], '会话标题由发送收尾时的 loadSessions() 回填，done 不改会话列表')
})
