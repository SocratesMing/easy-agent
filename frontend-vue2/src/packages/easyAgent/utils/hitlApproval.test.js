import assert from 'node:assert/strict'
import test from 'node:test'

import {
  applyApprovalRequests,
  approvalTargetForBlock,
  buildApprovalResumePlan,
  buildResumeDecisions,
  isNewApprovalEvent,
  markApprovalDecision,
  unmatchedApprovalRequests,
} from './hitlApproval.js'

// 两个并行子代理各自触发审批：主消息里只有 task 卡片，没有 delete 卡片
const subagentActions = [
  { tool_call_id: '', tool_name: 'delete', arguments: { file_path: '/a.txt' }, interrupt_id: 'A', action_index: 0 },
  { tool_call_id: '', tool_name: 'delete', arguments: { file_path: '/b.txt' }, interrupt_id: 'B', action_index: 0 },
]
const taskBlocks = () => [
  { type: 'tool_call', tool_name: 'task', tool_call_id: 't1', duration: null },
  { type: 'tool_call', tool_name: 'task', tool_call_id: 't2', duration: null },
]

test('subagent approvals never get pinned on an unrelated card', () => {
  const blocks = taskBlocks()
  const unmatched = applyApprovalRequests(blocks, subagentActions)
  assert.deepEqual(unmatched, subagentActions)
  assert.ok(blocks.every(b => !b.pending_approval))
})

test('unmatched approvals go to the message-level panel so every action has buttons', () => {
  const message = { blocks: taskBlocks(), pending_approval: { thread_id: 's-m', action_requests: subagentActions } }
  assert.deepEqual(unmatchedApprovalRequests(message).map(a => a.interrupt_id), ['A', 'B'])
})

test('main-agent approvals attach to their own card by tool_call_id', () => {
  const blocks = [
    { type: 'tool_call', tool_name: 'execute', tool_call_id: 'c-ls', duration: 0.1, result: 'ok' },
    { type: 'tool_call', tool_name: 'execute', tool_call_id: 'c-rm', duration: null },
  ]
  const actions = [{ tool_call_id: 'c-rm', tool_name: 'execute', interrupt_id: 'A', action_index: 0, file_paths: ['a.txt'] }]
  assert.deepEqual(applyApprovalRequests(blocks, actions), [])
  assert.equal(blocks[1].pending_approval, true)
  assert.equal(blocks[1].hitl_interrupt_id, 'A')
  assert.deepEqual(blocks[1].file_paths, ['a.txt'])
  assert.equal(blocks[0].pending_approval, undefined)
  const message = { blocks, pending_approval: { action_requests: actions } }
  assert.deepEqual(unmatchedApprovalRequests(message), [])
  assert.deepEqual(approvalTargetForBlock(message, blocks[1]), { interruptId: 'A', actionIndex: 0, actionName: 'execute' })
})

test('delete approvals carry allowed_decisions so the card hides 编辑参数', () => {
  const blocks = [{ type: 'tool_call', tool_name: 'delete', tool_call_id: 'c-rm', duration: null }]
  const actions = [{
    tool_call_id: 'c-rm', tool_name: 'delete', interrupt_id: 'A', action_index: 0,
    allowed_decisions: ['approve', 'reject'],
  }]
  applyApprovalRequests(blocks, actions)
  assert.deepEqual(blocks[0].allowed_decisions, ['approve', 'reject'])
})

test('payloads without allowed_decisions keep the legacy all-decisions behaviour', () => {
  const blocks = [{ type: 'tool_call', tool_name: 'delete', tool_call_id: 'c-rm', duration: null }]
  applyApprovalRequests(blocks, [{ tool_call_id: 'c-rm', tool_name: 'delete', interrupt_id: 'A' }])
  assert.equal(blocks[0].allowed_decisions, undefined)
})

