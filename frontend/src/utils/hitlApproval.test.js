import assert from 'node:assert/strict'
import test from 'node:test'

import {
  applyApprovalRequests,
  approvalTargetForBlock,
  buildResumeDecisions,
  derivePendingApproval,
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

// ── 会话列表「待审批」徽标的判据（DA-24，2026-11-07）────────────────────────────
// 回归背景：刷新页面后点审批，左侧会话的「待审批」一直不消失，过一阵再刷新才复原。
// 旧判据会把「块状态还停在 pending、但按钮已收起」的中间态算成待审批，形成死状态。

const aiMessage = (extra = {}) => ({ role: 'assistant', content: '好的', blocks: [], ...extra })
const toolBlock = (id, status, extra = {}) => ({ type: 'tool_call', tool_call_id: id, tool_name: 'save_expert', approval_status: status, ...extra })

test('待审批判据：消息级载荷 + 待审批卡片 → 待审批', () => {
  const msgs = [aiMessage({
    pending_approval: { thread_id: 's-1', action_requests: [{ tool_call_id: 'tc1', interrupt_id: 'i1' }] },
    blocks: [toolBlock('tc1', 'pending', { pending_approval: true })],
  })]
  assert.equal(derivePendingApproval(msgs), true)
})

test('待审批判据：答完后载荷清空、块状态残留 pending → 不再待审批（本次问题）', () => {
  const msgs = [aiMessage({ pending_approval: null, blocks: [toolBlock('tc1', 'pending')] })]
  assert.equal(derivePendingApproval(msgs), false)
})

test('待审批判据：块已批准但载荷残留 → 不待审批', () => {
  const msgs = [aiMessage({
    pending_approval: { thread_id: 's-1', action_requests: [{ tool_call_id: 'tc1', interrupt_id: 'i1' }] },
    blocks: [toolBlock('tc1', 'approved')],
  })]
  assert.equal(derivePendingApproval(msgs), false)
})

test('待审批判据：只看最后一条 assistant，旧残留不影响', () => {
  const msgs = [
    aiMessage({ pending_approval: { thread_id: 's-1' }, blocks: [toolBlock('tc1', 'pending')] }),
    { role: 'user', content: '继续' },
    aiMessage({ pending_approval: null, blocks: [toolBlock('tc2', 'approved')] }),
  ]
  assert.equal(derivePendingApproval(msgs), false)
})

test('待审批判据：末尾出现用户消息（已发起新一轮）→ 不待审批', () => {
  const msgs = [
    aiMessage({ pending_approval: { thread_id: 's-1' }, blocks: [toolBlock('tc1', 'pending')] }),
    { role: 'user', content: '再问一句' },
  ]
  assert.equal(derivePendingApproval(msgs), false)
})

test('待审批判据：子代理内部审批（主消息无对应卡片）→ 仍待审批', () => {
  const msgs = [aiMessage({
    pending_approval: { thread_id: 's-1', action_requests: [{ tool_call_id: 'sub-1', interrupt_id: 'i1' }] },
    blocks: [],
  })]
  assert.equal(derivePendingApproval(msgs), true)
})

test('待审批判据：旧数据无消息级载荷 → 按块状态兜底', () => {
  assert.equal(derivePendingApproval([aiMessage({ blocks: [toolBlock('tc1', 'pending')] })]), true)
  assert.equal(derivePendingApproval([aiMessage({ blocks: [toolBlock('tc1', 'rejected')] })]), false)
})

test('待审批判据：数组形态载荷 / 空列表', () => {
  assert.equal(derivePendingApproval([aiMessage({ pending_approval: [{ thread_id: 's-1' }] })]), true)
  assert.equal(derivePendingApproval([]), false)
  assert.equal(derivePendingApproval(null), false)
  assert.equal(derivePendingApproval(undefined), false)
})
