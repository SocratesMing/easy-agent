import assert from 'node:assert/strict'
import test from 'node:test'

import {
  applyApprovalRequests,
  approvalTargetForBlock,
  buildResumeDecisions,
  buildApprovalResumePlan,
  derivePendingApproval,
  approvalActionSummary,
  batchedApprovalGroups,
  isBatchedApprovalBlock,
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

test('ask_user respond is treated as completed rather than rejected', () => {
  const blocks = [
    {
      type: 'tool_call',
      tool_call_id: 'ask-1',
      pending_approval: true,
      approval_status: 'pending',
      hitl_interrupt_id: 'I',
    },
  ]
  markApprovalDecision(
    blocks,
    'respond',
    [{ tool_call_id: 'ask-1', interrupt_id: 'I', action_index: 0 }],
    'I',
  )
  assert.equal(blocks[0].pending_approval, false)
  assert.equal(blocks[0].approval_status, 'approved')
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

// ── 批次审批（DA-28）：一个中断挂多项操作时，只能整批作答，展示上必须整批给出 ──────
// 现实依据：日志 `2 个操作待审批` + 一次 resume 带 2 条 decisions —— 框架把同一条 AIMessage
// 里命中审批的多个工具调用打包成一个中断，前端点一张卡片就把整批答完了。

const batchedMessage = {
  role: 'assistant',
  pending_approval: {
    thread_id: 's-1',
    action_requests: [
      { tool_name: 'delete', name: 'delete', interrupt_id: 'I-1', action_index: 1, tool_call_id: 'c2', arguments: { file_path: '/w/notes_5.txt' }, file_paths: ['/w/notes_5.txt'] },
      { tool_name: 'delete', name: 'delete', interrupt_id: 'I-1', action_index: 0, tool_call_id: 'c1', arguments: { file_path: '/w/notes_4.txt' }, file_paths: ['/w/notes_4.txt'] },
      { tool_name: 'delete', name: 'delete', interrupt_id: 'I-2', action_index: 0, tool_call_id: 'c3', arguments: { file_path: '/w/other.txt' } },
    ],
  },
  blocks: [
    { type: 'tool_call', tool_name: 'delete', tool_call_id: 'c1', hitl_interrupt_id: 'I-1', pending_approval: true, approval_status: 'pending' },
    { type: 'tool_call', tool_name: 'delete', tool_call_id: 'c2', hitl_interrupt_id: 'I-1', pending_approval: true, approval_status: 'pending' },
    { type: 'tool_call', tool_name: 'delete', tool_call_id: 'c3', hitl_interrupt_id: 'I-2', pending_approval: true, approval_status: 'pending' },
  ],
}

test('批次分组：同一中断的多项操作合成一组，按 action_index 排序', () => {
  const groups = batchedApprovalGroups(batchedMessage)
  assert.equal(groups.length, 1)
  assert.equal(groups[0].interruptId, 'I-1')
  assert.equal(groups[0].count, 2)
  assert.deepEqual(groups[0].actions.map(a => a.tool_call_id), ['c1', 'c2'])
})

test('批次分组：单项中断不成批（每块各自回答自己的中断）', () => {
  const single = { role: 'assistant', pending_approval: { thread_id: 's-1', action_requests: [
    { tool_name: 'delete', interrupt_id: 'I-1', action_index: 0, tool_call_id: 'c1' },
    { tool_name: 'delete', interrupt_id: 'I-2', action_index: 0, tool_call_id: 'c2' },
  ] } }
  assert.deepEqual(batchedApprovalGroups(single), [])
})

test('批次分组：旧载荷（无 interrupt_id）与交互动作不进批次', () => {
  const legacy = { role: 'assistant', pending_approval: { thread_id: 's-1', action_requests: [
    { tool_name: 'delete', tool_call_id: 'c1' },
    { tool_name: 'delete', tool_call_id: 'c2' },
    { tool_name: 'ask_user', interrupt_id: 'I-9', action_index: 0 },
  ] } }
  assert.deepEqual(batchedApprovalGroups(legacy), [])
  assert.deepEqual(batchedApprovalGroups(null), [])
})

test('整批作答：点一次批次面板 = 该中断全部操作各一条 approve', () => {
  const { decisions } = buildResumeDecisions(batchedMessage.pending_approval.action_requests, 'approve', { interruptId: 'I-1' })
  assert.equal(decisions.length, 2)
  assert.deepEqual(decisions.map(d => [d.interrupt_id, d.action_index, d.type]), [
    ['I-1', 0, 'approve'],
    ['I-1', 1, 'approve'],
  ])
})

test('操作清单摘要素材：路径 / 命令 / 参数 JSON / 超长截断', () => {
  assert.deepEqual(approvalActionSummary({ tool_name: 'delete', arguments: { file_path: '/w/a.txt' } }), { name: 'delete', detail: '/w/a.txt' })
  assert.deepEqual(approvalActionSummary({ tool_name: 'execute', arguments: { command: 'ls -la' } }), { name: 'execute', detail: 'ls -la' })
  assert.deepEqual(approvalActionSummary({ name: 'write_file', arguments: { path: '/w/b.md' } }), { name: 'write_file', detail: '/w/b.md' })
  assert.equal(approvalActionSummary({ tool_name: 'mcp_write', arguments: { id: 7 } }).detail, '{"id":7}')
  assert.equal(approvalActionSummary({ tool_name: 'delete', file_paths: ['/w/c.txt'] }).detail, '/w/c.txt')
  assert.equal(approvalActionSummary({ tool_name: 'x', arguments: { s: 'a'.repeat(200) } }).detail.length, 140)
})

test('批次成员判据：刷新后只靠 tool_call_id 也能认出批次成员（隐藏卡片按钮）', () => {
  // 服务端落库的块没有 hitl_interrupt_id，只有 tool_call_id
  const refreshed = { ...batchedMessage, blocks: [
    { type: 'tool_call', tool_name: 'delete', tool_call_id: 'c1', approval_status: 'pending' },
    { type: 'tool_call', tool_name: 'delete', tool_call_id: 'c2', approval_status: 'pending' },
    { type: 'tool_call', tool_name: 'delete', tool_call_id: 'c3', approval_status: 'pending' },
  ] }
  const groups = batchedApprovalGroups(refreshed)
  assert.equal(isBatchedApprovalBlock(refreshed, refreshed.blocks[0], groups), true)
  assert.equal(isBatchedApprovalBlock(refreshed, refreshed.blocks[1], groups), true)
  // 另一个中断只有 1 项 → 不是批次成员，按钮留在卡片上
  assert.equal(isBatchedApprovalBlock(refreshed, refreshed.blocks[2], groups), false)
  // 没有待审批批次时一律 false（单项 / 旧载荷都不隐藏按钮）
  assert.equal(isBatchedApprovalBlock(batchedMessage, null, groups), false)
  assert.equal(isBatchedApprovalBlock({ role: 'assistant' }, { tool_call_id: 'c1' }), false)
})