test('legacy payloads keep the name fallback but skip already-finished cards', () => {
  const blocks = [
    { type: 'tool_call', tool_name: 'delete', tool_call_id: 'old', duration: 0.2, result: 'deleted' },
    { type: 'tool_call', tool_name: 'delete', tool_call_id: 'new', duration: null },
  ]
  applyApprovalRequests(blocks, [{ tool_call_id: '', tool_name: 'delete' }])
  assert.equal(blocks[0].pending_approval, undefined)
  assert.equal(blocks[1].pending_approval, true)
})

test('decisions answer only the clicked interrupt', () => {
  const { decisions, interruptId } = buildResumeDecisions(subagentActions, 'approve', { interruptId: 'B' })
  assert.equal(interruptId, 'B')
  assert.deepEqual(decisions, [{ type: 'approve', interrupt_id: 'B', action_index: 0 }])
})

test('reject without a message leaves the wording to the backend', () => {
  const { decisions } = buildResumeDecisions(subagentActions, 'reject', { interruptId: 'B' })
  assert.deepEqual(decisions, [{ type: 'reject', interrupt_id: 'B', action_index: 0 }])
})

test('a batch inside one interrupt gets one decision per action in order', () => {
  const actions = [
    { tool_call_id: 'c2', interrupt_id: 'A', action_index: 1 },
    { tool_call_id: 'c1', interrupt_id: 'A', action_index: 0 },
    { tool_call_id: 'x', interrupt_id: 'B', action_index: 0 },
  ]
  const { decisions } = buildResumeDecisions(actions, 'reject', { interruptId: 'A', rejectMessage: '不要' })
  assert.deepEqual(decisions, [
    { type: 'reject', message: '不要', interrupt_id: 'A', action_index: 0 },
    { type: 'reject', message: '不要', interrupt_id: 'A', action_index: 1 },
  ])
})

test('without a target the first interrupt is answered, legacy payloads send one decision', () => {
  assert.deepEqual(buildResumeDecisions(subagentActions, 'approve').decisions, [
    { type: 'approve', interrupt_id: 'A', action_index: 0 },
  ])
  assert.deepEqual(buildResumeDecisions([{ tool_name: 'delete' }], 'approve'), {
    decisions: [{ type: 'approve' }],
    interruptId: null,
  })
})

test('a stale interrupt id is sent as-is instead of being redirected', () => {
  const { decisions } = buildResumeDecisions(subagentActions, 'approve', { interruptId: 'gone' })
  assert.deepEqual(decisions, [{ type: 'approve', interrupt_id: 'gone' }])
})

test('marking a decision clears all buttons but only stamps the answered interrupt', () => {
  const blocks = [
    { type: 'tool_call', tool_call_id: 'c1', pending_approval: true, approval_status: 'pending', hitl_interrupt_id: 'A' },
    { type: 'tool_call', tool_call_id: 'c2', pending_approval: true, approval_status: 'pending', hitl_interrupt_id: 'B' },
  ]
  const actions = [
    { tool_call_id: 'c1', interrupt_id: 'A', action_index: 0 },
    { tool_call_id: 'c2', interrupt_id: 'B', action_index: 0 },
  ]
  markApprovalDecision(blocks, 'approve', actions, 'A')
  assert.deepEqual(blocks.map(b => [b.pending_approval, b.approval_status]), [
    [false, 'approved'],
    [false, 'pending'],
  ])
})

test('plan interaction answers become respond decisions without executing batched tools', () => {
  const { decisions } = buildApprovalResumePlan(
    [
      { tool_name: 'ask_user', interrupt_id: 'I', action_index: 0 },
      { tool_name: 'delete', interrupt_id: 'I', action_index: 1 },
    ],
    'respond',
    {
      interruptId: 'I',
      response: { answers: { project: 'fast' }, note: '先做核心' },
    },
  )
  assert.equal(decisions[0].type, 'respond')
  assert.equal(decisions[1].type, 'reject')
  assert.deepEqual(JSON.parse(decisions[0].message), {
    answers: { project: 'fast' },
    note: '先做核心',
  })
})

