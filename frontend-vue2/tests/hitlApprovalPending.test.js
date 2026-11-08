/**
 * 会话列表「待审批」徽标的判据（DA-24，2026-11-07）。
 *
 * 回归背景：刷新页面后点审批，左侧会话的「待审批」一直不消失，过一阵再刷新才复原。
 * 旧判据 = 「任意一条消息有消息级 pending_approval，或任意一个工具块 approval_status==='pending'」，
 * 而卡片按钮看的是块的 pending_approval。答完一个中断后其余块停在 pending、按钮已收起，
 * 于是出现「有徽标、没按钮」的死状态，只能靠整页刷新重建历史消掉。
 */
import assert from 'node:assert/strict'
import test from 'node:test'

import { derivePendingApproval } from '../src/packages/easyAgent/utils/hitlApproval.js'

const aiMessage = (extra = {}) => ({ role: 'assistant', content: '好的', blocks: [], ...extra })
const toolBlock = (id, status, extra = {}) => ({ type: 'tool_call', tool_call_id: id, tool_name: 'save_expert', approval_status: status, ...extra })

test('消息级载荷 + 待审批卡片 → 待审批', () => {
  const msgs = [aiMessage({
    pending_approval: { thread_id: 's-1', action_requests: [{ tool_call_id: 'tc1', interrupt_id: 'i1' }] },
    blocks: [toolBlock('tc1', 'pending', { pending_approval: true })],
  })]
  assert.equal(derivePendingApproval(msgs), true)
})

test('答完后消息级载荷清空、块状态残留 pending → 不再待审批（回归本次问题）', () => {
  const msgs = [aiMessage({ pending_approval: null, blocks: [toolBlock('tc1', 'pending')] })]
  assert.equal(derivePendingApproval(msgs), false)
})

test('块已批准但消息级载荷残留 → 不待审批', () => {
  const msgs = [aiMessage({
    pending_approval: { thread_id: 's-1', action_requests: [{ tool_call_id: 'tc1', interrupt_id: 'i1' }] },
    blocks: [toolBlock('tc1', 'approved')],
  })]
  assert.equal(derivePendingApproval(msgs), false)
})

test('历史里有旧的残留 pending 消息，最后一条已完成 → 不待审批（只看最后一条 assistant）', () => {
  const msgs = [
    aiMessage({ pending_approval: { thread_id: 's-1' }, blocks: [toolBlock('tc1', 'pending')] }),
    { role: 'user', content: '继续' },
    aiMessage({ pending_approval: null, blocks: [toolBlock('tc2', 'approved')] }),
  ]
  assert.equal(derivePendingApproval(msgs), false)
})

test('末尾出现用户消息（已发起新一轮）→ 不待审批', () => {
  const msgs = [
    aiMessage({ pending_approval: { thread_id: 's-1' }, blocks: [toolBlock('tc1', 'pending')] }),
    { role: 'user', content: '再问一句' },
  ]
  assert.equal(derivePendingApproval(msgs), false)
})

test('子代理内部审批（主消息无对应卡片）→ 仍待审批', () => {
  const msgs = [aiMessage({
    pending_approval: { thread_id: 's-1', action_requests: [{ tool_call_id: 'sub-1', interrupt_id: 'i1' }] },
    blocks: [],
  })]
  assert.equal(derivePendingApproval(msgs), true)
})

test('旧数据没有消息级载荷，只有块状态 → 按块状态兜底', () => {
  assert.equal(derivePendingApproval([aiMessage({ blocks: [toolBlock('tc1', 'pending')] })]), true)
  assert.equal(derivePendingApproval([aiMessage({ blocks: [toolBlock('tc1', 'rejected')] })]), false)
})

test('历史接口给数组形态的待审批载荷 → 待审批', () => {
  assert.equal(derivePendingApproval([aiMessage({ pending_approval: [{ thread_id: 's-1' }] })]), true)
})

test('空列表 / 空值 → 不待审批', () => {
  assert.equal(derivePendingApproval([]), false)
  assert.equal(derivePendingApproval(null), false)
  assert.equal(derivePendingApproval(undefined), false)
})
