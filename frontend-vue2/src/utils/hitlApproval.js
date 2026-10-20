/**
 * HITL 审批的纯函数（DA-02）：approval_required 载荷 → 工具卡片标记 / 独立审批面板 / 恢复决策。
 *
 * 后端给每条待审批操作带 interrupt_id + action_index：并行子代理各自触发审批时，图里会同时
 * 挂着多个中断。前端按中断作答——点哪张卡片（或面板里哪一项）的按钮，就只回答它所属的中断，
 * 其余中断保持挂起，恢复流结束后后端会再次下发 approval_required。
 * 子代理内部的操作在主消息里没有对应的工具卡片，渲染在消息级的独立审批面板里。
 * 旧载荷（没有 interrupt_id，改造前落库的待审批记录）沿用「一次决策作用于整批」的旧行为。
 */

function isToolBlock (b) {
  return !!b && b.type === 'tool_call'
}

function pendingActions (message) {
  const pending = message && message.pending_approval
  return pending && Array.isArray(pending.action_requests) ? pending.action_requests.filter(Boolean) : []
}

export function hasInterruptIds (actionRequests) {
  return (actionRequests || []).some(ar => ar && ar.interrupt_id)
}

/**
 * 为一条待审批操作找到对应的工具卡片下标（找不到返回 -1）。
 * 新载荷只认 tool_call_id；对不上的是子代理内部操作，交给独立面板。
 * 旧载荷兜底按工具名找「尚未执行完」的最后一张卡片，避免标到早已执行过的同名调用上。
 */
export function findApprovalBlockIndex (blocks, ar, taken = new Set()) {
  const list = blocks || []
  if (!ar) return -1
  if (ar.tool_call_id) {
    const idx = list.findIndex((b, i) => !taken.has(i) && isToolBlock(b) &&
      (b.tool_call_id === ar.tool_call_id || b.id === ar.tool_call_id))
    if (idx !== -1) return idx
  }
  if (ar.interrupt_id || !ar.tool_name) return -1
  for (let i = list.length - 1; i >= 0; i--) {
    const b = list[i]
    if (!taken.has(i) && isToolBlock(b) && b.tool_name === ar.tool_name && b.duration == null) return i
  }
  return -1
}

/**
 * 把 approval_required 的操作挂到工具卡片上（原地修改 blocks），返回挂不上的操作。
 */
export function applyApprovalRequests (blocks, actionRequests) {
  const unmatched = []
  const taken = new Set()
  for (const ar of actionRequests || []) {
    const idx = findApprovalBlockIndex(blocks, ar, taken)
    if (idx === -1) {
      unmatched.push(ar)
      continue
    }
    taken.add(idx)
    const blk = blocks[idx]
    blk.pending_approval = true
    blk.approval_status = 'pending'
    if (ar.tool_call_id) {
      blk.tool_call_id = ar.tool_call_id
      blk.id = ar.tool_call_id
    }
    if (ar.file_paths && ar.file_paths.length > 0) blk.file_paths = ar.file_paths
    if (ar.interrupt_id) blk.hitl_interrupt_id = ar.interrupt_id
  }
  return unmatched
}

/**
 * 消息仍待审批、但没有挂在任何待审批工具卡片上的操作（子代理内部的审批），供独立面板渲染。
 * 保证每条待审批操作都有可点的按钮。
 */
export function unmatchedApprovalRequests (message) {
  const actions = pendingActions(message)
  if (!actions.length) return []
  const blocks = (message && message.blocks) || []
  const taken = new Set()
  return actions.filter(ar => {
    const idx = findApprovalBlockIndex(blocks, ar, taken)
    if (idx === -1) return true
    const blk = blocks[idx]
    if (blk.pending_approval || blk.approval_status === 'pending') {
      taken.add(idx)
      return false
    }
    return true
  })
}

/** 工具卡片上的按钮回答哪个中断：卡片自身记录的，否则按 tool_call_id 到载荷里查。 */
export function approvalTargetForBlock (message, block) {
  if (block && block.hitl_interrupt_id) return { interruptId: block.hitl_interrupt_id }
  const id = block && (block.tool_call_id || block.id)
  const ar = id ? pendingActions(message).find(a => a.tool_call_id === id) : null
  return { interruptId: (ar && ar.interrupt_id) || null }
}

/**
 * 构造 /agent/chat/resume 的 decisions。
 * 新载荷：只回答 interruptId（缺省为第一个中断）的那一批操作，每条带 interrupt_id / action_index。
 * 旧载荷：单个决策（后端把它作用于整批），与改造前一致。
 */
export function buildResumeDecisions (actionRequests, decision, opts = {}) {
  // 拒绝说明由后端按工具生成（DA-15）；调用方明确给了说明才带上
  const base = decision === 'approve'
    ? { type: 'approve' }
    : (opts.rejectMessage ? { type: 'reject', message: opts.rejectMessage } : { type: 'reject' })
  const actions = (actionRequests || []).filter(Boolean)
  if (!hasInterruptIds(actions)) return { decisions: [base], interruptId: null }
  const interruptId = opts.interruptId || actions.find(ar => ar.interrupt_id).interrupt_id
  const batch = actions
    .filter(ar => ar.interrupt_id === interruptId)
    .sort((a, b) => (a.action_index || 0) - (b.action_index || 0))
  const decisions = batch.length
    ? batch.map(ar => ({ ...base, interrupt_id: interruptId, action_index: ar.action_index || 0 }))
    // 中断已不在当前载荷里：仍按 ID 发送，由后端判定（过期则 400），绝不改投别的中断
    : [{ ...base, interrupt_id: interruptId }]
  return { decisions, interruptId }
}

/**
 * 用户作答后更新卡片：清掉所有「待审批」按钮（恢复流期间不允许再点），
 * 只给本次作答的中断记上「已批准 / 已拒绝」，其余仍为 pending，等后端再次下发审批。
 */
export function markApprovalDecision (blocks, decision, actionRequests, interruptId) {
  const status = decision === 'approve' ? 'approved' : 'rejected'
  const targetIds = interruptId
    ? new Set((actionRequests || [])
      .filter(ar => ar && ar.interrupt_id === interruptId && ar.tool_call_id)
      .map(ar => ar.tool_call_id))
    : null
  for (const blk of blocks || []) {
    if (!blk || !blk.pending_approval) continue
    blk.pending_approval = false
    if (!targetIds || targetIds.has(blk.tool_call_id) || blk.hitl_interrupt_id === interruptId) {
      blk.approval_status = status
    }
  }
}