test('buildResumeDecisions: edit 只改目标那条，其余按批准', () => {
  const actions = [
    { name: 'a', action_index: 0, interrupt_id: 'i1', tool_call_id: 't0' },
    { name: 'b', action_index: 1, interrupt_id: 'i1', tool_call_id: 't1' }
  ]
  const { decisions } = buildResumeDecisions(actions, 'edit', {
    interruptId: 'i1',
    targetActionIndex: 1,
    editedAction: { name: 'b', args: { x: 2 } }
  })
  assert.deepEqual(decisions[0], { type: 'approve', interrupt_id: 'i1', action_index: 0 })
  assert.deepEqual(decisions[1], {
    type: 'edit',
    edited_action: { name: 'b', args: { x: 2 } },
    interrupt_id: 'i1',
    action_index: 1
  })
})

test('buildResumeDecisions: 旧载荷（无 interrupt_id）编辑单条', () => {
  const { decisions } = buildResumeDecisions([{ name: 'a' }], 'edit', {
    editedAction: { name: 'a', args: { y: 1 } }
  })
  assert.deepEqual(decisions, [{ type: 'edit', edited_action: { name: 'a', args: { y: 1 } } }])
})

// ── isNewApprovalEvent：恢复流里的事件是新审批还是重放 ──────────────────
// 一次运行（含其全部恢复流）的 thread_id 是同一个；嵌套审批、并行子代理剩下的中断
// 都带同一个 thread_id，因此只能按中断身份判，否则新审批会被清掉、按钮变成死按钮。

test('恢复流事件判据：同 thread、同一中断重放 → 不是新审批', () => {
  const data = { thread_id: 's-1', action_requests: [
    { tool_name: 'delete', interrupt_id: 'A', action_index: 0 },
    { tool_name: 'delete', interrupt_id: 'A', action_index: 1 },
  ] }
  assert.equal(isNewApprovalEvent(data, { threadId: 's-1', interruptId: 'A' }), false)
})

test('恢复流事件判据：同 thread、出现未作答的中断 → 是新审批', () => {
  const data = { thread_id: 's-1', action_requests: [
    { tool_name: 'delete', interrupt_id: 'B', action_index: 0 },
  ] }
  assert.equal(isNewApprovalEvent(data, { threadId: 's-1', interruptId: 'A' }), true)
})

test('恢复流事件判据：新旧中断混在同一批里 → 是新审批', () => {
  const data = { thread_id: 's-1', action_requests: [
    { tool_name: 'delete', interrupt_id: 'A', action_index: 0 },
    { tool_name: 'delete', interrupt_id: 'B', action_index: 0 },
  ] }
  assert.equal(isNewApprovalEvent(data, { threadId: 's-1', interruptId: 'A' }), true)
})

test('恢复流事件判据：thread_id 变了 → 是新审批', () => {
  assert.equal(isNewApprovalEvent(
    { thread_id: 's-2', action_requests: [{ interrupt_id: 'A' }] },
    { threadId: 's-1', interruptId: 'A' },
  ), true)
})

test('恢复流事件判据：缺 thread_id 的事件不算新审批；旧载荷按中断作答与否兜底', () => {
  assert.equal(isNewApprovalEvent(null, { threadId: 's-1', interruptId: 'A' }), false)
  assert.equal(isNewApprovalEvent({ action_requests: [{ interrupt_id: 'A' }] }, { threadId: 's-1' }), false)
  // 旧载荷（无 interrupt_id）：本次没答过中断 → 视为新审批；答过 → 视为重放
  assert.equal(isNewApprovalEvent(
    { thread_id: 's-1', action_requests: [{ tool_name: 'delete' }] },
    { threadId: 's-1', interruptId: 'A' },
  ), false)
  assert.equal(isNewApprovalEvent(
    { thread_id: 's-1', action_requests: [{ tool_name: 'delete' }] },
    { threadId: 's-1' },
  ), true)
})
