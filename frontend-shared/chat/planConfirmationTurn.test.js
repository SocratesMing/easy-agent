import test from 'node:test'
import assert from 'node:assert/strict'

import { buildPlanContinuationTurn } from './planConfirmationTurn.js'
import { createStreamChunkHandler } from './streamChunkHandler.js'

test('splits pending assistant into card, user confirmation, and continuation', () => {
  const messages = [{
    id: 'a1',
    role: 'assistant',
    loading: true,
    pending_approval: { thread_id: 's1-m1' },
    blocks: [{ type: 'content', content: '先确认' }],
    expert: { id: 'e1', name: '专家' },
  }]
  const turn = buildPlanContinuationTurn(messages, 0, {
    id: 'confirm-1',
    text: '1. 项目 A：快速版本',
  })

  assert.equal(turn.completed.loading, false)
  assert.equal(turn.completed.pending_approval, null)
  assert.deepEqual(turn.confirmationMessage, {
    id: 'confirm-1',
    role: 'user',
    content: '1. 项目 A：快速版本',
    created_at: turn.confirmationMessage.created_at,
    message_kind: 'plan_confirmation',
    display_only: true,
  })
  assert.equal(turn.continuation.role, 'assistant')
  assert.equal(turn.continuation.loading, true)
  assert.deepEqual(turn.replacement.map(item => item.role), [
    'assistant',
    'user',
    'assistant',
  ])
  assert.equal(turn.continuation.expert.id, 'e1')
})

test('attachMode clears stale pending_approval when resumed start arrives after replayed approval_required', () => {
  const syncedFlags = []
  const vm = {
    messages: [{
      id: 'attach-1',
      role: 'assistant',
      content: '',
      thinking: '',
      tool_calls: [],
      blocks: [{ type: 'tool_call', tool_call_id: 'tc-1', pending_approval: true }],
      pending_approval: null,
      loading: true,
    }],
    pendingApproval: null,
    sessionUsage: { input_tokens: 0, output_tokens: 0, context_tokens: 0 },
    sessionDuration: 0,
    iterationCount: 0,
    currentSessionId: 's1',
    attachedStreamingSessions: {},
    syncPendingApprovalFlag: (sid, flag) => syncedFlags.push([sid, flag]),
    markStreaming: () => {},
  }
  const { onChunk } = createStreamChunkHandler(vm, {
    isResume: true,
    attachMode: true,
    assistantMsgId: 'attach-1',
    streamSessionId: 's1',
    onApprovalRequired: data => {
      vm.pendingApproval = { threadId: data.thread_id, assistantMsgId: 'attach-1' }
      vm.messages[0].pending_approval = { thread_id: data.thread_id, action_requests: data.action_requests }
    },
  }, {
    setReactive: (arr, idx, val) => { arr[idx] = val },
    parseMCPResult: v => v,
  })

  onChunk({ type: 'approval_required', thread_id: 's1-m1', action_requests: [{ tool_name: 'ask_user' }] })
  assert.ok(vm.messages[0].pending_approval)
  assert.ok(vm.pendingApproval)

  onChunk({ type: 'start', session_id: 's1' })
  assert.equal(vm.messages[0].pending_approval, null)
  assert.equal(vm.messages[0].blocks[0].pending_approval, false)
  assert.equal(vm.pendingApproval, null)
  assert.deepEqual(syncedFlags, [['s1', false]])
})
