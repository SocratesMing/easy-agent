import test from 'node:test'
import assert from 'node:assert/strict'
import {
  freezeReadonlyMessageParts,
  freezeHistoryMessages,
  lastMessageRevisionKey,
} from './freezeMessage.js'

test('freezeReadonlyMessageParts freezes nested readonly payloads while keeping message shell mutable', () => {
  const msg = {
    id: 'm1',
    loading: false,
    pending_approval: { tool_name: 'delete' },
    knowledge_evidence: [{ doc: 'a.pdf', score: 0.9 }],
    knowledge_warnings: ['warn'],
    tool_calls: [{ name: 'bash', arguments: { cmd: 'ls' } }],
    blocks: [
      { type: 'tool_call', streaming: false, arguments: { path: '/tmp' }, subagent_steps: [{ tool_name: 'read' }] },
      { type: 'tool_call', streaming: true, arguments: { path: '/wip' } },
    ],
  }

  freezeReadonlyMessageParts(msg)

  assert.equal(Object.isFrozen(msg), false)
  assert.equal(Object.isFrozen(msg.knowledge_evidence), true)
  assert.equal(Object.isFrozen(msg.knowledge_evidence[0]), true)
  assert.equal(Object.isFrozen(msg.knowledge_warnings), true)
  assert.equal(Object.isFrozen(msg.tool_calls[0].arguments), true)
  assert.equal(Object.isFrozen(msg.blocks[0].arguments), true)
  assert.equal(Object.isFrozen(msg.blocks[0].subagent_steps), true)
  // Streaming block arguments stay mutable
  assert.equal(Object.isFrozen(msg.blocks[1].arguments), false)

  // Shell fields can still be cleared (e.g., expired approval)
  msg.pending_approval = null
  assert.equal(msg.pending_approval, null)
})

test('freezeHistoryMessages skips loading messages', () => {
  const list = [
    { id: '1', loading: false, knowledge_evidence: [{ id: 'e1' }] },
    { id: '2', loading: true, knowledge_evidence: [{ id: 'e2' }] },
  ]
  freezeHistoryMessages(list)
  assert.equal(Object.isFrozen(list[0].knowledge_evidence), true)
  assert.equal(Object.isFrozen(list[1].knowledge_evidence), false)
})

test('lastMessageRevisionKey changes when tail message content or block state changes', () => {
  assert.equal(lastMessageRevisionKey([]), '0')
  const msgs = [
    { id: 'u1', content: 'hi', blocks: [] },
    { id: 'a1', loading: true, content: '', thinking: '', blocks: [{ type: 'content', content: 'abc', streaming: false }] },
  ]
  const k1 = lastMessageRevisionKey(msgs)
  msgs[1].blocks[0].content = 'abcdef'
  const k2 = lastMessageRevisionKey(msgs)
  assert.notEqual(k1, k2)

  msgs[1].loading = false
  const k3 = lastMessageRevisionKey(msgs)
  assert.notEqual(k2, k3)
})
